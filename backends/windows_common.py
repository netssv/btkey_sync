"""
backends/windows_common.py
Shared code and utilities for Windows backends.
"""

from __future__ import annotations
import platform
import re
from typing import List, Optional
from models import BondKey, LinkKeyBond

REG_KEY_PATH = r"HKLM\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys"

# Matches both CurrentControlSet and ControlSet001/002/... (from reged -x output)
_SECTION_RE = re.compile(
    r"\[HKEY_LOCAL_MACHINE\\SYSTEM\\(?:CurrentControlSet|ControlSet\d+)"
    r"\\Services\\BTHPORT\\Parameters\\Keys\\"
    r"([0-9A-Fa-f]{12})\\([0-9A-Fa-f]{12})\]"
)


def ensure_windows() -> None:
    if platform.system().lower() != "windows":
        raise RuntimeError(
            "This operation can only be executed on Windows. "
            "You are running on: " + platform.system()
        )


def _fmt_mac(raw: str) -> str:
    return ":".join(raw[i:i + 2].upper() for i in range(0, 12, 2))


def _parse_erand(block: str) -> int:
    """Parse ERand from a .reg block; handles hex(b), qword, and dword formats."""
    m = re.search(r'"ERand"=hex\(b\):([0-9A-Fa-f,]+)', block)
    if m:
        return int.from_bytes(bytes(int(b, 16) for b in m.group(1).split(",")), "little")
    m = re.search(r'"ERand"=qword:([0-9A-Fa-f]+)', block)
    if m:
        return int(m.group(1), 16)
    m = re.search(r'"ERand"=dword:([0-9A-Fa-f]+)', block)
    if m:
        return int(m.group(1), 16)
    raise ValueError("Could not find ERand in the .reg block")


def _parse_bond_section(adapter_raw: str, device_raw: str, block: str,
                        name: Optional[str] = None) -> Optional[BondKey]:
    """Parse one [Keys\\adapter\\device] section into a BondKey. Returns None if no LTK."""
    ltk_m = re.search(r'"LTK"=hex:([0-9A-Fa-f,]+)', block)
    if not ltk_m:
        return None
    ltk_hex = "".join(b.upper() for b in ltk_m.group(1).split(","))
    ediv_m = re.search(r'"EDIV"=dword:([0-9A-Fa-f]+)', block)
    ediv = int(ediv_m.group(1), 16) if ediv_m else 0
    try:
        erand = _parse_erand(block)
    except ValueError:
        erand = 0
    authreq_m = re.search(r'"AuthReq"=dword:([0-9A-Fa-f]+)', block)
    irk_m = re.search(r'"IRK"=hex:([0-9A-Fa-f,]+)', block)
    irk_hex = "".join(b.upper() for b in irk_m.group(1).split(",")) if irk_m else None
    addr_type_m = re.search(r'"AddressType"=dword:([0-9A-Fa-f]+)', block)
    address_type = int(addr_type_m.group(1), 16) if addr_type_m else None

    return BondKey(
        adapter_mac=_fmt_mac(adapter_raw),
        device_mac=_fmt_mac(device_raw),
        ltk_hex=ltk_hex,
        ediv=ediv,
        erand=erand,
        auth_req=int(authreq_m.group(1), 16) if authreq_m else None,
        device_name=name,
        source_os="windows",
        irk_hex=irk_hex,
        address_type=address_type,
        is_le=True,
    )


_ADAPTER_SECTION_RE = re.compile(
    r"\[HKEY_LOCAL_MACHINE\\SYSTEM\\(?:CurrentControlSet|ControlSet\d+)"
    r"\\Services\\BTHPORT\\Parameters\\Keys\\"
    r"([0-9A-Fa-f]{12})\]"
)

_CLASSIC_KEY_RE = re.compile(
    r'"([0-9A-Fa-f]{12})"=hex:([0-9A-Fa-f,]+)'
)


def parse_windows_reg_text(text: str) -> BondKey:
    """Parse a single-device .reg export. Raises ValueError if not found."""
    bonds = parse_all_bonds_from_reg_text(text)
    if not bonds:
        raise ValueError("Could not find a valid BTHPORT\\Parameters\\Keys entry in the .reg")
    return bonds[0]


def parse_all_bonds_from_reg_text(text: str) -> List[BondKey]:
    """Parse ALL BLE and Classic bonding keys from a .reg dump."""
    bonds: List[BondKey] = []
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        adapter_raw, device_raw = m.groups()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start():end]
        bond = _parse_bond_section(adapter_raw, device_raw, block)
        if bond:
            bonds.append(bond)

    adapter_matches = list(_ADAPTER_SECTION_RE.finditer(text))
    for i, am in enumerate(adapter_matches):
        adapter_raw = am.group(1)
        end = adapter_matches[i + 1].start() if i + 1 < len(adapter_matches) else len(text)
        block = text[am.start():end]
        for device_raw, key_bytes in _CLASSIC_KEY_RE.findall(block):
            if not any(b.device_mac.replace(":", "").upper() == device_raw.upper() for b in bonds):
                ltk_hex = "".join(b.upper() for b in key_bytes.split(","))
                bonds.append(
                    BondKey(
                        adapter_mac=_fmt_mac(adapter_raw),
                        device_mac=_fmt_mac(device_raw),
                        ltk_hex=ltk_hex,
                        ediv=0,
                        erand=0,
                        auth_req=None,
                        device_name=None,
                        source_os="windows",
                        is_le=False,
                    )
                )
    return bonds


def parse_classic_from_reg_text(text: str) -> List[LinkKeyBond]:
    """
    Parse Classic (BR/EDR) bonding entries from a Windows .reg dump.

    On Windows, a Classic-bonded device appears as a plain REG_BINARY value
    directly under the adapter key — NOT as a subkey. The value name is the
    device MAC (12 hex chars, no separators, lowercase), and the value holds
    the 16-byte Link Key.

    key_type and pin_length are set to 0 here because Windows does not store
    them in the registry — the correct values live in the BlueZ info file on
    the Linux side and must be read/preserved there. The caller is responsible
    for not treating these zeros as authoritative.
    """
    bonds: List[LinkKeyBond] = []
    seen_device_macs: set[str] = set()

    # First collect all LE device MACs so we don't double-count dual-mode devices
    for m in _SECTION_RE.finditer(text):
        seen_device_macs.add(m.group(2).upper())

    for am in _ADAPTER_SECTION_RE.finditer(text):
        adapter_raw = am.group(1)
        # Slice block up to the next adapter section or end of text
        next_am = _ADAPTER_SECTION_RE.search(text, am.end())
        block_end = next_am.start() if next_am else len(text)
        block = text[am.start():block_end]

        for device_raw, key_bytes_str in _CLASSIC_KEY_RE.findall(block):
            if device_raw.upper() in seen_device_macs:
                continue  # already captured as LE subkey — skip
            link_key_hex = "".join(b.upper() for b in key_bytes_str.split(","))
            if len(link_key_hex) != 32:
                continue  # malformed — skip silently
            bonds.append(
                LinkKeyBond(
                    adapter_mac=_fmt_mac(adapter_raw),
                    device_mac=_fmt_mac(device_raw),
                    link_key_hex=link_key_hex,
                    key_type=0,      # unknown at Windows parse time; read from BlueZ on import
                    pin_length=0,    # same caveat
                    device_class="",
                    source_os="windows",
                )
            )
    return bonds
