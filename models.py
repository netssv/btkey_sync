"""
models.py

Core data structures for the project. Every backend (Windows/Linux)
converts its native format to/from BondKey, so the rest of the system
(exporters, importers, CLI) never needs to know Windows registry
details or BlueZ INI file internals directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class BondKey:
    """
    Represents the cryptographic material of a BLE bonding (LE Secure
    Connections / LE Legacy Pairing), independent of the OS it originated from.

    All numeric fields are stored in DECIMAL internally.
    Each backend is responsible for converting from/to its own native format
    (Windows uses hex in the registry, BlueZ uses decimal in INI files).
    """

    adapter_mac: str          # MAC del adaptador Bluetooth local, formato XX:XX:XX:XX:XX:XX
    device_mac: str           # MAC del dispositivo remoto (puede rotar por RPA)
    ltk_hex: str               # Long Term Key, 32 caracteres hex (16 bytes), MAYÚSCULAS
    ediv: int                  # Encrypted Diversifier, decimal
    erand: int                 # Encrypted Random, decimal (puede ser un QWORD grande)
    auth_req: Optional[int] = None
    key_length: int = 16
    device_name: Optional[str] = None
    source_os: Optional[str] = None       # "windows" | "linux"
    irk_hex: Optional[str] = None          # Identity Resolving Key, 32 characters hex
    address_type: Optional[int] = None     # 0 = public, 1 = static/random (Windows format)
    class_of_device: Optional[int] = None  # Class of Device (CoD) for BR/EDR devices
    is_le: Optional[bool] = None           # True if LE, False if Classic (BR/EDR)
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        self.adapter_mac = self._normalize_mac(self.adapter_mac)
        self.device_mac = self._normalize_mac(self.device_mac)
        self.ltk_hex = self.ltk_hex.upper().replace(":", "").replace(" ", "")
        if self.irk_hex:
            self.irk_hex = self.irk_hex.upper().replace(":", "").replace(" ", "")

        if len(self.ltk_hex) != 32:
            raise ValueError(
                f"LTK must be 32 hex characters (16 bytes), got: {len(self.ltk_hex)}"
            )
        try:
            int(self.ltk_hex, 16)
        except ValueError:
            raise ValueError(f"LTK is not valid hexadecimal: {self.ltk_hex}")

        if self.ediv < 0 or self.ediv > 0xFFFF:
            raise ValueError(f"EDIV out of 16-bit range: {self.ediv}")

        if self.erand < 0 or self.erand > 0xFFFFFFFFFFFFFFFF:
            raise ValueError(f"ERand out of 64-bit range: {self.erand}")

    @staticmethod
    def _normalize_mac(mac: str) -> str:
        """Accepts MACs with or without separators and normalises to XX:XX:XX:XX:XX:XX in upper case."""
        cleaned = mac.replace(":", "").replace("-", "").upper()
        if len(cleaned) != 12:
            raise ValueError(f"Invalid MAC: {mac!r}")
        return ":".join(cleaned[i:i + 2] for i in range(0, 12, 2))

    @property
    def mac_nodelim(self) -> str:
        """Device MAC without separators, lower-case (Windows registry path format)."""
        return self.device_mac.replace(":", "").lower()

    @property
    def adapter_mac_nodelim(self) -> str:
        return self.adapter_mac.replace(":", "").lower()

    def summary(self) -> str:
        s = (
            f"Device: {self.device_name or '(no name)'}\n"
            f"  Adapter MAC : {self.adapter_mac}\n"
            f"  Device MAC  : {self.device_mac}\n"
            f"  LTK         : {self.ltk_hex}\n"
            f"  EDIV        : {self.ediv}\n"
            f"  ERand       : {self.erand}\n"
        )
        if self.irk_hex:
            s += f"  IRK         : {self.irk_hex}\n"
        if self.address_type is not None:
            s += f"  AddressType : {self.address_type} ({'static/random' if self.address_type == 1 else 'public'})\n"
        s += (
            f"  AuthReq     : {self.auth_req}\n"
            f"  Source OS   : {self.source_os}\n"
        )
        return s


@dataclass
class DiscoveredDevice:
    """
    Represents a bonded BLE device as listed by a backend,
    BEFORE deciding which one to export. It is the row shown to the user
    in the interactive menu (step 2 of the flow: 'pick one').
    """
    index: int
    adapter_mac: str
    device_mac: str
    device_name: Optional[str]
    has_ltk: bool
    raw_source_path: str   # registry key or info file path it came from, for debug


@dataclass
class LinkKeyBond:
    """
    Represents the cryptographic material of a Classic (BR/EDR) bonding,
    independent of the OS it originated from.

    Kept intentionally separate from BondKey (LE): the field sets and failure
    semantics are different enough that merging them would hide bugs.
    See CLASSIC_SUPPORT.agent.md §6 and AGENTS.md rule 2.

    key_type and pin_length MUST be read from actual bonding data — never
    assumed or defaulted here. The caller is responsible for supplying them.
    """

    adapter_mac: str
    device_mac: str
    link_key_hex: str         # 32 hex chars / 16 bytes, uppercase, no separators
    key_type: int             # SSP key derivation type — read from bonding data
    pin_length: int           # PIN length — read from bonding data
    device_class: str         # Hex string e.g. "0x002540"; "" if unknown
    device_name: Optional[str] = None
    device_id: Optional[dict] = None   # keys: source, vendor, product, version
    service_uuids: List[str] = field(default_factory=list)
    source_os: Optional[str] = None    # "windows" | "linux"
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        self.adapter_mac = BondKey._normalize_mac(self.adapter_mac)
        self.device_mac = BondKey._normalize_mac(self.device_mac)
        self.link_key_hex = self.link_key_hex.upper().replace(":", "").replace(" ", "")

        if len(self.link_key_hex) != 32:
            raise ValueError(
                f"Link Key must be 32 hex characters (16 bytes), got: {len(self.link_key_hex)}"
            )
        try:
            int(self.link_key_hex, 16)
        except ValueError:
            raise ValueError(f"Link Key is not valid hexadecimal: {self.link_key_hex}")

    @property
    def mac_nodelim(self) -> str:
        """Device MAC without separators, lower-case (Windows registry value name format)."""
        return self.device_mac.replace(":", "").lower()

    @property
    def adapter_mac_nodelim(self) -> str:
        return self.adapter_mac.replace(":", "").lower()

    def summary(self) -> str:
        s = (
            f"Device (Classic BR/EDR): {self.device_name or '(no name)'}\n"
            f"  Adapter MAC   : {self.adapter_mac}\n"
            f"  Device MAC    : {self.device_mac}\n"
            f"  Link Key      : {self.link_key_hex}\n"
            f"  Key Type      : {self.key_type}\n"
            f"  PIN Length    : {self.pin_length}\n"
            f"  Device Class  : {self.device_class}\n"
        )
        if self.device_id:
            s += (
                f"  DeviceID      : Source={self.device_id.get('source', '?')} "
                f"Vendor={self.device_id.get('vendor', '?')} "
                f"Product={self.device_id.get('product', '?')} "
                f"Version={self.device_id.get('version', '?')}\n"
            )
        if self.service_uuids:
            s += f"  Services      : {'; '.join(self.service_uuids)}\n"
        s += f"  Source OS     : {self.source_os}\n"
        return s

