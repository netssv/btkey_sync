#!/usr/bin/env python3
"""
resync_bt_mouse.py

Re-creates the BlueZ bonding record for a BLE mouse on Linux, using the
LTK/EDIV/Rand values currently active on the Windows side of a dual-boot
setup. Use this whenever:

  - You deleted the device from Linux (bluetoothctl remove / GUI "forget")
    and want to re-add it WITHOUT a real re-pair, reusing the bond Windows
    already has.
  - The mouse rotated its BLE private address (RPA) and Linux needs to be
    pointed at the new MAC with the same LTK.

WHY THIS IS NEEDED (read before running):
  This mouse uses BLE Resolvable Private Addresses. The MAC address you see
  is not guaranteed to stay fixed forever, but the LTK (Long Term Key) is
  the actual stable cryptographic identity of the bond. Windows and BlueZ
  store EDIV/Rand in DIFFERENT formats (hex vs decimal), so this script
  does the conversion for you and writes BlueZ's expected format directly,
  removing the chance of a manual hex->decimal transcription error.

REQUIREMENTS:
  - Must be run with sudo (writes under /var/lib/bluetooth).
  - bluetoothd will be restarted at the end (sudo systemctl restart bluetooth).
  - Update the CONFIG block below with current values from Windows before running.

USAGE:
  sudo python3 resync_bt_mouse.py
  sudo python3 resync_bt_mouse.py --dry-run     # show what would happen, write nothing
  sudo python3 resync_bt_mouse.py --connect     # also attempt bluetoothctl connect at the end
"""

import argparse
import configparser
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ============================================================
# CONFIG -- update these from the Windows .reg export each time
# the mouse re-pairs on Windows and gets a new MAC / EDIV / Rand.
# ============================================================

ADAPTER_MAC = "70:08:94:93:77:78"          # Your Bluetooth adapter MAC (ls /var/lib/bluetooth/)
DEVICE_MAC = "68:78:56:11:35:AD"           # Current MAC the mouse presents (from Windows registry subkey name)
DEVICE_NAME = "BT4.0 Mouse"

LTK_HEX = "33221166554499887722110055443366"   # From Windows "LTK" value, hex bytes concatenated, no separators
EDIV_HEX = "3f07"                                # From Windows "EDIV" dword (without 0x prefix)
ERAND_HEX_LE_BYTES = "fe,f7,be,f5,ae,73,9a,d6"   # From Windows "ERand" hex(b) value, exactly as it appears in the .reg

KEY_LENGTH = 16
AUTH_REQ = 0x2D          # From Windows AuthReq dword, informational only (not written to BlueZ info file)

BLUETOOTH_DIR = Path("/var/lib/bluetooth")

# Device metadata mirrored from a previously known-good info file for this
# mouse. Adjust if your device exposes different services/appearance.
APPEARANCE = "0x03c2"
ICON_SERVICES = (
    "00001800-0000-1000-8000-00805f9b34fb;"
    "00001801-0000-1000-8000-00805f9b34fb;"
    "0000180a-0000-1000-8000-00805f9b34fb;"
    "0000180f-0000-1000-8000-00805f9b34fb;"
    "00001812-0000-1000-8000-00805f9b34fb;"
)
CONN_MIN_INTERVAL = 7
CONN_MAX_INTERVAL = 7
CONN_LATENCY = 32
CONN_TIMEOUT = 300
DEVICE_ID_SOURCE = 2
DEVICE_ID_VENDOR = 4661
DEVICE_ID_PRODUCT = 43554
DEVICE_ID_VERSION = 1


def require_root():
    if os.geteuid() != 0:
        sys.exit("This script must be run with sudo (it writes under /var/lib/bluetooth).")


def hex_to_decimal(hex_str: str) -> int:
    """Convert a plain hex string (e.g. '3f07') to a decimal int."""
    cleaned = hex_str.strip().lower().replace("0x", "")
    return int(cleaned, 16)


def le_bytes_to_decimal(byte_csv: str) -> int:
    """
    Convert a comma-separated little-endian byte list, exactly as it appears
    in a Windows hex(b) registry export (e.g. 'fe,f7,be,f5,ae,73,9a,d6'),
    into the correct decimal integer.

    Windows hex(b) byte order in the .reg file is little-endian, so the
    first byte listed is the LEAST significant byte. We reverse the byte
    order to get big-endian, then parse as hex.
    """
    parts = [p.strip() for p in byte_csv.split(",") if p.strip()]
    be_hex = "".join(parts[::-1])  # reverse to big-endian order
    return int(be_hex, 16)


def find_existing_device_dir(adapter_dir: Path, device_mac: str) -> Path | None:
    candidate = adapter_dir / device_mac
    return candidate if candidate.exists() else None


def find_any_old_mouse_dir(adapter_dir: Path, device_name: str) -> Path | None:
    """Best-effort scan: look for any device folder whose info file Name matches."""
    if not adapter_dir.exists():
        return None
    for entry in adapter_dir.iterdir():
        if not entry.is_dir():
            continue
        info_path = entry / "info"
        if not info_path.exists():
            continue
        try:
            text = info_path.read_text()
        except PermissionError:
            continue
        if f"Name={device_name}" in text:
            return entry
    return None


def write_info_file(device_dir: Path, ediv: int, rand: int, dry_run: bool):
    device_dir_display = device_dir
    content = f"""[General]
Name={DEVICE_NAME}
Appearance={APPEARANCE}
SupportedTechnologies=LE;
Trusted=true
Blocked=false
CablePairing=false
WakeAllowed=true
Services={ICON_SERVICES}

[LongTermKey]
Key={LTK_HEX}
Authenticated=0
EncSize={KEY_LENGTH}
EDiv={ediv}
Rand={rand}

[ConnectionParameters]
MinInterval={CONN_MIN_INTERVAL}
MaxInterval={CONN_MAX_INTERVAL}
Latency={CONN_LATENCY}
Timeout={CONN_TIMEOUT}

[DeviceID]
Source={DEVICE_ID_SOURCE}
Vendor={DEVICE_ID_VENDOR}
Product={DEVICE_ID_PRODUCT}
Version={DEVICE_ID_VERSION}
"""
    print(f"\n--- info file that will be written to {device_dir_display / 'info'} ---")
    print(content)
    print("--- end ---\n")

    if dry_run:
        print("[dry-run] Not writing any files.")
        return

    device_dir.mkdir(parents=True, exist_ok=True)
    info_path = device_dir / "info"
    info_path.write_text(content)
    os.chmod(info_path, 0o600)
    # BlueZ stores these as root:root
    shutil.chown(info_path, user="root", group="root")
    print(f"Wrote {info_path} with mode 600, owner root:root.")


def run(cmd: list[str], dry_run: bool):
    print(f"$ {' '.join(cmd)}")
    if dry_run:
        print("[dry-run] Not executing.")
        return
    subprocess.run(cmd, check=False)


def main():
    parser = argparse.ArgumentParser(description="Re-sync BLE mouse bond on Linux from Windows bonding data.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing/restarting anything.")
    parser.add_argument("--connect", action="store_true", help="Attempt 'bluetoothctl connect' at the end.")
    args = parser.parse_args()

    if not args.dry_run:
        require_root()

    adapter_dir = BLUETOOTH_DIR / ADAPTER_MAC
    if not adapter_dir.exists():
        sys.exit(f"Adapter directory not found: {adapter_dir}\n"
                  f"Run 'ls {BLUETOOTH_DIR}' to confirm your adapter MAC.")

    print(f"Adapter directory: {adapter_dir}")

    # Convert Windows hex values to BlueZ decimal format
    ediv_decimal = hex_to_decimal(EDIV_HEX)
    rand_decimal = le_bytes_to_decimal(ERAND_HEX_LE_BYTES)

    print(f"EDIV  hex 0x{EDIV_HEX} -> decimal {ediv_decimal}")
    print(f"Rand  bytes [{ERAND_HEX_LE_BYTES}] -> decimal {rand_decimal}")
    print(f"LTK   {LTK_HEX} (no conversion needed)")

    # Sanity check LTK length
    if len(LTK_HEX) != KEY_LENGTH * 2:
        print(f"WARNING: LTK_HEX has {len(LTK_HEX)} hex chars, expected {KEY_LENGTH * 2} for a {KEY_LENGTH}-byte key.")

    target_dir = adapter_dir / DEVICE_MAC

    if target_dir.exists():
        print(f"Target device directory already exists: {target_dir}")
        print("It will be overwritten with the info file below.")
    else:
        old_dir = find_any_old_mouse_dir(adapter_dir, DEVICE_NAME)
        if old_dir:
            print(f"Found a previous bonding folder for '{DEVICE_NAME}' at {old_dir}, "
                  f"but it has a different MAC than the configured DEVICE_MAC ({DEVICE_MAC}). "
                  f"Not reusing it automatically -- writing a fresh info file instead.")
        else:
            print(f"No existing folder found for {DEVICE_MAC} or for a device named '{DEVICE_NAME}'. "
                  f"Creating fresh.")

    write_info_file(target_dir, ediv_decimal, rand_decimal, args.dry_run)

    print("\nRestarting bluetooth daemon (required for BlueZ to pick up the new/edited bonding folder)...")
    run(["systemctl", "restart", "bluetooth"], args.dry_run)

    if not args.dry_run:
        import time
        time.sleep(2)

    print("\nListing known devices:")
    run(["bluetoothctl", "devices"], args.dry_run)

    print("\nDevice info:")
    run(["bluetoothctl", "info", DEVICE_MAC], args.dry_run)

    if args.connect:
        print("\nAttempting connection:")
        run(["bluetoothctl", "connect", DEVICE_MAC], args.dry_run)

    print("\nDone. If 'Paired: yes' / 'Bonded: yes' / 'Trusted: yes' did not show up above, "
          "the device folder may not have loaded -- re-run 'sudo systemctl restart bluetooth' "
          "and check again with 'bluetoothctl info {}'.".format(DEVICE_MAC))


if __name__ == "__main__":
    main()
