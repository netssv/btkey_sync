"""
backends/offline_windows.py
Reads Bluetooth bonding keys from a MOUNTED Windows partition SYSTEM hive.
Uses `reged` from `chntpw`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from models import BondKey
from backends.windows_common import parse_all_bonds_from_reg_text

REGED_BINARY = "reged"

BT_KEYS_REG_SUBKEY = r"\Services\BTHPORT\Parameters\Keys"
BT_DEVS_REG_SUBKEY = r"\Services\BTHPORT\Parameters\Devices"
SELECT_SUBKEY = r"\Select"


def reged_available() -> bool:
    """Return True if reged (chntpw) is installed and reachable."""
    return shutil.which(REGED_BINARY) is not None


def _run_reged_export(hive_path: Path, root_key: str, subkey: str, out_path: Path) -> bool:
    """Export a subkey from an offline hive using reged. Return True on success."""
    cmd = [REGED_BINARY, "-x", str(hive_path), root_key, subkey, str(out_path)]
    subprocess.run(cmd, capture_output=True, text=True)
    return out_path.exists() and out_path.stat().st_size > 0


def detect_active_control_set(hive_path: Path) -> str:
    """
    Read SYSTEM\\Select\\Current from the offline hive to find the active ControlSet.
    Returns e.g. 'ControlSet001'. Falls back to 'ControlSet001' on any error.
    """
    with tempfile.NamedTemporaryFile(suffix=".reg", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        ok = _run_reged_export(hive_path, "HKEY_LOCAL_MACHINE\\SYSTEM", SELECT_SUBKEY, tmp_path)
        if ok:
            text = tmp_path.read_text(encoding="utf-16-le", errors="ignore")
            # Look for: "Current"=dword:00000001
            m = re.search(r'"Current"=dword:([0-9a-fA-F]+)', text)
            if m:
                n = int(m.group(1), 16)
                return f"ControlSet{n:03d}"
    except Exception:
        pass
    finally:
        tmp_path.unlink(missing_ok=True)

    return "ControlSet001"  # safe fallback


def extract_bt_keys_from_hive(hive_path: Path) -> List[BondKey]:
    """
    Extract all BLE bonding keys from an offline Windows SYSTEM hive using reged.

    Steps:
      1. Detect the active ControlSet.
      2. Export BTHPORT\\Parameters\\Keys to a temp .reg.
      3. Parse it with the existing parse_windows_reg_text().
      4. Try to enrich device names from the Devices subkey.
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
            raise RuntimeError(
                f"reged failed to export keys from {hive_path}. "
                "Make sure the hive is not locked (Windows fast-startup must be disabled)."
            )

        text = _read_reg_file(keys_reg)
        bonds = parse_all_bonds_from_reg_text(text)

        # Optionally enrich with device metadata from Devices subkey
        _run_reged_export(hive_path, "HKEY_LOCAL_MACHINE\\SYSTEM", devs_subkey, devs_reg)
        if devs_reg.exists():
            meta_map = _extract_device_metadata(devs_reg)
            bonds = [
                BondKey(
                    adapter_mac=b.adapter_mac,
                    device_mac=b.device_mac,
                    ltk_hex=b.ltk_hex,
                    ediv=b.ediv,
                    erand=b.erand,
                    auth_req=b.auth_req,
                    key_length=b.key_length,
                    irk_hex=b.irk_hex,
                    address_type=b.address_type,
                    device_name=meta_map.get(b.device_mac.replace(":", "").lower(), {}).get("name", b.device_name),
                    class_of_device=meta_map.get(b.device_mac.replace(":", "").lower(), {}).get("cod", b.class_of_device),
                    source_os="windows",
                    is_le=b.is_le,
                )
                for b in bonds
            ]

    return bonds


def _read_reg_file(path: Path) -> str:
    """Robustly read registry files which can be UTF-8 (reged on Linux) or UTF-16 (Windows export)."""
    b = path.read_bytes()
    if b.startswith(b"\xff\xfe"):
        return b.decode("utf-16-le", errors="ignore")
    if b.startswith(b"\xfe\xff"):
        return b.decode("utf-16-be", errors="ignore")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("utf-16-le", errors="ignore")


def _extract_device_metadata(devs_reg: Path) -> dict:
    """Parse Devices .reg export for name and class of device (COD)."""
    meta_map: dict = {}
    try:
        text = _read_reg_file(devs_reg)
    except Exception:
        return meta_map

    lines, acc = [], ""
    for line in text.splitlines():
        ls = line.strip()
        if ls.endswith("\\"):
            acc += ls[:-1]
        else:
            acc += ls
            lines.append(acc)
            acc = ""
    if acc:
        lines.append(acc)

    sec_re = re.compile(r'\[HKEY_LOCAL_MACHINE\\.*?\\Services\\BTHPORT\\Parameters\\Devices\\([0-9a-fA-F]{12})\]')
    val_re = re.compile(r'"(?:FriendlyName|Name)"=(?:hex(?:\(1\))?:([0-9a-fA-F,\s]+)|"([^"]+)")')
    cod_re = re.compile(r'"COD"=dword:([0-9a-fA-F]+)')

    curr_mac: Optional[str] = None
    for line in lines:
        sm = sec_re.search(line)
        if sm:
            curr_mac = sm.group(1).lower()
            if curr_mac not in meta_map:
                meta_map[curr_mac] = {"name": None, "cod": None}
            continue
        # If we see any other section header, stop capturing for the current device
        if line.startswith("[") and "Services\\BTHPORT\\Parameters\\Devices\\" not in line:
            curr_mac = None
            continue
        if curr_mac:
            vm = val_re.search(line)
            if vm:
                hex_str, plain_str = vm.groups()
                name = None
                if plain_str:
                    name = plain_str
                elif hex_str:
                    h = hex_str.replace(",", "").replace(" ", "").replace("\t", "")
                    if h and h != "00":
                        try:
                            b = bytes.fromhex(h)
                            s = b.decode("utf-8")
                            printable = sum(32 <= ord(c) < 127 for c in s)
                            name = s.rstrip("\x00") if (len(s) > 0 and (printable / len(s)) > 0.7) else b.decode("utf-16-le", errors="ignore").rstrip("\x00")
                        except Exception:
                            pass
                if name:
                    meta_map[curr_mac]["name"] = name
            cm = cod_re.search(line)
            if cm:
                try:
                    meta_map[curr_mac]["cod"] = int(cm.group(1), 16)
                except Exception:
                    pass
    return meta_map
