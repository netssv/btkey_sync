"""
importers/reg_importer.py

Reads a .reg file received from the other side of the dual boot (typically
copied from the exports/ folder of the other OS) and converts it into a
BondKey, ready to be passed to any backend's import_bond_key().
"""

from __future__ import annotations

from pathlib import Path

from backends.windows_common import parse_windows_reg_text
from models import BondKey


def load_bond_from_reg_file(path: Path) -> BondKey:
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    return parse_windows_reg_text(text)
