"""
tests/test_import_keys_written.py

Regression: importing a bond into a device with no existing info file must
write the secret Key= lines. A `config.setdefault(section, {})["Key"] = ...`
pattern silently dropped them (the BLE mouse then got a pairing prompt).
"""

from pathlib import Path
import configparser
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backends.linux_backend import LinuxBluetoothBackend
from models import BondKey

ADAPTER = "2C:0D:A7:1E:47:7B"
DEVICE = "EE:EA:3D:D7:99:DF"
LTK = "00112233445566778899aabbccddeeff"
IRK = "ffeeddccbbaa99887766554433221100"


def _import(**bond_kwargs) -> configparser.ConfigParser:
    with tempfile.TemporaryDirectory() as tmp:
        backend = LinuxBluetoothBackend(bluetooth_dir=Path(tmp))
        bond = BondKey(adapter_mac=ADAPTER, device_mac=DEVICE, ltk_hex=LTK,
                       ediv=11116, erand=14985392592389889863, **bond_kwargs)
        info_path = backend.import_bond_key(bond)
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read(info_path)
        return cfg


def test_ble_import_writes_ltk_key_on_fresh_device():
    cfg = _import(is_le=True)
    assert (cfg["LongTermKey"].get("Key") or "").lower() == LTK
    assert cfg["LongTermKey"]["EDiv"] == "11116"
    assert cfg["LongTermKey"]["Rand"] == "14985392592389889863"


def test_ble_import_writes_irk_key_on_fresh_device():
    cfg = _import(is_le=True, irk_hex=IRK)
    assert (cfg["IdentityResolvingKey"].get("Key") or "").lower() == IRK


def test_classic_import_writes_link_key_on_fresh_device():
    cfg = _import(is_le=False)
    assert (cfg["LinkKey"].get("Key") or "").lower() == LTK


IMPORT_KEY_TESTS = [
    test_ble_import_writes_ltk_key_on_fresh_device,
    test_ble_import_writes_irk_key_on_fresh_device,
    test_classic_import_writes_link_key_on_fresh_device,
]
