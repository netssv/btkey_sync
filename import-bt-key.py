#!/usr/bin/env python3
"""
import-bt-key.py

Migra el bonding (LTK/EDIV/ERand) de un dispositivo Bluetooth LE exportado
desde Windows (.reg) hacia el almacenamiento de BlueZ en Linux, sin necesidad
de re-emparejar el dispositivo.

Uso:
    sudo python3 import-bt-key.py /ruta/al/archivo.reg

Qué hace:
    1. Parsea el .reg exportado de Windows (LTK, EDIV, ERand, AuthReq, MAC).
    2. Convierte EDIV/ERand de hex (formato Windows) a decimal (formato BlueZ).
    3. Lista los adaptadores y dispositivos ya conocidos por BlueZ para que
       elijas el destino correcto (o crear uno nuevo con la MAC del .reg).
    4. Crea/actualiza la carpeta en /var/lib/bluetooth/<adapter>/<device>/info
    5. Reinicia el servicio bluetooth (systemctl restart) para forzar a BlueZ
       a releer el estado desde disco -- BlueZ NO relee esto en caliente.

Requiere ejecutarse con sudo (lee/escribe en /var/lib/bluetooth).
"""

import os
import re
import sys
import shutil
import subprocess
import configparser
from pathlib import Path

BLUETOOTH_DIR = Path("/var/lib/bluetooth")


def fail(msg):
    print(f"\n[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def require_root():
    if os.geteuid() != 0:
        fail("Este script necesita privilegios de root. Ejecuta con: sudo python3 import-bt-key.py archivo.reg")


def parse_reg_file(path):
    """Extrae adapter_mac, device_mac, LTK, EDIV (decimal), ERand (decimal), AuthReq desde un .reg de Windows."""
    text = Path(path).read_text(encoding="utf-8-sig", errors="ignore")

    key_path_match = re.search(
        r"\[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT\\Parameters\\Keys\\"
        r"([0-9A-Fa-f]{12})\\([0-9A-Fa-f]{12})\]",
        text,
    )
    if not key_path_match:
        fail("No se encontró una ruta de registro BTHPORT\\Parameters\\Keys válida en el archivo .reg")

    adapter_mac_raw, device_mac_raw = key_path_match.groups()

    ltk_match = re.search(r'"LTK"=hex:([0-9A-Fa-f,]+)', text)
    if not ltk_match:
        fail("No se encontró el valor LTK en el .reg")
    ltk_bytes = ltk_match.group(1).split(",")
    ltk_hex = "".join(b.upper() for b in ltk_bytes)

    ediv_match = re.search(r'"EDIV"=dword:([0-9A-Fa-f]+)', text)
    if not ediv_match:
        fail("No se encontró el valor EDIV en el .reg")
    ediv_decimal = int(ediv_match.group(1), 16)

    # ERand puede venir como hex(b): (binary little-endian bytes) o como dword/qword en otros exports
    erand_bytes_match = re.search(r'"ERand"=hex\(b\):([0-9A-Fa-f,]+)', text)
    if erand_bytes_match:
        byte_list = erand_bytes_match.group(1).split(",")
        # Los bytes en hex(b) vienen en orden little-endian tal como Windows los guarda
        erand_le_bytes = bytes(int(b, 16) for b in byte_list)
        erand_decimal = int.from_bytes(erand_le_bytes, byteorder="little")
    else:
        erand_qword_match = re.search(r'"ERand"=qword:([0-9A-Fa-f]+)', text)
        erand_dword_match = re.search(r'"ERand"=dword:([0-9A-Fa-f]+)', text)
        if erand_qword_match:
            erand_decimal = int(erand_qword_match.group(1), 16)
        elif erand_dword_match:
            erand_decimal = int(erand_dword_match.group(1), 16)
        else:
            fail("No se encontró el valor ERand en el .reg (ni hex(b), ni qword, ni dword)")

    authreq_match = re.search(r'"AuthReq"=dword:([0-9A-Fa-f]+)', text)
    authreq = int(authreq_match.group(1), 16) if authreq_match else None

    def fmt_mac(raw):
        return ":".join(raw[i:i+2].upper() for i in range(0, 12, 2))

    return {
        "adapter_mac": fmt_mac(adapter_mac_raw),
        "device_mac": fmt_mac(device_mac_raw),
        "ltk_hex": ltk_hex,
        "ediv": ediv_decimal,
        "erand": erand_decimal,
        "authreq": authreq,
    }


def list_local_adapters():
    if not BLUETOOTH_DIR.exists():
        fail(f"No existe {BLUETOOTH_DIR}; ¿está instalado/inicializado BlueZ?")
    return [p.name for p in BLUETOOTH_DIR.iterdir() if p.is_dir() and re.match(r"^[0-9A-Fa-f:]{17}$", p.name)]


def list_devices_for_adapter(adapter_mac):
    adapter_dir = BLUETOOTH_DIR / adapter_mac
    return [
        p.name for p in adapter_dir.iterdir()
        if p.is_dir() and re.match(r"^[0-9A-Fa-f:]{17}$", p.name)
    ]


def prompt_choice(prompt, options):
    print(f"\n{prompt}")
    for idx, opt in enumerate(options, start=1):
        print(f"  [{idx}] {opt}")
    print(f"  [n] Ninguno de estos / usar otro valor manualmente")
    while True:
        choice = input("Elige una opción: ").strip().lower()
        if choice == "n":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print("Opción inválida, intenta de nuevo.")


def write_bluez_info(device_dir, parsed, existing_info_path=None):
    """
    Crea o actualiza el archivo info de BlueZ con el LongTermKey nuevo.
    Si existe un info previo (de otra MAC del mismo dispositivo), lo usa como base
    para conservar Name, Services, ConnectionParameters, etc.
    """
    config = configparser.ConfigParser()
    config.optionxform = str  # preserva mayúsculas/minúsculas de las claves

    if existing_info_path and Path(existing_info_path).exists():
        config.read(existing_info_path)
        print(f"  Usando como base: {existing_info_path}")
    else:
        config["General"] = {
            "Name": "Imported Device",
            "SupportedTechnologies": "LE;",
            "Trusted": "true",
            "Blocked": "false",
            "WakeAllowed": "true",
        }

    if "LongTermKey" not in config:
        config["LongTermKey"] = {}

    config["LongTermKey"]["Key"] = parsed["ltk_hex"]
    config["LongTermKey"]["Authenticated"] = "0"
    config["LongTermKey"]["EncSize"] = "16"
    config["LongTermKey"]["EDiv"] = str(parsed["ediv"])
    config["LongTermKey"]["Rand"] = str(parsed["erand"])

    device_dir.mkdir(parents=True, exist_ok=True)
    info_path = device_dir / "info"

    with open(info_path, "w") as f:
        config.write(f, space_around_delimiters=False)

    os.chmod(info_path, 0o600)
    # BlueZ espera root:root
    shutil.chown(info_path, user="root", group="root")

    return info_path


def restart_bluetooth():
    print("\n==> Reiniciando el servicio bluetooth (systemctl restart)...")
    result = subprocess.run(["systemctl", "restart", "bluetooth"], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [advertencia] systemctl restart devolvió error: {result.stderr.strip()}")
    else:
        print("  Servicio reiniciado correctamente.")


def verify_device(device_mac):
    print(f"\n==> Verificando estado con bluetoothctl...")
    result = subprocess.run(
        ["bluetoothctl", "info", device_mac],
        capture_output=True, text=True
    )
    print(result.stdout or result.stderr)


def main():
    require_root()

    if len(sys.argv) != 2:
        fail("Uso: sudo python3 import-bt-key.py /ruta/al/archivo.reg")

    reg_path = sys.argv[1]
    if not Path(reg_path).exists():
        fail(f"No existe el archivo: {reg_path}")

    print(f"==> Parseando {reg_path}...")
    parsed = parse_reg_file(reg_path)

    print("\nValores extraídos del .reg de Windows:")
    print(f"  Adapter MAC (Windows): {parsed['adapter_mac']}")
    print(f"  Device MAC  (Windows): {parsed['device_mac']}")
    print(f"  LTK:    {parsed['ltk_hex']}")
    print(f"  EDIV:   {parsed['ediv']} (decimal, convertido desde hex)")
    print(f"  ERand:  {parsed['erand']} (decimal, convertido desde hex little-endian)")
    print(f"  AuthReq: {parsed['authreq']}")

    # --- Elegir adaptador local ---
    local_adapters = list_local_adapters()
    if not local_adapters:
        fail("No se encontraron adaptadores Bluetooth en /var/lib/bluetooth/")

    chosen_adapter = None
    if parsed["adapter_mac"] in local_adapters:
        print(f"\nEl adaptador {parsed['adapter_mac']} del .reg coincide con uno local.")
        use_it = input("¿Usar ese adaptador? [S/n]: ").strip().lower()
        if use_it in ("", "s", "si", "y", "yes"):
            chosen_adapter = parsed["adapter_mac"]

    if not chosen_adapter:
        chosen_adapter = prompt_choice("Selecciona el adaptador Bluetooth local a usar:", local_adapters)
        if not chosen_adapter:
            chosen_adapter = input("Escribe la MAC del adaptador manualmente (formato XX:XX:XX:XX:XX:XX): ").strip().upper()

    # --- Elegir dispositivo destino (MAC nueva vs MAC vieja existente) ---
    existing_devices = list_devices_for_adapter(chosen_adapter)
    print(f"\nDispositivos ya conocidos por BlueZ bajo el adaptador {chosen_adapter}:")

    existing_choice = None
    if existing_devices:
        existing_choice = prompt_choice(
            "Si alguno de estos es una carpeta VIEJA de este mismo dispositivo (para copiar Name/Services), elígelo. "
            "Si no, selecciona 'n':",
            existing_devices,
        )

    target_mac = parsed["device_mac"]
    custom_mac = input(
        f"\nMAC del dispositivo a crear/actualizar en Linux [{target_mac}] (Enter para usar esta): "
    ).strip().upper()
    if custom_mac:
        target_mac = custom_mac

    target_dir = BLUETOOTH_DIR / chosen_adapter / target_mac
    existing_info_path = (BLUETOOTH_DIR / chosen_adapter / existing_choice / "info") if existing_choice else None

    print(f"\n==> Escribiendo info en: {target_dir}/info")
    info_path = write_bluez_info(target_dir, parsed, existing_info_path)
    print(f"  Listo: {info_path}")

    if existing_choice and existing_choice != target_mac:
        delete_old = input(
            f"\n¿Borrar la carpeta vieja {existing_choice}? [s/N]: "
        ).strip().lower()
        if delete_old in ("s", "si", "y", "yes"):
            shutil.rmtree(BLUETOOTH_DIR / chosen_adapter / existing_choice, ignore_errors=True)
            print(f"  Carpeta vieja {existing_choice} eliminada.")

    restart_bluetooth()
    verify_device(target_mac)

    print("\n==> Listo. Si 'Connected: yes' no aparece todavía, mueve/usa el dispositivo físicamente")
    print("    para que se reconecte, o prueba: bluetoothctl connect", target_mac)


if __name__ == "__main__":
    main()
