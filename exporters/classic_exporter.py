"""
exporters/classic_exporter.py

Serialises a LinkKeyBond to a Windows .reg file for Classic (BR/EDR) devices.

On Windows, a Classic bond is a plain REG_BINARY value directly under the
adapter key — NOT a subkey (RC4). The value name is the device MAC
(12 hex chars, lowercase, no separators).
"""

from __future__ import annotations

from pathlib import Path

from models import LinkKeyBond
from storage import write_classic_export


def build_classic_reg_content(bond: LinkKeyBond,
                               target_device_mac: str | None = None) -> str:
    """
    Build the Windows .reg text for a Classic (BR/EDR) bonding entry.

    Structure (RC4):
        [HKLM\\...\\Keys\\<adapter_nodelim>]
        "<device_mac_nodelim>"=hex:<link_key_bytes>

    Note: NO subkey is created for the device — this is the structural
    difference between LE (subkey) and Classic (plain value).
    """
    adapter_nodelim = bond.adapter_mac_nodelim
    device_nodelim = (
        (target_device_mac or bond.device_mac).replace(":", "").lower()
    )

    # Convert 32-char hex string to comma-separated lowercase byte pairs
    key_hex = bond.link_key_hex.lower()
    key_bytes = ",".join(key_hex[i:i + 2] for i in range(0, len(key_hex), 2))

    lines = [
        "Windows Registry Editor Version 5.00",
        "",
        (
            f"[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT"
            f"\\Parameters\\Keys\\{adapter_nodelim}]"
        ),
        f'"{device_nodelim}"=hex:{key_bytes}',
    ]
    return "\r\n".join(lines) + "\r\n"


def export_classic_bond(bond: LinkKeyBond,
                        target_device_mac: str | None = None) -> Path:
    """
    Serialise a Classic bond to the exports/ folder (.reg + .json sidecar).
    Returns the path of the written .reg file.
    """
    content = build_classic_reg_content(bond, target_device_mac=target_device_mac)
    return write_classic_export(bond, content)
