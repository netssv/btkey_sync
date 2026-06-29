"""
importers/classic_importer.py

Reads a Classic (BR/EDR) .reg export and converts it into a LinkKeyBond,
ready to be passed to the Linux backend's write_classic_info().
"""

from __future__ import annotations

from pathlib import Path

from backends.windows_common import parse_classic_from_reg_text
from models import LinkKeyBond
from storage import load_classic_bond_from_metadata


def load_classic_bond_from_reg_file(path: Path) -> LinkKeyBond:
    """
    Parse a .reg file produced by export_classic_bond() and return the
    first LinkKeyBond found in it.  Raises ValueError if none found.
    """
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    bonds = parse_classic_from_reg_text(text)
    if not bonds:
        raise ValueError(
            f"No Classic (BR/EDR) bonding key found in: {path}\n"
            "Check that the file is a Classic export (plain hex value under "
            "the adapter key), not an LE export (device subkey)."
        )
    return bonds[0]


def load_classic_bond_from_json(path: Path) -> LinkKeyBond:
    """Load a Classic bond from a .json metadata sidecar."""
    return load_classic_bond_from_metadata(path)
