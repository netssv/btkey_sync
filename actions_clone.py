"""
actions_clone.py

Unified, simplified cloning workflow for BLE, Classic, and Dual-Mode devices.
Maintains 200-line file limit (AGENTS.md R14).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from platform_detect import OSKind
import tui_helpers as tui
import actions_common as common
from models import BondKey, LinkKeyBond
from actions_verify import verify_and_connect
from backends.offline_windows import extract_bt_keys_from_hive, reged_available
from backends.offline_windows_classic import extract_classic_bt_keys_from_hive


class UnifiedDevice:
    """Represents a device that can have BLE keys, Classic keys, or both."""
    def __init__(self, mac: str, name: str | None = None):
        self.mac = mac
        self.name = name or "Unknown Device"
        self.ble_bond: BondKey | None = None
        self.classic_bond: LinkKeyBond | None = None

    def summary(self) -> str:
        types = []
        if self.ble_bond:
            types.append("BLE")
        if self.classic_bond:
            types.append("Classic")
        return f"{tui.bold(self.name)} ({'/'.join(types)}) [{tui.dim(self.mac)}]"


def _load_windows_devices(win_mount: Path | None) -> list[UnifiedDevice]:
    """Load both Classic and BLE keys from Windows partition and merge them."""
    from actions_common import find_windows_system_hive
    if not reged_available():
        tui.err("reged (chntpw) is not installed.")
        return []

    hive = find_windows_system_hive(win_mount)
    if not hive:
        tui.err("Could not find Windows SYSTEM hive.")
        return []

    tui.ok(f"Hive: {tui.dim(str(hive))}")
    with tui.Spinner("Reading Windows registry hive…"):
        ble_bonds = []
        try:
            ble_bonds = extract_bt_keys_from_hive(hive)
        except Exception:
            pass
        classic_bonds = []
        try:
            classic_bonds = extract_classic_bt_keys_from_hive(hive)
        except Exception:
            pass

    devices_map: dict[str, UnifiedDevice] = {}
    for b in ble_bonds:
        mac = b.device_mac.upper()
        if mac not in devices_map:
            devices_map[mac] = UnifiedDevice(mac, b.device_name)
        devices_map[mac].ble_bond = b

    for cb in classic_bonds:
        mac = cb.device_mac.upper()
        if mac not in devices_map:
            devices_map[mac] = UnifiedDevice(mac, cb.device_name)
        devices_map[mac].classic_bond = cb
        # Propagate name if Classic has it but BLE didn't
        if cb.device_name and devices_map[mac].name == "Unknown Device":
            devices_map[mac].name = cb.device_name

    return list(devices_map.values())


def _load_linux_devices(backend) -> list[UnifiedDevice]:
    """Scan local Linux BlueZ directory for bonded devices."""
    devices = backend.list_devices()
    devices_map: dict[str, UnifiedDevice] = {}
    for d in devices:
        mac = d.device_mac.upper()
        dev = UnifiedDevice(mac, d.device_name)
        
        # Try to extract BLE key
        if d.has_ltk:
            try:
                dev.ble_bond = backend.extract_bond_key(d)
            except Exception:
                pass
        
        # Try to extract Classic key
        try:
            dev.classic_bond = backend.extract_classic_bond(d)
        except Exception:
            pass
            
        if dev.ble_bond or dev.classic_bond:
            devices_map[mac] = dev
            
    return list(devices_map.values())


def run_unified_clone_flow() -> None:
    """Clones a device from source (Windows/Linux) automatically handling BLE/Classic/Both."""
    tui.header("Clone Device — Step 1: System detection")
    env, backend = common.detect_and_validate()
    if env.os_kind != OSKind.LINUX:
        tui.err("Cloning is only supported on Linux.")
        return

    tui.header("Clone Device — Step 2: Choose source")
    source_os, win_mount = common.prompt_source_os(env.os_kind)
    
    if source_os == OSKind.WINDOWS:
        devices = _load_windows_devices(win_mount)
    else:
        devices = _load_linux_devices(backend)

    if not devices:
        tui.warn("No bonded devices found on the source.")
        return

    tui.header("Clone Device — Step 3: Choose device")
    for i, dev in enumerate(devices, 1):
        print(f"  [{i}]  {dev.summary()}")
    print()

    choice = tui.ask("Device number [q to cancel]", default="q")
    if choice.lower() == "q" or not choice.isdigit() or not (1 <= int(choice) <= len(devices)):
        tui.info("Cancelled.")
        return

    chosen_dev = devices[int(choice) - 1]

    # Pre-flight warnings for Classic or Dual-Mode
    if chosen_dev.classic_bond:
        tui.header("Classic/Dual-Mode Pre-flight Warning")
        tui.warn("This device contains Bluetooth Classic bonding keys.")
        tui.warn("Classic/Dual-Mode devices usually support only ONE active bonding slot.")
        tui.info("Make sure the device is powered on, and do NOT put it in pairing mode during/after clone.")
        if tui.ask("Type 'yes' to proceed", default="").lower() != "yes":
            tui.info("Aborted.")
            return

    tui.header("Clone Device — Step 4: Destination MAC")
    target_mac = tui.ask("Destination MAC (Enter to keep original)", default=chosen_dev.mac)

    # Connection and confirmation checks
    with tui.Spinner(f"Checking if {target_mac} is currently connected…"):
        is_connected = backend.check_active_link(target_mac)
    if is_connected:
        tui.warn(f"Device {target_mac} is currently connected!")
        if tui.ask("Proceed anyway? [y/N]", default="N").strip().lower() != "y":
            tui.info("Aborted.")
            return

    target_dir = backend.bluetooth_dir / (chosen_dev.ble_bond or chosen_dev.classic_bond).adapter_mac / target_mac
    if target_dir.exists():
        tui.warn(f"Existing bonding folder found: {target_dir}")
        if tui.ask("Over-write existing bonding? [y/N]", default="N").strip().lower() != "y":
            tui.info("Aborted.")
            return

    tui.header("Clone Device — Step 5: Write and restart Bluetooth")
    with tui.Spinner("Stopping Bluetooth service…"):
        backend.stop_bluetooth()

    if target_dir.exists():
        # backup is handled inside remove_bond_key / import_bond_key
        backend.remove_bond_key((chosen_dev.ble_bond or chosen_dev.classic_bond).adapter_mac, target_mac)

    backend.disable_discovery_if_active()

    # Write whatever keys we have
    with tui.Spinner(f"Writing bonding info for {target_mac}…"):
        # 1. Classic key
        if chosen_dev.classic_bond:
            bond_classic = chosen_dev.classic_bond
            # Clone with target MAC
            bond_clone = LinkKeyBond(
                adapter_mac=bond_classic.adapter_mac,
                device_mac=target_mac,
                link_key_hex=bond_classic.link_key_hex,
                key_type=bond_classic.key_type,
                pin_length=bond_classic.pin_length,
                device_class=bond_classic.device_class,
                device_name=chosen_dev.name,
                device_id=bond_classic.device_id,
                service_uuids=bond_classic.service_uuids,
                source_os=bond_classic.source_os,
            )
            backend.write_classic_info(bond_clone, target_dir)

        # 2. BLE key (without deleting LinkKey since we modified import_bond_key/write_classic_info to be non-destructive!)
        if chosen_dev.ble_bond:
            bond_ble = chosen_dev.ble_bond
            # Clone with target MAC
            bond_clone = BondKey(
                adapter_mac=bond_ble.adapter_mac,
                device_mac=target_mac,
                ltk_hex=bond_ble.ltk_hex,
                ediv=bond_ble.ediv,
                erand=bond_ble.erand,
                auth_req=bond_ble.auth_req,
                device_name=chosen_dev.name,
                source_os=bond_ble.source_os,
                irk_hex=bond_ble.irk_hex,
                address_type=bond_ble.address_type,
                is_le=bond_ble.is_le,
            )
            backend.import_bond_key(bond_clone, target_device_mac=target_mac)

        # Set correct permissions
        backend.set_info_permissions(target_dir / "info")

    with tui.Spinner("Starting Bluetooth service…"):
        backend.start_bluetooth()

    tui.ok("Bonding successfully cloned!")
    if chosen_dev.classic_bond:
        tui.warn_classic_post_import(target_mac)

    verify_and_connect(backend, target_mac)
