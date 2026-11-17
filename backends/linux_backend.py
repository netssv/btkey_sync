"""
backends/linux_backend.py
Linux (BlueZ) backend. Reads/writes directly in /var/lib/bluetooth/.
Respects 200-line limit (AGENTS.md R14).
"""

from __future__ import annotations

import configparser, os, re, shutil, subprocess
from pathlib import Path
from typing import List, Optional

from models import BondKey, DiscoveredDevice, LinkKeyBond
from .base import BluetoothBackend
from . import linux_classic as _classic
from .linux_common import (
    set_info_permissions, backup_info_file, remove_bond_directory,
    restart_bluetooth_daemon, stop_bluetooth_daemon, start_bluetooth_daemon,
    check_active_link, disable_discovery_if_active, get_section,
)

BLUETOOTH_DIR = Path("/var/lib/bluetooth")
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


class LinuxBluetoothBackend(BluetoothBackend):
    name = "linux"

    def __init__(self, bluetooth_dir: Path = BLUETOOTH_DIR):
        self.bluetooth_dir = bluetooth_dir

    def check_prerequisites(self) -> List[str]:
        problems = []
        if os.geteuid() != 0:
            problems.append("Root is required to read/write /var/lib/bluetooth. Run with sudo.")
        if not self.bluetooth_dir.exists():
            problems.append(f"{self.bluetooth_dir} does not exist. Is BlueZ installed?")
        for cmd in ("bluetoothctl", "systemctl"):
            if shutil.which(cmd) is None:
                problems.append(f"'{cmd}' not found in PATH.")
        return problems

    def _list_adapters(self) -> List[str]:
        if not self.bluetooth_dir.exists():
            return []
        return [p.name for p in self.bluetooth_dir.iterdir() if p.is_dir() and MAC_RE.match(p.name)]

    def list_devices(self) -> List[DiscoveredDevice]:
        devices, idx = [], 1
        for adapter_mac in self._list_adapters():
            adapter_dir = self.bluetooth_dir / adapter_mac
            for device_path in adapter_dir.iterdir():
                if not (device_path.is_dir() and MAC_RE.match(device_path.name)):
                    continue
                info_path = device_path / "info"
                has_ltk, device_name = False, None
                if info_path.exists():
                    config = configparser.ConfigParser()
                    try:
                        config.read(info_path)
                        has_ltk = "LongTermKey" in config or "LinkKey" in config
                        device_name = config.get("General", "Name", fallback=None)
                    except configparser.Error:
                        pass
                devices.append(
                    DiscoveredDevice(
                        index=idx, adapter_mac=adapter_mac, device_mac=device_path.name,
                        device_name=device_name, has_ltk=has_ltk, raw_source_path=str(info_path),
                    )
                )
                idx += 1
        return devices

    def extract_bond_key(self, device: DiscoveredDevice) -> BondKey:
        info_path = Path(device.raw_source_path)
        if not info_path.exists():
            raise FileNotFoundError(f"info file not found: {info_path}")

        config = configparser.ConfigParser()
        config.optionxform = str
        config.read(info_path)

        if "LongTermKey" not in config:
            ltk_hex = config["LinkKey"].get("Key", "00" * 16) if "LinkKey" in config else "00" * 16
            ediv = erand = 0
        else:
            ltk_section = config["LongTermKey"]
            ltk_hex = ltk_section.get("Key", "00" * 16)
            ediv = int(ltk_section.get("EDiv", "0"))
            erand = int(ltk_section.get("Rand", "0"))

        irk_hex = config["IdentityResolvingKey"].get("Key") if "IdentityResolvingKey" in config else None
        return BondKey(
            adapter_mac=device.adapter_mac, device_mac=device.device_mac,
            ltk_hex=ltk_hex, ediv=ediv, erand=erand,
            auth_req=None, device_name=device.device_name,
            source_os="linux", irk_hex=irk_hex,
            is_le="LongTermKey" in config,
        )

    def import_bond_key(self, bond: BondKey, target_device_mac: Optional[str] = None,
                        source_device_mac: Optional[str] = None) -> Path:
        target_mac = target_device_mac or bond.device_mac
        target_dir = self.bluetooth_dir / bond.adapter_mac / target_mac
        info_path = target_dir / "info"

        if source_device_mac and not target_dir.exists():
            src_dir = self.bluetooth_dir / bond.adapter_mac / source_device_mac
            if src_dir.exists():
                shutil.copytree(src_dir, target_dir, dirs_exist_ok=True)

        is_classic = not bond.is_le if bond.is_le is not None else (bond.ediv == 0 and bond.erand == 0 and not bond.irk_hex)
        config = configparser.ConfigParser()
        config.optionxform = str

        if info_path.exists():
            backup_info_file(info_path)
            config.read(info_path)
            if "LinkKey" in config:
                is_classic = True
        else:
            config["General"] = {
                "Name": bond.device_name or "Imported Device", "Manufacturer": "0",
                "SupportedTechnologies": "BR/EDR;" if is_classic else "LE;",
                "Trusted": "true", "Blocked": "false", "WakeAllowed": "true",
            }

        if not is_classic:
            if bond.address_type is not None:
                config["General"]["AddressType"] = "public" if bond.address_type == 0 else "static"
            elif "AddressType" not in config["General"]:
                config["General"]["AddressType"] = "public"
        else:
            config["General"].pop("AddressType", None)

        if bond.class_of_device is not None:
            config["General"]["Class"] = f"0x{bond.class_of_device:06x}"

        techs = config["General"].get("SupportedTechnologies", "")
        if is_classic:
            config["General"]["SupportedTechnologies"] = "BR/EDR;LE;" if "LE;" in techs else "BR/EDR;"
            get_section(config, "LinkKey")["Key"] = bond.ltk_hex
            config["LinkKey"].setdefault("Type", "4")
            config["LinkKey"].setdefault("PINLength", "0")
        else:
            config["General"]["SupportedTechnologies"] = "BR/EDR;LE;" if ("BR/EDR;" in techs or "LinkKey" in config) else "LE;"
            get_section(config, "LongTermKey")["Key"] = bond.ltk_hex
            config["LongTermKey"].update({
                "Authenticated": "0", "EncSize": str(bond.key_length),
                "EDiv": str(bond.ediv), "Rand": str(bond.erand),
            })

        if bond.irk_hex:
            get_section(config, "IdentityResolvingKey")["Key"] = bond.irk_hex
            config["IdentityResolvingKey"]["EncSize"] = "16"

        target_dir.mkdir(parents=True, exist_ok=True)
        with open(info_path, "w") as f:
            config.write(f, space_around_delimiters=False)

        set_info_permissions(info_path)
        return info_path

    def restart_bluetooth_stack(self) -> None:
        restart_bluetooth_daemon()

    def verify_device(self, device_mac: str) -> str:
        res = subprocess.run(["bluetoothctl", "info", device_mac], capture_output=True, text=True)
        return res.stdout or res.stderr

    def remove_bond_key(self, adapter_mac: str, device_mac: str, remove_backups: bool = False) -> None:
        remove_bond_directory(self.bluetooth_dir, adapter_mac, device_mac, remove_backups)

    # ── Classic delegates (RC9) ──────────────────────────────────────────
    def extract_classic_bond(self, device: DiscoveredDevice) -> LinkKeyBond:
        return _classic.extract_classic_bond(self.bluetooth_dir, device)

    def check_active_link(self, device_mac: str) -> bool:
        return check_active_link(device_mac)

    def write_classic_info(self, bond: LinkKeyBond, target_dir: Path) -> Path:
        return _classic.write_classic_info(bond, target_dir)

    def set_info_permissions(self, info_path: Path) -> None:
        set_info_permissions(info_path)

    def stop_bluetooth(self) -> None:
        stop_bluetooth_daemon()

    def start_bluetooth(self) -> None:
        start_bluetooth_daemon()

    def disable_discovery_if_active(self) -> bool:
        return disable_discovery_if_active()
