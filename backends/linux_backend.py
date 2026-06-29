"""Linux (BlueZ) backend. Reads/writes directly in /var/lib/bluetooth/."""

from __future__ import annotations

import configparser, os, re, shutil, subprocess
from pathlib import Path
from typing import List, Optional

from datetime import datetime, timezone
from models import BondKey, DiscoveredDevice, LinkKeyBond
from .base import BluetoothBackend
from . import linux_classic as _classic

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
        devices = []
        idx = 1
        for adapter_mac in self._list_adapters():
            adapter_dir = self.bluetooth_dir / adapter_mac
            for device_path in adapter_dir.iterdir():
                if not (device_path.is_dir() and MAC_RE.match(device_path.name)):
                    continue
                info_path = device_path / "info"
                has_ltk = False
                device_name = None
                if info_path.exists():
                    config = configparser.ConfigParser()
                    try:
                        config.read(info_path)
                        has_ltk = "LongTermKey" in config or "LinkKey" in config
                        device_name = config.get("General", "Name", fallback=None)
                    except configparser.Error:
                        pass
                devices.append(DiscoveredDevice(
                    index=idx, adapter_mac=adapter_mac, device_mac=device_path.name,
                    device_name=device_name, has_ltk=has_ltk, raw_source_path=str(info_path)
                ))
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
            if "LinkKey" in config:
                ltk_hex = config["LinkKey"].get("Key", "00" * 16)
            else:
                ltk_hex = "00" * 16
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

    def _backup_info(self, info_path: Path) -> None:
        if not info_path.exists():
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = info_path.with_name(f"info.bak-{ts}")
        shutil.copy2(info_path, backup)
        os.chmod(backup, 0o600)

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
            self._backup_info(info_path)
            config.read(info_path)
            if "LinkKey" in config:
                is_classic = True
        else:
            config["General"] = {
                "Name": bond.device_name or "Imported Device",
                "Manufacturer": "0",
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

        if is_classic:
            # Classic Bluetooth — enforce BR/EDR tech, preserve LE if already present
            techs = config["General"].get("SupportedTechnologies", "")
            if "LE;" in techs:
                config["General"]["SupportedTechnologies"] = "BR/EDR;LE;"
            else:
                config["General"]["SupportedTechnologies"] = "BR/EDR;"
            if "LinkKey" not in config:
                config["LinkKey"] = {}
            config["LinkKey"]["Key"] = bond.ltk_hex
            if "Type" not in config["LinkKey"]:
                config["LinkKey"]["Type"] = "4"
            if "PINLength" not in config["LinkKey"]:
                config["LinkKey"]["PINLength"] = "0"
        else:
            # BLE — enforce LE tech, preserve Classic if already present
            techs = config["General"].get("SupportedTechnologies", "")
            if "BR/EDR;" in techs or "LinkKey" in config:
                config["General"]["SupportedTechnologies"] = "BR/EDR;LE;"
            else:
                config["General"]["SupportedTechnologies"] = "LE;"
            if "LongTermKey" not in config:
                config["LongTermKey"] = {}
            config["LongTermKey"]["Key"] = bond.ltk_hex
            config["LongTermKey"]["Authenticated"] = "0"
            config["LongTermKey"]["EncSize"] = str(bond.key_length)
            config["LongTermKey"]["EDiv"] = str(bond.ediv)
            config["LongTermKey"]["Rand"] = str(bond.erand)

        if bond.irk_hex:
            if "IdentityResolvingKey" not in config:
                config["IdentityResolvingKey"] = {}
            config["IdentityResolvingKey"]["Key"] = bond.irk_hex
            config["IdentityResolvingKey"]["EncSize"] = "16"

        target_dir.mkdir(parents=True, exist_ok=True)
        with open(info_path, "w") as f:
            config.write(f, space_around_delimiters=False)

        os.chmod(info_path, 0o600)
        shutil.chown(info_path, user="root", group="root")
        return info_path

    def restart_bluetooth_stack(self) -> None:
        import time; subprocess.run(["systemctl", "restart", "bluetooth"], check=True)
        for _ in range(10):
            time.sleep(1)
            r = subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, text=True)
            if "succeeded" in r.stdout.lower() or "powered: yes" in r.stdout.lower(): break

    def verify_device(self, device_mac: str) -> str:
        res = subprocess.run(["bluetoothctl", "info", device_mac], capture_output=True, text=True)
        return res.stdout or res.stderr

    def remove_bond_key(self, adapter_mac: str, device_mac: str, remove_backups: bool = False) -> None:
        target_dir = self.bluetooth_dir / adapter_mac / device_mac
        if target_dir.exists() and target_dir.is_dir():
            if not remove_backups:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                try: shutil.copytree(target_dir, target_dir.with_name(f"{device_mac}.bak-{ts}"), dirs_exist_ok=True)
                except Exception: pass
            shutil.rmtree(target_dir)
        if remove_backups and (self.bluetooth_dir / adapter_mac).exists():
            for p in (self.bluetooth_dir / adapter_mac).iterdir():
                if p.is_dir() and p.name.startswith(f"{device_mac}.bak-"):
                    shutil.rmtree(p, ignore_errors=True)

    # ── Classic (BR/EDR) methods — delegates to linux_classic.py (RC9) ─────

    def extract_classic_bond(self, device: DiscoveredDevice) -> LinkKeyBond:
        """Extract a Classic bond from the BlueZ info file."""
        return _classic.extract_classic_bond(self.bluetooth_dir, device)

    def check_active_link(self, device_mac: str) -> bool:
        """Return True if the device is currently connected (RC11)."""
        return _classic.check_active_link(device_mac)

    def write_classic_info(self, bond: LinkKeyBond, target_dir: Path) -> Path:
        """Write a complete BlueZ info file for a Classic bond (RC3)."""
        return _classic.write_classic_info(bond, target_dir)

    def set_info_permissions(self, info_path: Path) -> None:
        """Apply root ownership and 600 permissions to a BlueZ info file."""
        _classic.set_info_permissions(info_path)

    def stop_bluetooth(self) -> None:
        """Stop the Bluetooth daemon (RC9 step a)."""
        _classic.stop_bluetooth()

    def start_bluetooth(self) -> None:
        """Start the Bluetooth daemon (RC9 step e)."""
        _classic.start_bluetooth()

    def disable_discovery_if_active(self) -> bool:
        """Disable discovery scan only if currently active (RC12)."""
        return _classic.disable_discovery_if_active()
