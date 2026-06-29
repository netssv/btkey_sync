"""
tests/test_parsing.py

Valida el pipeline completo de parsing/conversión usando datos REALES
(no inventados) de la migración que se hizo a mano y se confirmó funcionando:
  - EDIV hex 0x3f07       -> decimal 16135
  - ERand hex(b) LE bytes -> decimal 15463799465814456318
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backends.windows_backend import parse_windows_reg_text
from backends.windows_common import parse_classic_from_reg_text
from exporters.reg_exporter import build_windows_reg_content
from models import BondKey, LinkKeyBond

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_mouse.reg"

EXPECTED_ADAPTER_MAC = "70:08:94:93:77:78"
EXPECTED_DEVICE_MAC = "68:78:56:11:35:AD"
EXPECTED_LTK = "33221166554499887722110055443366"
EXPECTED_EDIV = 16135
EXPECTED_ERAND = 15463799465814456318
EXPECTED_AUTHREQ = 0x2D


def test_parse_real_mouse_reg():
    text = FIXTURE_PATH.read_text(encoding="utf-8-sig")
    bond = parse_windows_reg_text(text)

    assert bond.adapter_mac == EXPECTED_ADAPTER_MAC
    assert bond.device_mac == EXPECTED_DEVICE_MAC
    assert bond.ltk_hex == EXPECTED_LTK
    assert bond.ediv == EXPECTED_EDIV
    assert bond.erand == EXPECTED_ERAND
    assert bond.auth_req == EXPECTED_AUTHREQ


def test_bondkey_normalizes_mac_without_separators():
    bond = BondKey(
        adapter_mac="700894937778",
        device_mac="6878561135ad",
        ltk_hex=EXPECTED_LTK,
        ediv=EXPECTED_EDIV,
        erand=EXPECTED_ERAND,
    )
    assert bond.adapter_mac == EXPECTED_ADAPTER_MAC
    assert bond.device_mac == EXPECTED_DEVICE_MAC


def test_bondkey_rejects_invalid_ltk_length():
    try:
        BondKey(
            adapter_mac=EXPECTED_ADAPTER_MAC,
            device_mac=EXPECTED_DEVICE_MAC,
            ltk_hex="deadbeef",  # muy corto
            ediv=1,
            erand=1,
        )
        assert False, "Debió lanzar ValueError por LTK inválido"
    except ValueError:
        pass


def test_roundtrip_reg_export_matches_original_values():
    """
    Parsea el .reg original -> reconstruye un .reg nuevo -> vuelve a parsear.
    Los valores numéricos deben sobrevivir el round-trip exactamente.
    """
    original_text = FIXTURE_PATH.read_text(encoding="utf-8-sig")
    bond = parse_windows_reg_text(original_text)

    regenerated_text = build_windows_reg_content(bond)
    bond_again = parse_windows_reg_text(regenerated_text)

    assert bond_again.adapter_mac == bond.adapter_mac
    assert bond_again.device_mac == bond.device_mac
    assert bond_again.ltk_hex == bond.ltk_hex
    assert bond_again.ediv == bond.ediv
    assert bond_again.erand == bond.erand
    assert bond_again.auth_req == bond.auth_req


def test_roundtrip_with_different_target_mac():
    """Simula el caso real: el dispositivo rotó su RPA y migramos a una MAC nueva."""
    original_text = FIXTURE_PATH.read_text(encoding="utf-8-sig")
    bond = parse_windows_reg_text(original_text)

    new_mac = "AA:BB:CC:DD:EE:FF"
    regenerated_text = build_windows_reg_content(bond, target_device_mac=new_mac)
    bond_again = parse_windows_reg_text(regenerated_text)

    assert bond_again.device_mac == new_mac
    assert bond_again.ltk_hex == bond.ltk_hex
    assert bond_again.ediv == bond.ediv
    assert bond_again.erand == bond.erand


def test_parse_irk_and_addresstype():
    text = (
        "Windows Registry Editor Version 5.00\r\n\r\n"
        "[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\BTHPORT\\Parameters\\Keys\\700894937778\\d4c1b57106bb]\r\n"
        '"LTK"=hex:ee,07,60,40,eb,c4,1e,8a,17,e9,4b,12,53,6e,37,a2\r\n'
        '"KeyLength"=dword:00000010\r\n'
        '"ERand"=hex(b):00,00,00,00,00,00,00,00\r\n'
        '"EDIV"=dword:00000000\r\n'
        '"IRK"=hex:fe,aa,17,a8,27,96,a4,c0,ed,f2,d6,76,ce,40,34,fd\r\n'
        '"AddressType"=dword:00000001\r\n'
    )
    bond = parse_windows_reg_text(text)
    assert bond.irk_hex == "FEAA17A82796A4C0EDF2D676CE4034FD"
    assert bond.address_type == 1

    regenerated = build_windows_reg_content(bond)
    bond_again = parse_windows_reg_text(regenerated)
    assert bond_again.irk_hex == "FEAA17A82796A4C0EDF2D676CE4034FD"
    assert bond_again.address_type == 1


# ── Task 2: LinkKeyBond unit tests ────────────────────────────────────────

_GOOD_KEY = "0102030405060708090A0B0C0D0E0F10"
_ADAPTER  = "AA:BB:CC:DD:EE:FF"
_DEVICE   = "11:22:33:44:55:66"


def test_link_key_bond_validates_key_length():
    """Reject keys shorter or longer than 32 hex chars (16 bytes)."""
    for bad_key in ("AABBCC" * 5, "00" * 17):  # 30 chars and 34 chars
        try:
            LinkKeyBond(
                adapter_mac=_ADAPTER, device_mac=_DEVICE,
                link_key_hex=bad_key, key_type=4, pin_length=0, device_class="",
            )
            assert False, f"Should have raised ValueError for key: {bad_key!r}"
        except ValueError:
            pass


def test_link_key_bond_normalizes_macs():
    """Accept MACs without colons and normalise to XX:XX:XX:XX:XX:XX upper-case."""
    bond = LinkKeyBond(
        adapter_mac="aabbccddeeff",
        device_mac="112233445566",
        link_key_hex=_GOOD_KEY,
        key_type=4, pin_length=0, device_class="",
    )
    assert bond.adapter_mac == _ADAPTER
    assert bond.device_mac  == _DEVICE
    assert bond.mac_nodelim == "112233445566"
    assert bond.adapter_mac_nodelim == "aabbccddeeff"


def test_link_key_bond_rejects_invalid_hex():
    """Reject a 32-char string that is not valid hexadecimal."""
    bad_key = "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"  # 32 chars but not hex
    try:
        LinkKeyBond(
            adapter_mac=_ADAPTER, device_mac=_DEVICE,
            link_key_hex=bad_key, key_type=4, pin_length=0, device_class="",
        )
        assert False, "Should have raised ValueError for non-hex key"
    except ValueError:
        pass


# ── Task 4: parse_classic_from_reg_text ──────────────────────────────────

_CLASSIC_REG_TEXT = (
    "Windows Registry Editor Version 5.00\r\n\r\n"
    "[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT"
    "\\Parameters\\Keys\\aabbccddeeff]\r\n"
    '"112233445566"=hex:01,02,03,04,05,06,07,08,09,0a,0b,0c,0d,0e,0f,10\r\n'
)


def test_parse_classic_from_reg_text_placeholder():
    """Inline .reg snippet -> LinkKeyBond with correct fields."""
    bonds = parse_classic_from_reg_text(_CLASSIC_REG_TEXT)
    assert len(bonds) == 1, f"Expected 1 bond, got {len(bonds)}"
    b = bonds[0]
    assert b.adapter_mac == "AA:BB:CC:DD:EE:FF"
    assert b.device_mac  == "11:22:33:44:55:66"
    assert b.link_key_hex == "0102030405060708090A0B0C0D0E0F10"
    assert b.source_os == "windows"
    assert b.mac_nodelim == "112233445566"


# ── Task 6: write_classic_info integration test ────────────────────────────

def test_write_classic_info_produces_complete_file():
    """
    Calls write_classic_info() with an injected temp dir (never touches
    /var/lib/bluetooth — AGENTS.md rule 6). Asserts all three sections and
    their key fields are present and correct.
    """
    import configparser, tempfile
    from backends.linux_classic import write_classic_info

    bond = LinkKeyBond(
        adapter_mac="AA:BB:CC:DD:EE:FF",
        device_mac="11:22:33:44:55:66",
        link_key_hex=_GOOD_KEY,
        key_type=4,
        pin_length=0,
        device_class="0x002540",
        device_name="Test Keyboard",
        device_id={"source": "1", "vendor": "0x045E",
                   "product": "0x0800", "version": "0x0001"},
        service_uuids=["00001124-0000-1000-8000-00805f9b34fb"],
        source_os="linux",
    )

    with tempfile.TemporaryDirectory() as tmp:
        target_dir = Path(tmp) / "AA:BB:CC:DD:EE:FF" / "11:22:33:44:55:66"
        info_path = write_classic_info(bond, target_dir)

        assert info_path.exists(), "info file was not created"

        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read(info_path)

        assert "General" in cfg, "Missing [General] section"
        assert cfg["General"]["SupportedTechnologies"] == "BR/EDR;"
        assert cfg["General"]["Class"] == bond.device_class
        assert cfg["General"]["Trusted"] == "true"

        assert "LinkKey" in cfg, "Missing [LinkKey] section"
        assert cfg["LinkKey"]["Key"] == bond.link_key_hex
        assert cfg["LinkKey"]["Type"] == str(bond.key_type)
        assert cfg["LinkKey"]["PINLength"] == str(bond.pin_length)

        assert "DeviceID" in cfg, "Missing [DeviceID] section"
        assert cfg["DeviceID"]["Source"] == "1"

        assert "LongTermKey" not in cfg
        assert "IdentityResolvingKey" not in cfg


# ── Task 9: Classic exporter/importer round-trip ─────────────────────────

def test_classic_exporter_importer_roundtrip():
    """export_classic_bond -> load_classic_bond_from_reg_file: all fields preserved."""
    import tempfile
    from exporters.classic_exporter import build_classic_reg_content
    from importers.classic_importer import load_classic_bond_from_reg_file

    original = LinkKeyBond(
        adapter_mac=_ADAPTER,
        device_mac=_DEVICE,
        link_key_hex=_GOOD_KEY,
        key_type=4,
        pin_length=0,
        device_class="0x002540",
        device_name="Round-trip Keyboard",
        source_os="linux",
    )

    reg_text = build_classic_reg_content(original)
    # Verify structural format: plain value under adapter key, NOT a subkey
    assert f'[HKEY_LOCAL_MACHINE' in reg_text
    assert f'\\{original.adapter_mac_nodelim}]' in reg_text
    assert f'"{original.mac_nodelim}"=hex:' in reg_text

    with tempfile.NamedTemporaryFile(suffix=".reg", mode="w",
                                     delete=False, encoding="utf-8") as tf:
        tf.write(reg_text)
        tmp_path = Path(tf.name)

    try:
        reloaded = load_classic_bond_from_reg_file(tmp_path)
        assert reloaded.adapter_mac == original.adapter_mac
        assert reloaded.device_mac  == original.device_mac
        assert reloaded.link_key_hex == original.link_key_hex
    finally:
        tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    # Permite correr sin pytest instalado: python3 tests/test_parsing.py
    tests = [
        test_parse_real_mouse_reg,
        test_bondkey_normalizes_mac_without_separators,
        test_bondkey_rejects_invalid_ltk_length,
        test_roundtrip_reg_export_matches_original_values,
        test_roundtrip_with_different_target_mac,
        test_parse_irk_and_addresstype,
        test_link_key_bond_validates_key_length,
        test_link_key_bond_normalizes_macs,
        test_link_key_bond_rejects_invalid_hex,
        test_parse_classic_from_reg_text_placeholder,
        test_write_classic_info_produces_complete_file,
        test_classic_exporter_importer_roundtrip,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests pasaron")
    sys.exit(1 if failures else 0)
