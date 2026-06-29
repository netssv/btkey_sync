"""
actions_classic_extra.py

Classic (BR/EDR) Bluetooth verification and cloning workflows.
Kept in a separate file to adhere to the 200-line limit (AGENTS.md R14).
"""

from __future__ import annotations

from pathlib import Path
from platform_detect import OSKind
import tui_helpers as tui
import actions_common as common
from models import LinkKeyBond
from actions_verify import verify_and_connect
from exporters.classic_exporter import export_classic_bond
from backends.offline_windows_classic import extract_classic_bt_keys_from_hive


def run_classic_verify_flow() -> None:
    """Choose a bonded Classic device and attempt connection."""
    tui.header("Verify & Connect (Classic) — Step 1: System detection")
    env, backend = common.detect_and_validate()
    if env.os_kind != OSKind.LINUX:
        tui.err("Verify & Connect is only supported on Linux.")
        return

    tui.header("Verify & Connect (Classic) — Step 2: Choose device")
    with tui.Spinner("Scanning bonded Classic devices…"):
        all_devices = backend.list_devices()

    from actions_classic import _filter_classic_devices
    classic_devices = _filter_classic_devices(backend, all_devices)
    if not classic_devices:
        tui.warn("No Classic (BR/EDR) bonded devices found.")
        return

    chosen = common.prompt_select_device(classic_devices)
    if not chosen:
        tui.info("No device selected.")
        return

    verify_and_connect(backend, chosen.device_mac)


def _load_classic_hive(win_mount: Path | None):
    """Locate hive, check reged, return Classic bonds list or None."""
    from backends.offline_windows import reged_available
    from actions_common import find_windows_system_hive
    if not reged_available():
        tui.err("reged not found. Install chntpw (e.g. `sudo apt install chntpw`).")
        return None
    hive = find_windows_system_hive(win_mount)
    if not hive:
        tui.err("Could not locate the Windows SYSTEM hive on the selected partition.")
        return None
    tui.ok(f"Hive: {tui.dim(str(hive))}")
    with tui.Spinner("Reading Windows registry hive for Classic bonds…"):
        bonds = extract_classic_bt_keys_from_hive(hive)
    if not bonds:
        tui.warn("No Classic (BR/EDR) bonding keys found in the Windows hive.")
        return None
    return bonds


def _choose_classic_bond(bonds) -> LinkKeyBond | None:
    """Present a list and return the chosen LinkKeyBond."""
    for i, b in enumerate(bonds, 1):
        name = b.device_name or "Unknown Device"
        print(f"  [{i}]  {tui.bold(name)}  {tui.dim(b.device_mac)}")
    raw = tui.ask("→ Device number [q to cancel]", default="q")
    if raw.lower() == "q" or not raw.isdigit() or not (1 <= int(raw) <= len(bonds)):
        tui.info("Cancelled.")
        return None
    return bonds[int(raw) - 1]


def _pick_source_classic_bond(source_os: OSKind, win_mount: Path | None, env, backend) -> LinkKeyBond | None:
    """Extract a Classic bond from the chosen source (local Linux or Windows partition)."""
    if source_os == OSKind.WINDOWS and env.os_kind == OSKind.LINUX:
        bonds = _load_classic_hive(win_mount)
        if not bonds:
            return None
        return _choose_classic_bond(bonds)

    with tui.Spinner("Scanning bonded Classic devices…"):
        all_devices = backend.list_devices()
    from actions_classic import _filter_classic_devices
    classic_devices = _filter_classic_devices(backend, all_devices)
    if not classic_devices:
        tui.warn("No bonded Classic devices found.")
        return None
    source = common.prompt_select_device(classic_devices)
    if source is None:
        tui.info("Cancelled.")
        return None
    with tui.Spinner(f"Extracting Classic bond for {source.device_mac}…"):
        bond = backend.extract_classic_bond(source)
        bond.source_os = source_os.value
    return bond


def run_classic_clone_flow() -> None:
    """Directly copy/clone a Classic bonding to the destination on Linux."""
    tui.header("Classic Clone — Step 1: System detection")
    env, backend = common.detect_and_validate()
    if env.os_kind != OSKind.LINUX:
        tui.err("Clone (write destination) is only supported on Linux.")
        return

    # RC6: Classic pre-flight safety warning
    tui.header("Classic Clone — Pre-flight Check")
    if not tui.warn_classic_preflight():
        tui.info("Aborted — pre-flight not acknowledged.")
        return

    tui.header("Classic Clone — Step 2: Choose source")
    source_os, win_mount = common.prompt_source_os(env.os_kind)
    bond = _pick_source_classic_bond(source_os, win_mount, env, backend)
    if bond is None:
        return

    tui.info(bond.summary())
    if not bond.device_name:
        name = tui.ask("Device has no name — enter a label (Enter to skip)", default="")
        if name.strip():
            bond.device_name = name.strip()

    tui.header("Classic Clone — Step 3: Destination MAC")
    target_mac = tui.ask("Destination MAC (Enter to keep original)", default=bond.device_mac)

    tui.header("Classic Clone — Step 4: Pre-write checks")
    # RC11: Check for active connection
    with tui.Spinner(f"Checking if {target_mac} is currently connected…"):
        is_connected = backend.check_active_link(target_mac)
    if is_connected:
        tui.warn(f"Device {target_mac} is currently connected!")
        confirm = tui.ask("Proceed anyway? [y/N]", default="N")
        if confirm.strip().lower() != "y":
            tui.info("Aborted.")
            return

    # RC5: confirmation before overwriting
    target_dir = backend.bluetooth_dir / bond.adapter_mac / target_mac
    if target_dir.exists():
        tui.warn(f"Existing bonding folder found: {target_dir}")
        tui.info("It must be removed before writing (RC5). A backup will be kept.")
        if tui.ask("Remove existing bonding? [y/N]", default="N").strip().lower() != "y":
            tui.info("Aborted.")
            return

    tui.header("Classic Clone — Step 5: Write and restart Bluetooth")
    with tui.Spinner("Stopping Bluetooth service…"):
        backend.stop_bluetooth()

    if target_dir.exists():
        with tui.Spinner(f"Removing {target_mac} bonding folder…"):
            backend.remove_bond_key(bond.adapter_mac, target_mac)

    with tui.Spinner("Disabling discovery scan if active…"):
        backend.disable_discovery_if_active()

    with tui.Spinner(f"Writing Classic info for {target_mac}…"):
        write_dir = backend.bluetooth_dir / bond.adapter_mac / target_mac
        # Clone with target mac
        bond_clone = LinkKeyBond(
            adapter_mac=bond.adapter_mac,
            device_mac=target_mac,
            link_key_hex=bond.link_key_hex,
            key_type=bond.key_type,
            pin_length=bond.pin_length,
            device_class=bond.device_class,
            device_name=bond.device_name,
            device_id=bond.device_id,
            service_uuids=bond.service_uuids,
            source_os=bond.source_os,
        )
        info_path = backend.write_classic_info(bond_clone, write_dir)
        backend.set_info_permissions(info_path)

    with tui.Spinner("Starting Bluetooth service…"):
        backend.start_bluetooth()

    # RC7: Post-import instructions
    tui.warn_classic_post_import(target_mac)

    verify_and_connect(backend, target_mac)
