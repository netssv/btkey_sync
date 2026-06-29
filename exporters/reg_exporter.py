"""
exporters/reg_exporter.py

Genera el archivo .reg "universal" -- usamos formato .reg como representación
intercambiable incluso cuando el origen es Linux, porque:
  1. Es texto plano fácil de inspeccionar/versionar.
  2. Ya tenemos el parser robusto (parse_windows_reg_text) probado en producción.
  3. Es el formato nativo de Windows, así que si el destino ES Windows, el
     archivo ya está listo para `reg import` sin transformación adicional.

Cuando el destino es Linux, import-bt-key se encarga de convertir hex->decimal;
el .reg sigue siendo solo el "contenedor de transporte".
"""

from __future__ import annotations

from pathlib import Path

from models import BondKey
from storage import write_reg_export


def build_windows_reg_content(bond: BondKey, target_device_mac: str | None = None) -> str:
    target_mac = (target_device_mac or bond.device_mac).replace(":", "").lower()
    adapter_nodelim = bond.adapter_mac.replace(":", "").lower()

    ltk_pairs = ",".join(bond.ltk_hex[i:i + 2] for i in range(0, len(bond.ltk_hex), 2))
    erand_le_bytes = bond.erand.to_bytes(8, byteorder="little")
    erand_hex_bytes = ",".join(f"{b:02x}" for b in erand_le_bytes)

    lines = [
        "Windows Registry Editor Version 5.00",
        "",
        f"[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT\\"
        f"Parameters\\Keys\\{adapter_nodelim}\\{target_mac}]",
        f'"LTK"=hex:{ltk_pairs}',
        f'"KeyLength"=dword:{bond.key_length:08x}',
        f'"ERand"=hex(b):{erand_hex_bytes}',
        f'"EDIV"=dword:{bond.ediv:08x}',
    ]
    if bond.auth_req is not None:
        lines.append(f'"AuthReq"=dword:{bond.auth_req:08x}')
    if bond.irk_hex:
        irk_pairs = ",".join(bond.irk_hex[i:i + 2].lower() for i in range(0, len(bond.irk_hex), 2))
        lines.append(f'"IRK"=hex:{irk_pairs}')
    if bond.address_type is not None:
        lines.append(f'"AddressType"=dword:{bond.address_type:08x}')

    return "\r\n".join(lines) + "\r\n"


def export_bond_key(bond: BondKey, target_device_mac: str | None = None) -> Path:
    """
    Paso 4 del flujo: serializa el BondKey ya extraído hacia la carpeta
    especial exports/ (junto al .json de metadata legible).
    """
    content = build_windows_reg_content(bond, target_device_mac=target_device_mac)
    return write_reg_export(bond, content)
