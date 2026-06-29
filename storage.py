"""
storage.py

Step 4 of the flow: manages the special exports folder, ALWAYS located
relative to the package location (not the cwd from which the script is invoked),
so that no matter where you run `python -m btkey_sync` the files end up in a
predictable place and are easy to copy between partitions (e.g. a folder you mount
on both OSes, or a USB drive).

Naming convention:
    exports/<device_mac>__<device_name_slug>__<source_os>__<timestamp>.reg
    exports/<device_mac>__<device_name_slug>__<source_os>__<timestamp>.json

    If the device has no name, the name slug segment is omitted:
    exports/<device_mac>__<source_os>__<timestamp>.reg
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from models import BondKey, LinkKeyBond

PROJECT_ROOT = Path(__file__).resolve().parent
EXPORTS_DIR = PROJECT_ROOT / "exports"


def ensure_exports_dir() -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORTS_DIR


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sanitize_name(name: str) -> str:
    """Convert a device name into a clean, filesystem-safe slug."""
    import re
    slug = re.sub(r"[^\w]+", "_", name.strip()).strip("_")
    return slug[:32]  # cap length to keep filenames reasonable


def build_export_basename(bond: BondKey) -> str:
    mac_clean = bond.device_mac.replace(":", "")
    os_slug = bond.source_os or "unknown"
    ts = _timestamp()
    if bond.device_name and bond.device_name.strip():
        name_slug = _sanitize_name(bond.device_name)
        return f"{mac_clean}__{name_slug}__{os_slug}__{ts}"
    return f"{mac_clean}__{os_slug}__{ts}"


def write_reg_export(bond: BondKey, reg_content: str) -> Path:
    """Writes the .reg ready to be used in Windows (or as a universal reference)."""
    ensure_exports_dir()
    basename = build_export_basename(bond)
    reg_path = EXPORTS_DIR / f"{basename}.reg"
    reg_path.write_text(reg_content, encoding="utf-8")
    _write_metadata_sidecar(bond, reg_path)
    return reg_path


def _write_metadata_sidecar(bond: BondKey, primary_path: Path) -> Path:
    """
    Saves a .json alongside the .reg with all fields in readable plain text.
    This is what the script on the OTHER system (or a human) can inspect
    without having to parse the binary/hex format of a .reg.
    """
    meta_path = primary_path.with_suffix(".json")
    meta = {
        "adapter_mac": bond.adapter_mac,
        "device_mac": bond.device_mac,
        "device_name": bond.device_name,
        "ltk_hex": bond.ltk_hex,
        "ediv": bond.ediv,
        "erand": bond.erand,
        "auth_req": bond.auth_req,
        "key_length": bond.key_length,
        "source_os": bond.source_os,
        "irk_hex": bond.irk_hex,
        "address_type": bond.address_type,
        "is_le": bond.is_le,
        "extracted_at": bond.extracted_at.isoformat(),
        "note": (
            "ediv and erand are in DECIMAL. Windows expects them in hex within the "
            ".reg; BlueZ (Linux) expects them in decimal within /var/lib/bluetooth/.../info. "
            "This project performs that conversion automatically -- do not edit manually unless necessary."
        ),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta_path


def list_exports() -> list[Path]:
    ensure_exports_dir()
    return sorted(EXPORTS_DIR.glob("*.reg"))


def load_bond_from_metadata(json_path: Path) -> BondKey:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return BondKey(
        adapter_mac=data["adapter_mac"],
        device_mac=data["device_mac"],
        ltk_hex=data["ltk_hex"],
        ediv=data["ediv"],
        erand=data["erand"],
        auth_req=data.get("auth_req"),
        key_length=data.get("key_length", 16),
        device_name=data.get("device_name"),
        source_os=data.get("source_os"),
        irk_hex=data.get("irk_hex"),
        address_type=data.get("address_type"),
        is_le=data.get("is_le"),
    )


# ── Classic (BR/EDR) storage helpers ────────────────────────────────────

def build_classic_export_basename(bond: LinkKeyBond) -> str:
    mac_clean = bond.device_mac.replace(":", "")
    os_slug = bond.source_os or "unknown"
    ts = _timestamp()
    if bond.device_name and bond.device_name.strip():
        name_slug = _sanitize_name(bond.device_name)
        return f"{mac_clean}__{name_slug}__classic__{os_slug}__{ts}"
    return f"{mac_clean}__classic__{os_slug}__{ts}"


def write_classic_export(bond: LinkKeyBond, reg_content: str) -> Path:
    """Write a Classic .reg export + JSON sidecar to the exports/ folder."""
    ensure_exports_dir()
    basename = build_classic_export_basename(bond)
    reg_path = EXPORTS_DIR / f"{basename}.reg"
    reg_path.write_text(reg_content, encoding="utf-8")
    _write_classic_metadata_sidecar(bond, reg_path)
    return reg_path


def _write_classic_metadata_sidecar(bond: LinkKeyBond, primary_path: Path) -> Path:
    meta_path = primary_path.with_suffix(".json")
    meta = {
        "transport":    "classic",
        "adapter_mac":  bond.adapter_mac,
        "device_mac":   bond.device_mac,
        "device_name":  bond.device_name,
        "link_key_hex": bond.link_key_hex,
        "key_type":     bond.key_type,
        "pin_length":   bond.pin_length,
        "device_class": bond.device_class,
        "device_id":    bond.device_id,
        "service_uuids":bond.service_uuids,
        "source_os":    bond.source_os,
        "extracted_at": bond.extracted_at.isoformat(),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    return meta_path


def load_classic_bond_from_metadata(json_path: Path) -> LinkKeyBond:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return LinkKeyBond(
        adapter_mac=data["adapter_mac"],
        device_mac=data["device_mac"],
        link_key_hex=data["link_key_hex"],
        key_type=data["key_type"],
        pin_length=data["pin_length"],
        device_class=data.get("device_class", ""),
        device_name=data.get("device_name"),
        device_id=data.get("device_id"),
        service_uuids=data.get("service_uuids", []),
        source_os=data.get("source_os"),
    )
