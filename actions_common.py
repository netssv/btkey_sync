"""actions_common.py — Shared helpers for synchronization flows."""

from __future__ import annotations

import sys
from pathlib import Path

from backends import LinuxBluetoothBackend, WindowsBluetoothBackend
from models import BondKey, DiscoveredDevice
from platform_detect import OSKind, gather_environment_info
import tui_helpers as tui


def get_backend(os_kind: OSKind, bluetooth_dir: Path | None = None):
    if os_kind == OSKind.WINDOWS:
        return WindowsBluetoothBackend()
    if os_kind == OSKind.LINUX:
        if bluetooth_dir:
            return LinuxBluetoothBackend(bluetooth_dir=bluetooth_dir)
        return LinuxBluetoothBackend()
    raise RuntimeError(f"No backend available for: {os_kind}")


def detect_and_validate(os_kind_override: OSKind | None = None,
                        bluetooth_dir: Path | None = None) -> tuple:
    env = gather_environment_info()
    effective_os = os_kind_override or env.os_kind

    print(f"  {tui.dim('Running OS   :')} {tui.bold(env.os_kind.value)}  {tui.dim('Python:')} {env.python_version}")
    if os_kind_override and os_kind_override != env.os_kind:
        print(f"  {tui.dim('Source target :')} {tui.bold(tui.magenta(os_kind_override.value))} {tui.dim('(cross-OS)')}")

    for note in env.notes:
        tui.warn(note)

    if env.os_kind == OSKind.UNKNOWN:
        tui.err("Unsupported operating system.")
        sys.exit(1)

    backend = get_backend(effective_os, bluetooth_dir=bluetooth_dir)
    problems = backend.check_prerequisites()
    if problems:
        tui.header("Missing prerequisites")
        for p in problems:
            tui.err(p)
        sys.exit(1)

    return env, backend


def _detect_windows_mounts() -> list[str]:
    """Scans mounted filesystems to find potential Windows installations."""
    suggestions = []
    # 1. Check actively mounted drives from /proc/mounts
    try:
        if Path("/proc/mounts").exists():
            for line in Path("/proc/mounts").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[2] in ("ntfs", "fuseblk", "msdos", "vfat"):
                    p = Path(parts[1])
                    if (p / "Windows/System32").exists() or (p / "windows/system32").exists():
                        suggestions.append(parts[1])
    except Exception:
        pass

    # 2. Check common mount directories if none found active
    if not suggestions:
        for common_dir in ("/mnt", "/media"):
            p = Path(common_dir)
            if p.exists():
                for sub in p.iterdir():
                    if sub.is_dir():
                        if (sub / "Windows/System32").exists() or (sub / "windows/system32").exists():
                            suggestions.append(str(sub))
    return suggestions


def prompt_source_os(running_os: OSKind) -> tuple[OSKind, Path | None]:
    if running_os != OSKind.LINUX:
        return running_os, None

    print(f"\n  {tui.bold('Where do you want to extract bonding keys from?')}\n")
    print(f"    {tui.cyan('[1]')}  {tui.bold('This Linux system')}  "
          f"{tui.dim('(/var/lib/bluetooth — default)')}")
    print(f"    {tui.cyan('[2]')}  {tui.bold('A mounted Windows partition')}  "
          f"{tui.dim('(read HKLM registry hive from disk)')}")
    print()

    choice = tui.ask("Source", default="1")
    if choice == "2":
        print(f"    reged / chntpw required (see REQUIREMENTS.md)")
        mounts = _detect_windows_mounts()
        default_mount = "/mnt/windows"
        if mounts:
            tui.info("Detected Windows partition(s) mounted at:")
            for m in mounts:
                print(f"      • {tui.bold(m)}")
            default_mount = mounts[0]

        mount = tui.ask("Mount point of the Windows partition", default=default_mount)
        mount_path = Path(mount)
        if not mount_path.exists():
            tui.err(f"Mount point not found: {mount_path}")
            # Try to help user by listing storage blocks
            tui.info("Check partition list with 'lsblk' and mount it, e.g.:")
            print(f"    {tui.cyan('sudo mkdir -p /mnt/windows && sudo mount /dev/sdXN /mnt/windows')}")
            return running_os, None
        return OSKind.WINDOWS, mount_path

    return running_os, None


def prompt_select_device(devices: list[DiscoveredDevice]) -> DiscoveredDevice | None:
    print(f"\n  {tui.bold('Bonded BLE devices found:')}\n")
    for d in devices:
        name     = d.device_name or tui.dim("(no name)")
        ltk_flag = tui.green("✓ LTK") if d.has_ltk else tui.red("✗ no LTK")
        idx_str  = tui.cyan(f"[{d.index}]")
        print(f"    {idx_str}  {name:<30}  "
              f"{tui.dim('MAC=')}{d.device_mac}  "
              f"{tui.dim('adapter=')}{d.adapter_mac}  {ltk_flag}")

    print()
    while True:
        raw = tui.ask(f"Device index {tui.dim('(or q to cancel)')}")
        if raw.lower() == "q":
            return None
        if raw.isdigit():
            match = next((d for d in devices if d.index == int(raw)), None)
            if match:
                if not match.has_ltk:
                    tui.warn("This device has no LTK — may not be a BLE bonded device.")
                return match
        tui.warn("Invalid index, try again.")


def prompt_select_export_file() -> Path | None:
    from storage import list_exports, ensure_exports_dir
    exports = list_exports()
    if exports:
        print(f"\n  {tui.bold('Available export files:')}\n")
        for i, p in enumerate(exports, 1):
            ts = tui.dim(p.stem.split("__")[-1] if "__" in p.stem else "")
            print(f"    {tui.cyan(f'[{i}]')}  {p.name}  {ts}")
        print(f"    {tui.cyan('[0]')}  Enter a custom path\n")
        raw = tui.ask("File number")
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(exports):
                return exports[idx - 1]
    else:
        tui.warn("No export files found in exports/ directory.")

    custom = tui.ask("Path to .reg file")
    if not custom:
        return None
    p = Path(custom)
    if not p.is_absolute():
        candidate = ensure_exports_dir() / p
        p = candidate if candidate.exists() else p
    return p


def print_bond_summary(bond: BondKey) -> None:
    lines = bond.summary().splitlines()
    print()
    for line in lines:
        if "LTK" in line:
            print(f"  {tui.yellow(line)}")
        elif "MAC" in line:
            print(f"  {tui.cyan(line)}")
        else:
            print(f"  {line}")


def find_windows_system_hive(win_mount: Path | None) -> Path | None:
    """Case-insensitively locates the Windows SYSTEM registry hive on disk."""
    import os
    if not win_mount:
        return None
    win_dir = next((win_mount / d for d in os.listdir(win_mount) if d.lower() == "windows" and (win_mount / d).is_dir()), None)
    if win_dir:
        sys32_dir = next((win_dir / d for d in os.listdir(win_dir) if d.lower() == "system32" and (win_dir / d).is_dir()), None)
        if sys32_dir:
            cfg_dir = next((sys32_dir / d for d in os.listdir(sys32_dir) if d.lower() == "config" and (sys32_dir / d).is_dir()), None)
            if cfg_dir:
                return next((cfg_dir / f for f in os.listdir(cfg_dir) if f.lower() == "system" and (cfg_dir / f).is_file()), None)
    return None



# Windows-partition helpers live in actions_windows to keep this file under 200 lines.
from actions_windows import run_offline_windows_export, pick_bond_from_windows  # noqa: E402
