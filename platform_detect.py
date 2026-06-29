"""
platform_detect.py

Step 1 of the flow: detect which OS we are running on, and expose
helpers that the rest of the project uses to decide which backend to load
and what type of "destination" file to generate for the other dual-boot side.
"""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass
from enum import Enum


class OSKind(str, Enum):
    WINDOWS = "windows"
    LINUX = "linux"
    UNKNOWN = "unknown"


@dataclass
class EnvironmentInfo:
    os_kind: OSKind
    os_release: str
    python_version: str
    is_admin_or_root: bool
    has_bluetoothctl: bool  # solo relevante en Linux
    notes: list


def detect_os() -> OSKind:
    system = platform.system().lower()
    if system == "windows":
        return OSKind.WINDOWS
    if system == "linux":
        return OSKind.LINUX
    return OSKind.UNKNOWN


def _is_admin_or_root(os_kind: OSKind) -> bool:
    if os_kind == OSKind.LINUX:
        import os
        return os.geteuid() == 0
    if os_kind == OSKind.WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return False


def gather_environment_info() -> EnvironmentInfo:
    os_kind = detect_os()
    notes = []

    is_privileged = _is_admin_or_root(os_kind)
    has_bluetoothctl = False

    if os_kind == OSKind.LINUX:
        has_bluetoothctl = shutil.which("bluetoothctl") is not None
        if not has_bluetoothctl:
            notes.append(
                "'bluetoothctl' not found in PATH. Install bluez-utils "
                "(or the equivalent package for your distro) to list/verify devices."
            )
        if not is_privileged:
            notes.append(
                "Not running as root. Reading/writing "
                "/var/lib/bluetooth requires sudo. Run with: sudo python3 -m btkey_sync"
            )

    if os_kind == OSKind.WINDOWS:
        if not is_privileged:
            notes.append(
                "Not running as Administrator. Although the actual extraction is done "
                "via a scheduled task as SYSTEM, you need Administrator privileges "
                "to create that scheduled task."
            )

    if os_kind == OSKind.UNKNOWN:
        notes.append(
            f"Unsupported operating system: {platform.system()}. "
            "This project only supports Windows and Linux."
        )

    return EnvironmentInfo(
        os_kind=os_kind,
        os_release=platform.platform(),
        python_version=sys.version.split()[0],
        is_admin_or_root=is_privileged,
        has_bluetoothctl=has_bluetoothctl,
        notes=notes,
    )


def other_os(os_kind: OSKind) -> OSKind:
    """Given the current OS, returns which one is the 'other side' of the dual boot."""
    if os_kind == OSKind.WINDOWS:
        return OSKind.LINUX
    if os_kind == OSKind.LINUX:
        return OSKind.WINDOWS
    return OSKind.UNKNOWN
