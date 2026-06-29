"""
backends/windows_backend.py
Windows backend. Accesses registry keys via temporary scheduled tasks.
"""

from __future__ import annotations

import platform
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

from backends.windows_common import ensure_windows, parse_windows_reg_text
from models import BondKey, DiscoveredDevice
from .base import BluetoothBackend

REG_KEY_PATH = r"HKLM\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys"
WORK_DIR = Path(r"C:\Users\Public\BTDualBootSync")


class WindowsBluetoothBackend(BluetoothBackend):
    name = "windows"

    def __init__(self, work_dir: Path = WORK_DIR):
        self.work_dir = work_dir

    def _run_as_system(self, command: str, timeout_seconds: int = 20) -> None:
        """
        Executes `command` (a single cmd.exe line) as SYSTEM, via a temporary
        one-time scheduled task. Blocks until it finished or timeout_seconds is reached.
        """
        ensure_windows()
        self.work_dir.mkdir(parents=True, exist_ok=True)

        task_name = f"BTSyncTemp_{uuid.uuid4().hex[:8]}"
        bat_path = self.work_dir / f"{task_name}.bat"
        bat_path.write_text(command + "\r\n", encoding="ascii")

        subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True,
        )
        create = subprocess.run(
            [
                "schtasks", "/create", "/tn", task_name,
                "/tr", str(bat_path),
                "/sc", "once", "/st", "00:00",
                "/ru", "SYSTEM", "/f",
            ],
            capture_output=True, text=True,
        )
        if create.returncode != 0:
            raise RuntimeError(f"Could not create scheduled task: {create.stderr}")

        subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True, text=True)

        waited = 0
        while waited < timeout_seconds:
            time.sleep(1)
            waited += 1
            query = subprocess.run(
                ["schtasks", "/query", "/tn", task_name, "/fo", "list"],
                capture_output=True, text=True,
            )
            if "Running" not in query.stdout:
                break

        subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True)
        try:
            bat_path.unlink()
        except OSError:
            pass

    def check_prerequisites(self) -> List[str]:
        problems = []
        if platform.system().lower() != "windows":
            problems.append("This backend only applies on Windows; you are on another OS.")
            return problems

        import ctypes
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            is_admin = False
        if not is_admin:
            problems.append(
                "You need a PowerShell/CMD console as Administrator "
                "to be able to create the scheduled task (you do not need to be SYSTEM yourself)."
            )
        return problems

    def list_devices(self) -> List[DiscoveredDevice]:
        ensure_windows()
        dump_file = self.work_dir / "bt_keys_all.txt"
        self._run_as_system(f'reg query "{REG_KEY_PATH}" /s > "{dump_file}"')

        if not dump_file.exists():
            raise RuntimeError(
                "Registry dump was not generated. Check permissions or if the "
                "Task Scheduler is restricted on this machine."
            )

        raw = dump_file.read_text(encoding="utf-8", errors="ignore")
        blocks = re.split(r"(?=HKEY_LOCAL_MACHINE)", raw)

        devices = []
        idx = 1
        for block in blocks:
            if "LTK" not in block or "REG_BINARY" not in block:
                continue
            path_match = re.search(
                r"Keys\\([0-9a-fA-F]{12})\\([0-9a-fA-F]{12})", block
            )
            if not path_match:
                continue
            adapter_mac_raw, device_mac_raw = path_match.groups()

            devices.append(
                DiscoveredDevice(
                    index=idx,
                    adapter_mac=adapter_mac_raw,
                    device_mac=device_mac_raw,
                    device_name=None,  # Windows doesn't store the name in this branch; resolved with Get-PnpDevice if needed
                    has_ltk=True,
                    raw_source_path=f"{REG_KEY_PATH}\\{adapter_mac_raw}\\{device_mac_raw}",
                )
            )
            idx += 1
        return devices

    def extract_bond_key(self, device: DiscoveredDevice) -> BondKey:
        ensure_windows()
        export_file = self.work_dir / f"bt_export_{device.device_mac}.reg"
        self._run_as_system(
            f'reg export "{device.raw_source_path}" "{export_file}" /y'
        )

        if not export_file.exists():
            raise RuntimeError(f"Export was not generated: {export_file}")

        return parse_windows_reg_text(export_file.read_text(encoding="utf-8-sig", errors="ignore"))

    def import_bond_key(self, bond: BondKey, target_device_mac: Optional[str] = None,
                        source_device_mac: Optional[str] = None) -> Path:
        ensure_windows()
        target_mac = (target_device_mac or bond.device_mac).replace(":", "").lower()
        reg_file = self.work_dir / f"bt_import_{target_mac}.reg"

        erand_le_bytes = bond.erand.to_bytes(8, byteorder="little")
        erand_hex_bytes = ",".join(f"{b:02x}" for b in erand_le_bytes)

        reg_content = (
            "Windows Registry Editor Version 5.00\r\n\r\n"
            f"[{REG_KEY_PATH.replace('HKLM', 'HKEY_LOCAL_MACHINE')}\\"
            f"{bond.adapter_mac_nodelim}\\{target_mac}]\r\n"
            f'"LTK"=hex:{",".join(bond.ltk_hex[i:i+2] for i in range(0, len(bond.ltk_hex), 2))}\r\n'
            f'"KeyLength"=dword:{bond.key_length:08x}\r\n'
            f'"ERand"=hex(b):{erand_hex_bytes}\r\n'
            f'"EDIV"=dword:{bond.ediv:08x}\r\n'
        )
        if bond.auth_req is not None:
            reg_content += f'"AuthReq"=dword:{bond.auth_req:08x}\r\n'
        if bond.irk_hex:
            irk_formatted = ",".join(bond.irk_hex[i:i+2].lower() for i in range(0, len(bond.irk_hex), 2))
            reg_content += f'"IRK"=hex:{irk_formatted}\r\n'
        if bond.address_type is not None:
            reg_content += f'"AddressType"=dword:{bond.address_type:08x}\r\n'


        self.work_dir.mkdir(parents=True, exist_ok=True)
        reg_file.write_text(reg_content, encoding="utf-8")

        self._run_as_system(f'reg import "{reg_file}"')

        return reg_file

    def restart_bluetooth_stack(self) -> None:
        ensure_windows()
        self._run_as_system("net stop bthserv && net start bthserv", timeout_seconds=30)

    def remove_bond_key(self, adapter_mac: str, device_mac: str, remove_backups: bool = False) -> None:
        ensure_windows()
        adapter_clean = adapter_mac.replace(":", "").lower()
        device_clean = device_mac.replace(":", "").lower()
        full_key_path = f"{REG_KEY_PATH}\\{adapter_clean}\\{device_clean}"
        self._run_as_system(f'reg delete "{full_key_path}" /f')


