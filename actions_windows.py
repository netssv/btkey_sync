"""actions_windows.py — Helpers for offline Windows partition extraction flows."""

from __future__ import annotations
from pathlib import Path
from typing import Optional

import tui_helpers as tui


def _load_hive(win_mount: Optional[Path]):
    """Locate hive, check reged, return (hive_path, bonds) or (None, None)."""
    from backends.offline_windows import extract_bt_keys_from_hive, reged_available
    from actions_common import find_windows_system_hive
    if not reged_available():
        tui.err("reged not found. Install chntpw (e.g. `sudo pacman -S chntpw`).")
        return None, None
    hive = find_windows_system_hive(win_mount)
    if not hive:
        tui.err("Could not locate the Windows SYSTEM hive on the selected partition.")
        return None, None
    tui.ok(f"Hive: {tui.dim(str(hive))}")
    with tui.Spinner("Reading Windows registry hive…"):
        bonds = extract_bt_keys_from_hive(hive)
    if not bonds:
        tui.warn("No BLE bonding keys found in the Windows hive.")
        return hive, None
    return hive, bonds


def _choose_bond(bonds):
    """Present a numbered list and return the chosen BondKey, or None."""
    for i, b in enumerate(bonds, 1):
        print(f"  [{i}]  {tui.bold(b.device_name or b.device_mac)}  {tui.dim(b.device_mac)}")
    raw = tui.ask("→ Device number [q to cancel]", default="q")
    if raw.lower() == "q" or not raw.isdigit() or not (1 <= int(raw) <= len(bonds)):
        tui.info("Cancelled.")
        return None
    return bonds[int(raw) - 1]


def run_offline_windows_export(win_mount: Optional[Path], export_fn, exports_dir_fn) -> None:
    """Interactive offline Windows hive export flow (called from actions.py)."""
    from actions_common import print_bond_summary
    tui.header("Select & Extract — Windows partition mode")
    _, bonds = _load_hive(win_mount)
    if not bonds:
        return
    tui.header("Select & Extract — Step 3: Choose a device")
    bond = _choose_bond(bonds)
    if not bond:
        return
    print_bond_summary(bond)
    with tui.Spinner("Writing export files…"):
        exports_dir = exports_dir_fn()
        reg_path = export_fn(bond)
    print()
    tui.ok(f"Exports folder : {tui.dim(str(exports_dir))}")
    tui.ok(f"File generated : {tui.bold(reg_path.name)}")
    tui.ok(f"Metadata (JSON): {tui.bold(reg_path.with_suffix('.json').name)}")


def pick_bond_from_windows(win_mount: Optional[Path]):
    """Interactively pick one BondKey from a mounted Windows partition hive.

    Used by the Clone flow (direct Windows→Linux import without intermediate file).
    """
    _, bonds = _load_hive(win_mount)
    if not bonds:
        return None
    return _choose_bond(bonds)
