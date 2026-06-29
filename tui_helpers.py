"""
tui_helpers.py

Polished, interactive, colored terminal UI components for btkey_sync.
Handles color output, spinners, banner layout, input prompts, and signals.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

_TTY = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text

def bold(t: str)    -> str: return _c("1", t)
def dim(t: str)     -> str: return _c("2", t)
def cyan(t: str)    -> str: return _c("96", t)
def green(t: str)   -> str: return _c("92", t)
def yellow(t: str)  -> str: return _c("93", t)
def red(t: str)     -> str: return _c("91", t)
def blue(t: str)    -> str: return _c("94", t)
def magenta(t: str) -> str: return _c("95", t)

BANNER_LINES = [
    r"  ██████╗ ████████╗██╗  ██╗███████╗██╗   ██╗    ███████╗██╗   ██╗███╗   ██╗ ██████╗",
    r"  ██╔══██╗╚══██╔══╝██║ ██╔╝██╔════╝╚██╗ ██╔╝    ██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝",
    r"  ██████╔╝   ██║   █████╔╝ █████╗   ╚████╔╝     ███████╗ ╚████╔╝ ██╔██╗ ██║██║",
    r"  ██╔══██╗   ██║   ██╔═██╗ ██╔══╝    ╚██╔╝      ╚════██║  ╚██╔╝  ██║╚██╗██║██║",
    r"  ██████╔╝   ██║   ██║  ██╗███████╗   ██║       ███████║   ██║   ██║ ╚████║╚██████╗",
    r"  ╚═════╝    ╚═╝   ╚═╝  ╚═╝╚══════╝   ╚═╝       ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝",
]

SEP  = dim("─" * 62)
SEP2 = dim("═" * 62)

def print_banner() -> None:
    print()
    for i, line in enumerate(BANNER_LINES):
        colour = cyan if i < 3 else blue
        print(colour(bold(line)))
        if _TTY:
            time.sleep(0.04)
    print(dim("  Bluetooth LE Bond Key Synchronizer\n"))

def header(text: str) -> None:
    print(f"\n{SEP}\n  {bold(cyan(text))}\n{SEP}")

def ok(msg: str)   -> None: print(f"  {green('✓')}  {msg}")
def warn(msg: str) -> None: print(f"  {yellow('⚠')}  {yellow(msg)}")
def err(msg: str)  -> None: print(f"  {red('✗')}  {red(msg)}", file=sys.stderr)
def info(msg: str) -> None: print(f"  {blue('ℹ')}  {msg}")

def ask(prompt: str, default: str = "") -> str:
    hint = f" {dim('[' + default + ']')}" if default else ""
    try:
        return input(f"  {cyan('→')} {prompt}{hint}: ").strip() or default
    except EOFError:
        return default

def pause() -> None:
    try:
        input(f"\n  {dim('Press Enter to return to the menu...')}")
    except (KeyboardInterrupt, EOFError):
        pass

class Spinner:
    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, label: str):
        self._label  = label
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = cyan(self._FRAMES[i % len(self._FRAMES)])
            print(f"\r  {frame}  {self._label}", end="", flush=True)
            time.sleep(0.08)
            i += 1

    def __enter__(self):
        if _TTY:
            self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if _TTY and self._thread.is_alive():
            self._thread.join()
        print(f"\r  {green('✓')}  {self._label}  {dim('done')}     ")

def sigint_handler(sig, frame):
    print(f"\n\n  {yellow('Interrupted.')}  {dim('Returning to menu...')}\n")
    raise KeyboardInterrupt

signal.signal(signal.SIGINT, sigint_handler)


def warn_classic_preflight() -> bool:
    """
    Display the mandatory pre-flight safety check for Classic (BR/EDR) flows.
    Returns True only if the user explicitly acknowledges with 'yes' (RC6).

    All 5 mandatory points from CLASSIC_SUPPORT.agent.md §4 are covered here.
    """
    print(f"\n{SEP2}")
    print(f"  {bold(yellow('⚠  BLUETOOTH CLASSIC — PRE-FLIGHT SAFETY CHECK'))}")
    print(f"{SEP2}\n")

    warn("BR/EDR devices often store only ONE bonding at a time.")
    print(f"     {dim('Migrating the key does NOT guarantee the device will accept it.')}")
    print(f"     {dim('The device firmware decides whether to honour the imported key.')}\n")

    warn("Make sure the device is powered ON and NOT reconnecting to another host.")
    print(f"     {dim('If unsure: power it OFF, wait 5 seconds, power ON just before running this.')}\n")

    warn("Do NOT power-cycle the device between export and import.")
    print(f"     {dim('Each incomplete power cycle risks the device discarding its stored key.')}\n")

    info("After import: connect immediately WITHOUT pressing the pairing button.")
    print(f"     {dim('Pressing the pairing button forces a fresh negotiation, invalidating the key.')}\n")

    info("If connection fails with a timeout (not auth error): the device may still")
    print(f"     {dim('be linked to the other host. Remove it there first, then retry here.')}\n")

    print(f"{SEP2}")
    ack = ask("Type 'yes' to confirm you have read the above and wish to proceed", default="")
    return ack.strip().lower() == "yes"


def warn_classic_post_import(device_mac: str) -> None:
    """
    Display the mandatory post-import instructions before the user tries to
    connect (RC7). Called immediately after a successful Classic import.
    """
    print(f"\n{SEP}")
    print(f"  {bold(green('✓  Classic bond imported successfully'))}")
    print(f"{SEP}\n")
    warn(f"Do NOT press the physical pairing button on the device.")
    info(f"Connect now to {bold(device_mac)} WITHOUT pressing any pairing button.")
    info("If it times out: remove the device from the OTHER host's Bluetooth settings first.")
    print()
