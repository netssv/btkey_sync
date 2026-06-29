#!/usr/bin/env python3
"""
resync_ble_mouse.py

Re-syncs the BLE bonding identity (LTK/EDIV/Rand) for a BLE peripheral
(e.g. a cheap BT4.0 mouse using rotating Resolvable Private Addresses)
between Windows and Linux (BlueZ) on a dual-boot setup.

BACKGROUND
----------
Some cheap BLE devices rotate their advertised MAC address (RPA) on
certain re-pairing events while keeping the same Long Term Key (LTK).
When that happens, BlueZ on Linux still has the bond filed under the
OLD MAC address and won't recognize the device under its NEW address,
even though the underlying cryptographic identity hasn't changed.

This script automates the fix:
  1. Locate the Bluetooth adapter folder under /var/lib/bluetooth/
  2. Locate the existing bonded device folder (old MAC)
  3. Copy it to a new folder named after the device's current MAC
  4. Patch EDIV and Rand inside the copied info file (decimal, BlueZ format)
  5. Restart the bluetooth daemon (required -- BlueZ caches devices at
     startup and won't reload manually edited folders on the fly)
  6. Verify the device shows up as Paired/Bonded/Trusted/Connected

USAGE
-----
Run with sudo (it needs to read/write /var/lib/bluetooth and restart
the bluetooth service):

    sudo python3 resync_ble_mouse.py

You will be prompted interactively for:
  - old MAC (the MAC currently filed in /var/lib/bluetooth/<adapter>/)
  - new MAC (the MAC the device is now presenting -- e.g. from Windows'
    registry export, or from `bluetoothctl scan on` / `devices`)
  - EDIV in hex (as shown in the Windows .reg export, e.g. "3f07")
  - Rand in hex (as shown in the Windows .reg export's hex(b) value,
    reassembled into a single 64-bit hex number, e.g. "d69a73aef5bef7fe")

All hex-to-decimal conversion is done with Python's built-in int(),
never by hand, to avoid transcription errors.

You can also pass everything as CLI flags for non-interactive / scripted
use -- see --help.
"""

import argparse
import configparser
import os
import re
import shutil
import subprocess
import sys
import time

BLUETOOTH_BASE = "/var/lib/bluetooth"
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def fail(msg: str) -> None:
    print(f"\n[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg: str) -> None:
    print(f"[*] {msg}")


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def require_root() -> None:
    if os.geteuid() != 0:
        fail("This script must be run as root (use: sudo python3 resync_ble_mouse.py)")


def normalize_mac(mac: str) -> str:
    mac = mac.strip().upper()
    if not MAC_RE.match(mac):
        fail(f"'{mac}' does not look like a valid MAC address (expected format AA:BB:CC:DD:EE:FF)")
    return mac


def hex_to_decimal(label: str, hex_str: str) -> int:
    cleaned = hex_str.strip().lower().replace("0x", "").replace(",", "").replace(" ", "")
    try:
        value = int(cleaned, 16)
    except ValueError:
        fail(f"Could not parse {label} '{hex_str}' as hexadecimal.")
    info(f"Converted {label}: hex 0x{cleaned} -> decimal {value}")
    return value


def find_adapter_dir(explicit_adapter: str | None) -> str:
    if explicit_adapter:
        adapter = normalize_mac(explicit_adapter)
        path = os.path.join(BLUETOOTH_BASE, adapter)
        if not os.path.isdir(path):
            fail(f"Adapter folder not found: {path}")
        return path

    if not os.path.isdir(BLUETOOTH_BASE):
        fail(f"{BLUETOOTH_BASE} does not exist -- is bluetoothd installed / has Bluetooth ever been used?")

    candidates = [
        d for d in os.listdir(BLUETOOTH_BASE)
        if MAC_RE.match(d) and os.path.isdir(os.path.join(BLUETOOTH_BASE, d))
    ]

    if not candidates:
        fail(f"No adapter folders found under {BLUETOOTH_BASE}")
    if len(candidates) > 1:
        print("[*] Multiple adapters found:")
        for c in candidates:
            print(f"      {c}")
        fail("Please re-run with --adapter <MAC> to disambiguate.")

    adapter_path = os.path.join(BLUETOOTH_BASE, candidates[0])
    ok(f"Using adapter: {candidates[0]}")
    return adapter_path


def patch_info_file(info_path: str, ediv_dec: int, rand_dec: int) -> None:
    if not os.path.isfile(info_path):
        fail(f"Expected info file not found: {info_path}")

    parser = configparser.ConfigParser()
    parser.optionxform = str  # preserve key case
    parser.read(info_path)

    if "LongTermKey" not in parser:
        fail(f"No [LongTermKey] section found in {info_path} -- is this really the right device folder?")

    old_ediv = parser["LongTermKey"].get("EDiv", "<missing>")
    old_rand = parser["LongTermKey"].get("Rand", "<missing>")

    parser["LongTermKey"]["EDiv"] = str(ediv_dec)
    parser["LongTermKey"]["Rand"] = str(rand_dec)

    with open(info_path, "w") as f:
        parser.write(f, space_around_delimiters=False)

    ok(f"Patched {info_path}")
    info(f"  EDiv: {old_ediv} -> {ediv_dec}")
    info(f"  Rand: {old_rand} -> {rand_dec}")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    if check and result.returncode != 0:
        fail(f"Command failed ({result.returncode}): {' '.join(cmd)}")
    return result


def restart_bluetooth() -> None:
    info("Restarting bluetooth daemon (required for BlueZ to pick up the new device folder)...")
    run(["systemctl", "restart", "bluetooth"])
    time.sleep(2)
    ok("bluetooth.service restarted")


def verify_device(new_mac: str) -> None:
    info(f"Querying bluetoothctl for {new_mac}...")
    result = run(["bluetoothctl", "info", new_mac], check=False)
    output = result.stdout

    if "Device" not in output:
        print(
            f"\n[WARN] '{new_mac}' did not show up in bluetoothctl info.\n"
            f"       Try: bluetoothctl devices   (to confirm it's listed at all)\n"
            f"       If missing, the bluetooth daemon may need another restart,\n"
            f"       or the device folder/info file may have a formatting issue."
        )
        return

    for field in ("Paired", "Bonded", "Trusted", "Connected"):
        match = re.search(rf"{field}:\s*(\S+)", output)
        status = match.group(1) if match else "unknown"
        marker = "OK" if status == "yes" else ".."
        print(f"    [{marker}] {field}: {status}")

    if "Connected: yes" in output:
        ok("Device is connected.")
    else:
        print(
            "\n[*] Device is bonded but not auto-connected yet.\n"
            f"    Try forcing it: bluetoothctl connect {new_mac}\n"
            "    Or just move/click the mouse -- many BLE HID devices auto-reconnect on activity."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-sync a BLE device's bonding identity between Windows and Linux/BlueZ "
                    "after its advertised MAC address has rotated (RPA behavior).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--adapter", help="Bluetooth adapter MAC (auto-detected if omitted)")
    parser.add_argument("--old-mac", help="Existing bonded device MAC currently in /var/lib/bluetooth/<adapter>/")
    parser.add_argument("--new-mac", help="New MAC the device is currently presenting")
    parser.add_argument("--ediv-hex", help="EDIV in hex, as shown in the Windows .reg export (e.g. 3f07)")
    parser.add_argument("--rand-hex", help="Rand/ERand in hex, as a single 64-bit hex value "
                                            "(e.g. d69a73aef5bef7fe)")
    parser.add_argument("--keep-old", action="store_true",
                        help="Keep the old device folder instead of leaving it untouched by default "
                             "(default behavior already keeps it; this flag is a no-op kept for clarity)")
    parser.add_argument("--delete-old", action="store_true",
                        help="Delete the old device folder after a successful copy+patch")
    args = parser.parse_args()

    require_root()

    print("=" * 70)
    print(" BLE bonding key re-sync (Windows <-> Linux/BlueZ)")
    print("=" * 70)

    adapter_dir = find_adapter_dir(args.adapter)

    old_mac = normalize_mac(args.old_mac) if args.old_mac else normalize_mac(
        input("Old (currently bonded) device MAC, e.g. 68:78:56:11:34:9B: ")
    )
    new_mac = normalize_mac(args.new_mac) if args.new_mac else normalize_mac(
        input("New (currently presented) device MAC, e.g. 68:78:56:11:35:AD: ")
    )

    if old_mac == new_mac:
        fail("Old MAC and new MAC are identical -- nothing to re-sync.")

    old_dir = os.path.join(adapter_dir, old_mac)
    new_dir = os.path.join(adapter_dir, new_mac)

    if not os.path.isdir(old_dir):
        fail(f"Old device folder not found: {old_dir}\n"
             f"       Run 'sudo ls {adapter_dir}' to see what's actually bonded.")

    if os.path.isdir(new_dir):
        fail(f"New device folder already exists: {new_dir}\n"
             f"       It may already be synced -- check with 'bluetoothctl info {new_mac}' first.")

    ediv_hex = args.ediv_hex or input("EDIV in hex (from Windows .reg, e.g. 3f07): ")
    rand_hex = args.rand_hex or input("Rand/ERand in hex, 64-bit value (e.g. d69a73aef5bef7fe): ")

    ediv_dec = hex_to_decimal("EDIV", ediv_hex)
    rand_dec = hex_to_decimal("Rand", rand_hex)

    info(f"Copying {old_dir} -> {new_dir}")
    shutil.copytree(old_dir, new_dir)
    ok("Folder copied.")

    patch_info_file(os.path.join(new_dir, "info"), ediv_dec, rand_dec)

    restart_bluetooth()
    verify_device(new_mac)

    if args.delete_old:
        info(f"Deleting old folder {old_dir} (--delete-old was set)")
        shutil.rmtree(old_dir)
        ok("Old folder removed.")
    else:
        info(f"Old folder left in place: {old_dir} (pass --delete-old next time to auto-clean it)")

    print("\n" + "=" * 70)
    print(" Done. If the device shows Connected: yes above, you're all set.")
    print(" If not, try: bluetoothctl connect " + new_mac)
    print("=" * 70)


if __name__ == "__main__":
    main()
