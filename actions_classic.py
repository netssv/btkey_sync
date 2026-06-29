"""
actions_classic.py

Top-level orchestration for Classic (BR/EDR) export and import flows.

Implements RC5, RC6, RC7, RC9, RC11 from CLASSIC_SUPPORT.agent.md.
Each step of the bonding lifecycle is sequenced explicitly here — the backend
exposes discrete methods (RC9) and this layer calls them in order.
"""

from __future__ import annotations

from pathlib import Path

import tui_helpers as tui
import actions_common as common
from exporters.classic_exporter import export_classic_bond
from importers.classic_importer import load_classic_bond_from_reg_file
from platform_detect import OSKind
from actions_verify import verify_and_connect


def _filter_classic_devices(backend, devices):
    """Return only devices whose info file has a [LinkKey] section."""
    import configparser
    classic = []
    for d in devices:
        info_path = Path(d.raw_source_path)
        if not info_path.exists():
            continue
        cfg = configparser.ConfigParser()
        try:
            cfg.read(info_path)
            if "LinkKey" in cfg:
                classic.append(d)
        except Exception:
            pass
    return classic


def run_classic_export_flow() -> None:
    """
    Full Classic export flow:
    pre-flight → pick device → extract bond → write .reg + .json.
    """
    tui.header("Classic Export — Step 1: System detection")
    env, backend = common.detect_and_validate()

    tui.header("Classic Export — Pre-flight Check")
    if not tui.warn_classic_preflight():
        tui.info("Aborted — pre-flight not acknowledged.")
        return

    tui.header("Classic Export — Step 2: Choose device")
    with tui.Spinner("Scanning bonded devices…"):
        all_devices = backend.list_devices()

    classic_devices = _filter_classic_devices(backend, all_devices)
    if not classic_devices:
        tui.warn("No Classic (BR/EDR) bonded devices found.")
        tui.info("Classic devices have a [LinkKey] section in their BlueZ info file.")
        return

    chosen = common.prompt_select_device(classic_devices)
    if not chosen:
        tui.info("Cancelled.")
        return

    tui.header("Classic Export — Step 3: Extract and save")
    with tui.Spinner(f"Extracting Classic bond for {chosen.device_mac}…"):
        bond = backend.extract_classic_bond(chosen)

    tui.info(bond.summary())

    with tui.Spinner("Writing export files…"):
        reg_path = export_classic_bond(bond)

    tui.ok(f"Exported to: {tui.bold(reg_path.name)}")
    tui.warn("Keep the device powered ON. Do NOT power-cycle it before importing.")


def run_classic_import_flow(reg_file_arg: str | None = None) -> None:
    """
    Full Classic import flow (RC5, RC9, RC11):
    pre-flight → load .reg → check active link → confirm remove →
    stop BT → remove → write info → set perms → start BT → post-import warnings.
    """
    tui.header("Classic Import — Step 1: System detection")
    env, backend = common.detect_and_validate()

    if env.os_kind != OSKind.LINUX:
        tui.err("Classic import (write destination) is only supported on Linux.")
        return

    tui.header("Classic Import — Pre-flight Check")
    if not tui.warn_classic_preflight():
        tui.info("Aborted — pre-flight not acknowledged.")
        return

    tui.header("Classic Import — Step 2: Load bond key")
    reg_path = Path(reg_file_arg) if reg_file_arg else common.prompt_select_export_file()
    if not reg_path:
        tui.info("Cancelled.")
        return
    if not reg_path.exists():
        tui.err(f"File not found: {reg_path}")
        return

    with tui.Spinner(f"Parsing {reg_path.name}…"):
        bond = load_classic_bond_from_reg_file(reg_path)

    tui.info(bond.summary())

    if not bond.device_name:
        name = tui.ask("Device has no name — enter a label (Enter to skip)", default="")
        if name.strip():
            bond.device_name = name.strip()

    tui.header("Classic Import — Step 3: Pre-write checks")

    # RC11: check for active connection before touching bonding state
    with tui.Spinner(f"Checking if {bond.device_mac} is currently connected…"):
        is_connected = backend.check_active_link(bond.device_mac)

    if is_connected:
        tui.warn(f"Device {bond.device_mac} is currently connected!")
        tui.info("Writing bonding state over an active connection may leave BlueZ inconsistent.")
        confirm = tui.ask("Proceed anyway? [y/N]", default="N")
        if confirm.strip().lower() != "y":
            tui.info("Aborted. Disconnect the device first, then re-run.")
            return

    # RC5: remove existing bonding folder with explicit user confirmation
    target_dir = (backend.bluetooth_dir / bond.adapter_mac / bond.device_mac)
    if target_dir.exists():
        tui.warn(f"Existing bonding folder found: {target_dir}")
        tui.info("It must be removed before writing the new Classic bond (RC5).")
        tui.info("A timestamped backup will be kept automatically.")
        confirm_rm = tui.ask("Remove existing bonding? [y/N]", default="N")
        if confirm_rm.strip().lower() != "y":
            tui.info("Aborted. Existing bonding preserved.")
            return

    tui.header("Classic Import — Step 4: Write bond to disk")

    with tui.Spinner("Stopping Bluetooth service…"):
        backend.stop_bluetooth()
    tui.ok("Bluetooth service stopped.")

    if target_dir.exists():
        with tui.Spinner(f"Removing {bond.device_mac} bonding folder…"):
            backend.remove_bond_key(bond.adapter_mac, bond.device_mac)
        tui.ok("Old bonding removed (backup kept).")

    with tui.Spinner("Disabling discovery scan if active…"):
        disabled = backend.disable_discovery_if_active()
    if disabled:
        tui.info("Discovery scan disabled.")

    with tui.Spinner(f"Writing Classic info for {bond.device_mac}…"):
        write_dir = backend.bluetooth_dir / bond.adapter_mac / bond.device_mac
        info_path = backend.write_classic_info(bond, write_dir)
        backend.set_info_permissions(info_path)
    tui.ok(f"Written to: {tui.dim(str(info_path))}")

    with tui.Spinner("Starting Bluetooth service…"):
        backend.start_bluetooth()
    tui.ok("Bluetooth service started.")

    # RC7: mandatory post-import instructions
    tui.warn_classic_post_import(bond.device_mac)

    verify_and_connect(backend, bond.device_mac)
