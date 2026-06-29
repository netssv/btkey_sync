"""actions_verify.py — Verify & Connect flow: bluetoothctl info + connect with log."""

from __future__ import annotations

import configparser
import subprocess
from pathlib import Path
from platform_detect import OSKind
import tui_helpers as tui
import actions_common as common


def _color_line(line: str) -> str:
    l = line.lower()
    if "successful" in l or l.endswith("yes"):
        return tui.green(line)
    if "fail" in l or "error" in l or l.endswith("no"):
        return tui.red(line)
    return tui.dim(line)


def _parse_field(info_out: str, name: str) -> str:
    for line in info_out.splitlines():
        if name + ":" in line:
            return line.split(":", 1)[-1].strip()
    return "unknown"


def _show_info_file_diagnostic(mac: str) -> None:
    """Read /var/lib/bluetooth/.../info and show LTK/EDiv/Rand for comparison."""
    bt_base = Path("/var/lib/bluetooth")
    for adapter_dir in bt_base.iterdir():
        info_path = adapter_dir / mac / "info"
        if not info_path.exists():
            continue
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        try:
            cfg.read(info_path)
        except Exception:
            return
        print(f"\n{tui.dim('  ── Linux info file diagnostic ──')}")
        if "LongTermKey" in cfg:
            ltk = cfg["LongTermKey"].get("Key", "?")
            ediv = cfg["LongTermKey"].get("EDiv", "?")
            rand = cfg["LongTermKey"].get("Rand", "?")
            tech = cfg.get("General", "SupportedTechnologies", fallback="?")
            print(f"  Tech  : {tui.cyan(tech)}")
            print(f"  LTK   : {tui.cyan(ltk)}")
            print(f"  EDiv  : {tui.cyan(ediv)}  (hex: {tui.dim(hex(int(ediv)) if ediv.isdigit() else '?')})")
            print(f"  Rand  : {tui.cyan(rand)}  (hex: {tui.dim(hex(int(rand)) if rand.isdigit() else '?')})")
        elif "LinkKey" in cfg:
            key = cfg["LinkKey"].get("Key", "?")
            ktype = cfg["LinkKey"].get("Type", "?")
            tech = cfg.get("General", "SupportedTechnologies", fallback="?")
            print(f"  Tech  : {tui.cyan(tech)}")
            print(f"  ClassicKey : {tui.cyan(key)}  Type={ktype}")
        print()
        return


def verify_and_connect(backend, mac: str) -> None:
    """Attempt bluetoothctl connect and print a colour-coded connection log."""
    tui.header("Verify & Connect")
    tui.info(f"Attempting connection to {tui.bold(mac)}…")
    tui.warn("NOTE: If this is a keyboard/mouse, please PRESS SEVERAL KEYS on the device now to wake it up!")
    try:
        info_out = backend.verify_device(mac)
    except Exception as exc:
        tui.err(f"Verification failed: {exc}")
        return

    paired = _parse_field(info_out, "Paired")
    bonded = _parse_field(info_out, "Bonded")
    trusted = _parse_field(info_out, "Trusted")
    connected = _parse_field(info_out, "Connected")

    connect_log = ""
    success = connected == "yes"
    if not success:
        try:
            proc = subprocess.run(
                ["bluetoothctl", "connect", mac],
                capture_output=True, text=True, timeout=18
            )
            connect_log = proc.stdout or proc.stderr
            success = "Connection successful" in connect_log or "Connection: yes" in connect_log
        except subprocess.TimeoutExpired:
            connect_log = "Connection attempt timed out."
        except Exception as exc:
            connect_log = f"Error launching bluetoothctl: {exc}"

    ok = tui.green
    bad = tui.red
    paired_c  = ok(paired)  if paired  == "yes" else bad(paired)
    bonded_c  = ok(bonded)  if bonded  == "yes" else bad(bonded)
    trusted_c = ok(trusted) if trusted == "yes" else bad(trusted)
    print(f"  Paired : {paired_c}   Bonded : {bonded_c}   Trusted : {trusted_c}")

    if connect_log:
        print(f"\n{tui.dim('  ── bluetoothctl connect log ──')}")
        for line in connect_log.splitlines():
            line = line.strip()
            if line:
                print(f"  {_color_line(line)}")
        print()

    if success:
        tui.ok("Connection successful! Device is ready.")
    else:
        _show_info_file_diagnostic(mac)
        if "br-connection-page-timeout" in connect_log:
            tui.warn("Connection failed: Radio-layer Page Timeout.")
            tui.info("This is NOT a key mismatch — the device is not accepting connections.")
            tui.info("Most likely cause: the device is still 'linked' to the other host.")
            tui.info("→ On the other host: remove this device from Bluetooth settings.")
            tui.info("→ Then retry here WITHOUT pressing the device's pairing button.")
        elif ("auth failed" in connect_log.lower()
              or "authentication" in connect_log.lower()
              or "not authorized" in connect_log.lower()):
            tui.warn("Authentication failed — key mismatch suspected.")
            tui.info("Re-export from the source OS immediately and re-import.")
            tui.info("Ensure the device was NOT power-cycled between export and import.")
        else:
            tui.warn("Could not connect. Device may be out of range or MAC rotated.")
            tui.info("Tip: power-cycle the device then run [5] Verify from the menu.")


def run_verify_flow() -> None:
    """Menu flow: pick a bonded device and attempt connection."""
    tui.header("Verify & Connect — Step 1: System detection")
    env, backend = common.detect_and_validate()
    if env.os_kind != OSKind.LINUX:
        tui.err("Verify & Connect is only supported on Linux.")
        return
    tui.header("Verify & Connect — Step 2: Choose device")
    with tui.Spinner("Scanning bonded BLE devices…"):
        devices = backend.list_devices()
    chosen = common.prompt_select_device(devices) if devices else None
    if not chosen:
        tui.info("No device selected.")
        return
    verify_and_connect(backend, chosen.device_mac)

