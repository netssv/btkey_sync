"""
backends/linux_common.py

Shared Linux/BlueZ daemon, permissions, and filesystem helpers.
Respects 200-line limit (AGENTS.md R14).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def get_section(config, name: str):
    """Return config[name], creating the section first if needed.

    Do NOT use `config.setdefault(name, {})[key] = v`: for a missing section it
    returns the throwaway `{}` default (not the new section), so `v` is lost.
    """
    if not config.has_section(name):
        config.add_section(name)
    return config[name]


def set_info_permissions(info_path: Path) -> None:
    """Set ownership root:root and mode 600 on a BlueZ info file."""
    if not info_path.exists():
        return
    os.chmod(info_path, 0o600)
    try:
        shutil.chown(info_path, user="root", group="root")
    except Exception:
        pass


def backup_info_file(info_path: Path) -> None:
    """Creates a timestamped 0600 backup of an info file."""
    if not info_path.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = info_path.with_name(f"info.bak-{ts}")
    shutil.copy2(info_path, backup)
    os.chmod(backup, 0o600)


def remove_bond_directory(bluetooth_dir: Path, adapter_mac: str, device_mac: str, remove_backups: bool = False) -> None:
    """Safely removes a device bonding directory with optional secure backup."""
    target_dir = bluetooth_dir / adapter_mac / device_mac
    if target_dir.exists() and target_dir.is_dir():
        if not remove_backups:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            bak_dir = target_dir.with_name(f"{device_mac}.bak-{ts}")
            try:
                shutil.copytree(target_dir, bak_dir, dirs_exist_ok=True)
                os.chmod(bak_dir, 0o700)
            except Exception:
                pass
        shutil.rmtree(target_dir)
    if remove_backups and (bluetooth_dir / adapter_mac).exists():
        for p in (bluetooth_dir / adapter_mac).iterdir():
            if p.is_dir() and p.name.startswith(f"{device_mac}.bak-"):
                shutil.rmtree(p, ignore_errors=True)


def stop_bluetooth_daemon() -> None:
    """Stop the BlueZ bluetooth daemon."""
    subprocess.run(["systemctl", "stop", "bluetooth"], check=True)


def start_bluetooth_daemon() -> None:
    """Start BlueZ bluetooth daemon and wait for adapter to power on."""
    subprocess.run(["systemctl", "start", "bluetooth"], check=True)
    for _ in range(10):
        time.sleep(1)
        r = subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, text=True)
        if "succeeded" in r.stdout.lower() or "powered: yes" in r.stdout.lower():
            break


def restart_bluetooth_daemon() -> None:
    """Restart BlueZ daemon and power on adapter."""
    subprocess.run(["systemctl", "restart", "bluetooth"], check=True)
    for _ in range(10):
        time.sleep(1)
        r = subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, text=True)
        if "succeeded" in r.stdout.lower() or "powered: yes" in r.stdout.lower():
            break


def check_active_link(device_mac: str) -> bool:
    """Return True if bluetoothctl reports the device is currently connected."""
    try:
        res = subprocess.run(["bluetoothctl", "info", device_mac], capture_output=True, text=True, timeout=8)
        return "Connected: yes" in res.stdout
    except Exception:
        return False


def disable_discovery_if_active() -> bool:
    """Disable BT discovery scan if active (RC12)."""
    try:
        show = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5)
        if "Discovering: yes" in show.stdout:
            subprocess.run(["bluetoothctl", "scan", "off"], capture_output=True, timeout=5)
            return True
    except Exception:
        pass
    return False
