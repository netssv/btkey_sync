"""
backends/offline_windows_classic.py

Reads Classic (BR/EDR) Bluetooth bonding keys from a MOUNTED Windows SYSTEM hive.
Respects 200-line file limit (AGENTS.md R14).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

from models import LinkKeyBond
from backends.windows_common import parse_classic_from_reg_text
from backends.offline_windows import (
    reged_available,
    detect_active_control_set,
    _run_reged_export,
    _read_reg_file,
    _extract_device_metadata,
    BT_KEYS_REG_SUBKEY,
    BT_DEVS_REG_SUBKEY,
)


def extract_classic_bt_keys_from_hive(hive_path: Path) -> List[LinkKeyBond]:
    """
    Extract all Classic (BR/EDR) bonding keys from an offline Windows SYSTEM hive.
    """
    if not reged_available():
        raise RuntimeError(
            "reged not found. Install chntpw: `pacman -S chntpw` or `apt install chntpw`."
        )

    control_set = detect_active_control_set(hive_path)
    keys_subkey = f"\\{control_set}{BT_KEYS_REG_SUBKEY}"
    devs_subkey = f"\\{control_set}{BT_DEVS_REG_SUBKEY}"

    with tempfile.TemporaryDirectory() as tmpdir:
        keys_reg = Path(tmpdir) / "bt_keys.reg"
        devs_reg = Path(tmpdir) / "bt_devs.reg"

        ok = _run_reged_export(hive_path, "HKEY_LOCAL_MACHINE\\SYSTEM", keys_subkey, keys_reg)
        if not ok:
            raise RuntimeError(f"reged failed to export keys from {hive_path}.")

        text = _read_reg_file(keys_reg)
        bonds = parse_classic_from_reg_text(text)

        # Enrich with names from Devices subkey
        _run_reged_export(hive_path, "HKEY_LOCAL_MACHINE\\SYSTEM", devs_subkey, devs_reg)
        if devs_reg.exists():
            meta_map = _extract_device_metadata(devs_reg)
            for b in bonds:
                mac_nodelim = b.device_mac.replace(":", "").lower()
                meta = meta_map.get(mac_nodelim, {})
                if meta.get("name"):
                    b.device_name = meta["name"]
                if meta.get("cod"):
                    b.device_class = f"0x{meta['cod']:06x}"

    return bonds
