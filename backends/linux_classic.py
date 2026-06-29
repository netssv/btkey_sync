"""
backends/linux_classic.py

Discrete Classic (BR/EDR) backend operations for LinuxBluetoothBackend.
Kept in a separate module to respect the 200-line limit (AGENTS.md R14).

Each method is independently callable — they must NOT be collapsed into a
single opaque 'sync' call (RC9). The CLI layer sequences them.

RC10: This module uses the same systemctl / bluetoothctl calls already
established in linux_backend.py — no second, different mechanism is introduced.
"""

from __future__ import annotations

import configparser
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from models import DiscoveredDevice, LinkKeyBond


# ── Extraction ──────────────────────────────────────────────────────────────

def extract_classic_bond(bluetooth_dir: Path,
                         device: DiscoveredDevice) -> LinkKeyBond:
    """
    Read [LinkKey], [General], and [DeviceID] from a BlueZ info file and
    return a LinkKeyBond.  Raises FileNotFoundError or ValueError on bad data.
    """
    info_path = Path(device.raw_source_path)
    if not info_path.exists():
        raise FileNotFoundError(f"info file not found: {info_path}")

    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(info_path)

    if "LinkKey" not in cfg:
        raise ValueError(f"No [LinkKey] section in {info_path}")

    lk = cfg["LinkKey"]
    link_key_hex = lk.get("Key", "")
    key_type   = int(lk.get("Type", "0"))
    pin_length = int(lk.get("PINLength", "0"))

    gen = cfg["General"] if "General" in cfg else {}
    device_class  = gen.get("Class", "")
    device_name   = gen.get("Name", device.device_name)
    raw_uuids     = gen.get("Services", "")
    service_uuids = [u for u in raw_uuids.split(";") if u.strip()]

    device_id: Optional[dict] = None
    if "DeviceID" in cfg:
        did = cfg["DeviceID"]
        device_id = {
            "source":  did.get("Source",  ""),
            "vendor":  did.get("Vendor",  ""),
            "product": did.get("Product", ""),
            "version": did.get("Version", ""),
        }

    return LinkKeyBond(
        adapter_mac=device.adapter_mac,
        device_mac=device.device_mac,
        link_key_hex=link_key_hex,
        key_type=key_type,
        pin_length=pin_length,
        device_class=device_class,
        device_name=device_name,
        device_id=device_id,
        service_uuids=service_uuids,
        source_os="linux",
    )


# ── Connection check (RC11) ──────────────────────────────────────────────────

def check_active_link(device_mac: str) -> bool:
    """
    Return True if bluetoothctl reports the device as currently connected.
    Used before overwriting bonding state (RC11).
    """
    try:
        res = subprocess.run(
            ["bluetoothctl", "info", device_mac],
            capture_output=True, text=True, timeout=8,
        )
        return "Connected: yes" in res.stdout
    except Exception:
        return False


# ── Write complete info file (RC3) ───────────────────────────────────────────

def write_classic_info(bond: LinkKeyBond, target_dir: Path) -> Path:
    """
    Write a COMPLETE BlueZ info file for a Classic bond.

    All three sections ([General], [LinkKey], [DeviceID]) are written.
    A partial file — e.g. only [LinkKey] — causes a silent
    br-connection-page-timeout, NOT an authentication error. This has been
    empirically confirmed (see CLASSIC_SUPPORT.agent.md §1, RC3).

    [DeviceID] is only written when bond.device_id is not None.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    info_path = target_dir / "info"

    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    if info_path.exists():
        cfg.read(info_path)

    if "General" not in cfg:
        cfg["General"] = {}
    
    # Set default values if not present
    cfg["General"]["Name"] = bond.device_name or cfg["General"].get("Name", "Imported Device")
    cfg["General"]["Class"] = bond.device_class or cfg["General"].get("Class", "0x000000")
    cfg["General"]["Trusted"] = "true"
    cfg["General"]["Blocked"] = "false"
    cfg["General"]["CablePairing"] = "false"
    cfg["General"]["WakeAllowed"] = "true"
    cfg["General"]["Services"] = ";".join(bond.service_uuids) or cfg["General"].get("Services", "")

    techs = cfg["General"].get("SupportedTechnologies", "")
    if "LE;" in techs or "LongTermKey" in cfg:
        cfg["General"]["SupportedTechnologies"] = "BR/EDR;LE;"
    else:
        cfg["General"]["SupportedTechnologies"] = "BR/EDR;"

    cfg["LinkKey"] = {
        "Key":       bond.link_key_hex,
        "Type":      str(bond.key_type),
        "PINLength": str(bond.pin_length),
    }

    if bond.device_id:
        cfg["DeviceID"] = {
            "Source":  str(bond.device_id.get("source",  "")),
            "Vendor":  str(bond.device_id.get("vendor",  "")),
            "Product": str(bond.device_id.get("product", "")),
            "Version": str(bond.device_id.get("version", "")),
        }

    with open(info_path, "w") as f:
        cfg.write(f, space_around_delimiters=False)

    return info_path


# ── Permissions (RC9 step d) ─────────────────────────────────────────────────

def set_info_permissions(info_path: Path) -> None:
    """Set ownership root:root and mode 600 on a BlueZ info file."""
    import shutil
    os.chmod(info_path, 0o600)
    shutil.chown(info_path, user="root", group="root")


# ── Daemon lifecycle (RC9 steps a, e) ────────────────────────────────────────

def stop_bluetooth() -> None:
    """Stop the Bluetooth daemon (same mechanism as restart_bluetooth_stack)."""
    subprocess.run(["systemctl", "stop", "bluetooth"], check=True)


def start_bluetooth() -> None:
    """Start the Bluetooth daemon and wait until the adapter is powered on."""
    subprocess.run(["systemctl", "start", "bluetooth"], check=True)
    for _ in range(10):
        time.sleep(1)
        r = subprocess.run(
            ["bluetoothctl", "power", "on"],
            capture_output=True, text=True,
        )
        if "succeeded" in r.stdout.lower() or "powered: yes" in r.stdout.lower():
            break


# ── Discovery gate (RC9 step f, RC12) ────────────────────────────────────────

def disable_discovery_if_active() -> bool:
    """
    Disable BT discovery scan ONLY if currently active (RC12).
    Returns True if it was disabled, False if already off.
    An active inquiry scan can obscure or interfere with a direct connect
    attempt on BR/EDR devices.
    """
    try:
        show = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True, text=True, timeout=5,
        )
        if "Discovering: yes" in show.stdout:
            subprocess.run(["bluetoothctl", "scan", "off"],
                           capture_output=True, timeout=5)
            return True
    except Exception:
        pass
    return False
