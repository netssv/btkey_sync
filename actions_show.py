"""actions_show.py — Interactive bonding key display flow (Linux or Windows source)."""

from __future__ import annotations

import tui_helpers as tui
import actions_common as common
from platform_detect import OSKind


def run_show_keys_flow() -> None:
    tui.header("Show Keys — Step 1: System detection")
    env, backend = common.detect_and_validate()

    tui.header("Show Keys — Step 2: Choose source")
    source_os, win_mount = common.prompt_source_os(env.os_kind)

    if source_os == OSKind.WINDOWS and env.os_kind == OSKind.LINUX:
        # Read directly from the mounted Windows hive — no import needed
        from actions_windows import _load_hive, _choose_bond
        _, bonds = _load_hive(win_mount)
        if not bonds:
            return
        tui.header("Show Keys — Step 3: Choose device")
        bond = _choose_bond(bonds)
        if not bond:
            return
        tui.header(f"Bonding Key Details: {bond.device_name or bond.device_mac}")
        common.print_bond_summary(bond)
        return

    # Linux source (or running on Windows)
    tui.header("Show Keys — Step 3: Choose device")
    with tui.Spinner("Scanning bonded devices…"):
        devices = backend.list_devices()
    if not devices:
        tui.warn("No bonded devices found.")
        return
    chosen = common.prompt_select_device(devices)
    if not chosen:
        tui.info("No device selected.")
        return
    tui.header(f"Bonding Key Details: {chosen.device_name or chosen.device_mac}")
    try:
        bond = backend.extract_bond_key(chosen)
        common.print_bond_summary(bond)
    except Exception as exc:
        tui.err(f"Could not read keys: {exc}")

