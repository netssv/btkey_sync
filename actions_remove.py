"""actions_remove.py — Interactive bonding removal flow."""

from __future__ import annotations
from pathlib import Path

import tui_helpers as tui
import actions_common as common


def _find_backups(adapter_mac: str, device_mac: str) -> list[Path]:
    bt_dir = Path("/var/lib/bluetooth")
    if not bt_dir.exists():
        return []
    backups = []
    adapter_dir = bt_dir / adapter_mac
    if adapter_dir.exists():
        for p in adapter_dir.iterdir():
            if p.is_dir() and p.name.startswith(f"{device_mac}.bak-"):
                backups.append(p)
    device_dir = adapter_dir / device_mac
    if device_dir.exists():
        for p in device_dir.iterdir():
            if p.is_file() and p.name.startswith("info.bak-"):
                backups.append(p)
    return backups


def run_remove_flow() -> None:
    tui.header("Remove — Step 1: System detection")
    env, backend = common.detect_and_validate()
    tui.header("Remove — Step 2: Choose device to delete")
    with tui.Spinner("Scanning bonded BLE devices…"):
        devices = backend.list_devices()
    chosen = common.prompt_select_device(devices) if devices else None
    if not chosen:
        tui.info("No device selected.")
        return
    tui.warn(f"Deleting bonding for: {tui.bold(chosen.device_name or chosen.device_mac)}")

    backups = _find_backups(chosen.adapter_mac, chosen.device_mac)
    rm_backups = False
    if backups:
        print(f"\n  {tui.yellow('Found backup files/folders:')}")
        for b in backups:
            print(f"    • {b.name}")
        print()
        rm_backups = tui.ask("Delete these backup files/folders as well? [y/N]", default="N").lower() == "y"

    if tui.ask("Are you absolutely sure you want to remove this device? [y/N]", default="N").lower() == "y":
        with tui.Spinner("Removing bonding…"):
            backend.remove_bond_key(chosen.adapter_mac, chosen.device_mac, remove_backups=rm_backups)
        tui.ok("Removed.")
        tui.info("Restarting Bluetooth service to apply changes. This may take a moment…")
        with tui.Spinner("Restarting Bluetooth…"):
            backend.restart_bluetooth_stack()
        tui.ok("Restarted.")

