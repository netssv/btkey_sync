from __future__ import annotations
from pathlib import Path
from exporters import export_bond_key
from importers import load_bond_from_reg_file
from platform_detect import OSKind
from storage import ensure_exports_dir
import tui_helpers as tui
import actions_common as common
from actions_verify import verify_and_connect, run_verify_flow  # noqa: F401
from actions_remove import run_remove_flow  # noqa: F401


def _ensure_device_name(bond) -> None:
    """If bond has no name, prompt the user to enter one interactively."""
    if not bond.device_name:
        name = tui.ask("Device has no name — enter a label (Enter to skip)", default="")
        if name.strip():
            bond.device_name = name.strip()


def run_export_flow() -> None:
    tui.header("Select & Extract — Step 1: System detection")
    env, backend = common.detect_and_validate()
    tui.header("Select & Extract — Step 2: Choose extraction source")
    source_os, win_mount = common.prompt_source_os(env.os_kind)
    if source_os == OSKind.WINDOWS and env.os_kind == OSKind.LINUX:
        common.run_offline_windows_export(win_mount, export_bond_key, ensure_exports_dir)
        return
    tui.header("Select & Extract — Step 3: Choose a device")
    with tui.Spinner("Scanning bonded BLE devices…"):
        devices = backend.list_devices()
    chosen = common.prompt_select_device(devices) if devices else None
    if not chosen:
        tui.info("No devices or cancelled.")
        return
    tui.header("Select & Extract — Step 4: Extract and save")
    with tui.Spinner(f"Extracting bonding for {chosen.device_mac}…"):
        bond = backend.extract_bond_key(chosen)
        bond.source_os = source_os.value
    common.print_bond_summary(bond)
    with tui.Spinner("Writing export files…"):
        exports_dir = ensure_exports_dir()
        reg_path = export_bond_key(bond)
    tui.ok(f"Exported to: {tui.bold(reg_path.name)}")


def run_import_flow(reg_file_arg: str | None = None) -> None:
    tui.header("Import — Step 1: System detection")
    env, backend = common.detect_and_validate()
    reg_path = Path(reg_file_arg) if reg_file_arg else common.prompt_select_export_file()
    if not reg_path:
        tui.info("Cancelled.")
        return
    if not reg_path.exists():
        tui.err(f"File not found: {reg_path}")
        return

    tui.header("Import — Step 3: Load bond key")
    with tui.Spinner(f"Parsing {reg_path.name}…"):
        bond = load_bond_from_reg_file(reg_path)
    common.print_bond_summary(bond)
    _ensure_device_name(bond)

    tui.header("Import — Step 4: Compare with local device")
    with tui.Spinner("Looking up local bonding for this device…"):
        existing = backend.list_devices()

    local_match = next(
        (d for d in existing if d.device_mac.upper() == bond.device_mac.upper()), None
    )
    ltk_match = None
    if not local_match:
        for d in existing:
            if d.has_ltk:
                try:
                    local_bond = backend.extract_bond_key(d)
                    if (local_bond.ltk_hex.upper() == bond.ltk_hex.upper() or
                        (bond.irk_hex and local_bond.irk_hex and
                         local_bond.irk_hex.upper() == bond.irk_hex.upper())):
                        ltk_match = d
                        # Propagate name if source has no name
                        if not bond.device_name and local_bond.device_name:
                            bond.device_name = local_bond.device_name
                        break
                except Exception:
                    pass

    source_device_mac = None
    if local_match and local_match.has_ltk:
        try:
            local_bond = backend.extract_bond_key(local_match)
            if local_bond.ltk_hex.upper() == bond.ltk_hex.upper():
                tui.ok("Keys are already in sync — LTK matches. No import needed.")
                return
            tui.warn(f"Key mismatch!\n    Local LTK: {local_bond.ltk_hex}\n    New LTK:   {bond.ltk_hex}")
        except Exception:
            pass
    elif ltk_match:
        tui.info(f"Matched physical device under different MAC: {ltk_match.device_mac}")
        q = f"Migrate profile/cache from {ltk_match.device_mac} to {bond.device_mac}? [Y/n]"
        if tui.ask(q, default="Y").upper() == "Y":
            source_device_mac = ltk_match.device_mac
    elif local_match:
        tui.info("Device found locally but has no LTK.")
    else:
        tui.info("Device not paired locally yet.")

    print()
    target_mac = tui.ask("Destination MAC (Enter to keep original)", default=bond.device_mac)
    target_mac = target_mac if target_mac != bond.device_mac else None

    tui.header("Import — Step 5: Write and restart Bluetooth")
    with tui.Spinner("Writing bonding to disk…"):
        written_path = backend.import_bond_key(
            bond, target_device_mac=target_mac, source_device_mac=source_device_mac
        )
    tui.ok(f"Written to: {tui.dim(str(written_path))}")
    tui.info("Restarting Bluetooth service to apply changes. This may take a moment…")
    with tui.Spinner("Restarting Bluetooth service…"):
        backend.restart_bluetooth_stack()
    tui.ok("Bluetooth service restarted.")
    verify_and_connect(backend, target_mac or bond.device_mac)


def _pick_source_bond(source_os: OSKind, win_mount, env) -> "BondKey | None":
    """Extract a bond from the chosen source (Linux or Windows partition)."""
    if source_os == OSKind.WINDOWS and env.os_kind == OSKind.LINUX:
        return common.pick_bond_from_windows(win_mount)
    src_backend = common.get_backend(source_os)
    with tui.Spinner("Scanning bonded BLE devices…"):
        devices = src_backend.list_devices()
    if not devices:
        tui.warn("No bonded BLE devices found.")
        return None
    source = common.prompt_select_device(devices)
    if source is None:
        tui.info("Cancelled.")
        return None
    with tui.Spinner(f"Extracting bonding for {source.device_mac}…"):
        bond = src_backend.extract_bond_key(source)
        bond.source_os = source_os.value
    return bond

def run_clone_flow() -> None:
    tui.header("Clone — Step 1: System detection")
    env, backend = common.detect_and_validate()
    if env.os_kind != OSKind.LINUX:
        tui.err("Clone (write destination) is only supported on Linux.")
        return
    tui.header("Clone — Step 2: Choose source")
    source_os, win_mount = common.prompt_source_os(env.os_kind)
    bond = _pick_source_bond(source_os, win_mount, env)
    if bond is None:
        return
    common.print_bond_summary(bond)
    _ensure_device_name(bond)
    tui.header("Clone — Step 3: Destination MAC")
    target_mac = tui.ask("Destination MAC (Enter = same)", default=bond.device_mac)
    with tui.Spinner("Scanning local devices…"):
        existing = backend.list_devices()
    local_match = next((d for d in existing if d.device_mac.upper() == target_mac.upper()), None)
    ltk_match = None
    if not local_match:
        for d in existing:
            if d.has_ltk:
                try:
                    local_bond = backend.extract_bond_key(d)
                    if (local_bond.ltk_hex.upper() == bond.ltk_hex.upper() or
                        (bond.irk_hex and local_bond.irk_hex and
                         local_bond.irk_hex.upper() == bond.irk_hex.upper())):
                        ltk_match = d
                        if not bond.device_name and local_bond.device_name:
                            bond.device_name = local_bond.device_name
                        break
                except Exception:
                    pass
    source_device_mac = None
    if ltk_match:
        tui.info(f"Matched physical device under different MAC: {ltk_match.device_mac}")
        q = f"Migrate profile/cache from {ltk_match.device_mac} to {target_mac}? [Y/n]"
        if tui.ask(q, default="Y").upper() == "Y":
            source_device_mac = ltk_match.device_mac
    tui.header("Clone — Step 4: Write and restart Bluetooth")
    with tui.Spinner("Writing cloned bonding to disk…"):
        written_path = backend.import_bond_key(
            bond, target_device_mac=target_mac, source_device_mac=source_device_mac
        )
    tui.ok(f"Cloned bonding written to: {tui.dim(str(written_path))}")
    tui.info("Restarting Bluetooth service to apply changes. This may take a moment…")
    with tui.Spinner("Restarting Bluetooth service…"):
        backend.restart_bluetooth_stack()
    tui.ok("Bluetooth service restarted.")
    verify_and_connect(backend, target_mac)
