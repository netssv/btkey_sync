"""
actions_help.py

Submenu helper and advisor for Bluetooth LE vs Classic (BR/EDR) devices.
Provides educational advice and guides the user to the correct workflow.
"""

from __future__ import annotations

import tui_helpers as tui
from platform_detect import OSKind
from actions import run_export_flow, run_import_flow
from actions_classic import run_classic_export_flow, run_classic_import_flow


def show_advice_and_differences() -> None:
    """Print a detailed comparison between Bluetooth LE and Bluetooth Classic."""
    tui.header("Advice — BLE vs. Bluetooth Classic (BR/EDR)")
    
    print(f"  {tui.bold(tui.cyan('1. Bluetooth Low Energy (BLE)'))}")
    print(f"     • {tui.bold('Device Types:')} Modern mice/keyboards (e.g., Logitech MX series), smartwatches, fitness trackers.")
    print(f"     • {tui.bold('Key Structure:')} Uses a Long-Term Key (LTK), plus EDIV (Encryption Diversifier) and ERand (Randomizer).")
    print(f"     • {tui.bold('Multi-Bonding:')} Often supports storing multiple host profiles/keys.")
    print(f"     • {tui.bold('Sync Method:')} Use standard Select & Extract [1] and Import [2].")
    print()
    print(f"  {tui.bold(tui.magenta('2. Bluetooth Classic (BR/EDR)'))}")
    print(f"     • {tui.bold('Device Types:')} Audio devices (TWS earbuds, headphones, speakers), older game controllers, cheap keyboards/mice.")
    print(f"     • {tui.bold('Key Structure:')} Uses a single 32-character Link Key. EDIV/ERand are either 0 or non-existent.")
    print(f"     • {tui.bold('Single Slot Limit:')} Typically supports only ONE stored bond slot. Special sequence is required (no power cycles/pairing mode between syncs).")
    print(f"     • {tui.bold('Sync Method:')} Use Classic Export [7] and Classic Import [8].")
    print(f"\n{tui.SEP}")


def run_device_helper_flow(os_kind: OSKind) -> None:
    """Guided wizard to help the user identify their device and start the right flow."""
    while True:
        tui.header("Device Helper & Advisor")
        print(f"    {tui.cyan('[1]')}  {tui.bold('Learn the differences')} {tui.dim('— BLE vs. Classic details')}")
        print(f"    {tui.cyan('[2]')}  {tui.bold('Identify my device')}   {tui.dim('— run diagnostic wizard')}")
        print(f"    {tui.red('[q]')}  {tui.bold('Back to main menu')}")
        print(tui.SEP2)
        
        choice = tui.ask("Choose an option").lower()
        if choice == "1":
            show_advice_and_differences()
            tui.ask("Press Enter to continue", default="")
        elif choice == "2":
            run_diagnostic_wizard(os_kind)
        elif choice in ("q", "quit", "exit", ""):
            return
        else:
            tui.warn("Invalid option.")


def run_diagnostic_wizard(os_kind: OSKind) -> None:
    """Run a quick Q&A to determine device type and offer direct routing."""
    tui.header("Device Identifier Wizard")
    
    print("  Q1: What kind of device is it?")
    print("    1) Audio device (Earbuds, Headphones, Speaker)")
    print("    2) Keyboard, Mouse, or Game Controller")
    print("    3) Other / Not sure")
    
    q1 = tui.ask("Select option", default="3")
    
    is_audio = (q1 == "1")
    
    print("\n  Q2: Do you have existing key details from an extraction attempt?")
    print("    1) Yes: it shows EDIV = 0, ERand = 0, and a 32-char key")
    print("    2) Yes: it shows non-zero EDIV/ERand and an LTK")
    print("    3) No: I haven't extracted it yet")
    
    q2 = tui.ask("Select option", default="3")
    
    # Analyze device type
    is_classic = False
    confidence = "Medium"
    
    if q2 == "1":
        is_classic = True
        confidence = "High (Matches Classic Link Key signature)"
    elif q2 == "2":
        is_classic = False
        confidence = "High (Matches BLE LTK/EDIV/ERand signature)"
    elif is_audio:
        is_classic = True
        confidence = "High (Audio streaming profiles require Classic Bluetooth)"
    else:
        # Default or ambiguous
        is_classic = False
        confidence = "Low (Defaulting to BLE, please check details)"
        
    tui.header("Recommendation Result")
    if is_classic:
        print(f"  {tui.green('●')} Device Type: {tui.bold(tui.magenta('Bluetooth Classic (BR/EDR)'))}")
        print(f"  {tui.green('●')} Confidence:  {tui.bold(confidence)}")
        print(f"  {tui.green('●')} Advice:      Use the {tui.bold('Classic Export / Import')} flows. Do not use BLE.")
        print(f"                TWS earbuds and audio devices usually support only one bond.")
        print(f"                Avoid power cycles or entering pairing mode during sync.")
        print(f"\n  Would you like to start the Classic workflow now?")
        print("    1) Classic Export")
        if os_kind == OSKind.LINUX:
            print("    2) Classic Import")
        print("    3) Return to helper menu")
        
        flow_choice = tui.ask("Select option", default="3")
        if flow_choice == "1":
            run_classic_export_flow()
        elif flow_choice == "2" and os_kind == OSKind.LINUX:
            run_classic_import_flow()
    else:
        print(f"  {tui.green('●')} Device Type: {tui.bold(tui.cyan('Bluetooth Low Energy (BLE)'))}")
        print(f"  {tui.green('●')} Confidence:  {tui.bold(confidence)}")
        print(f"  {tui.green('●')} Advice:      Use the standard {tui.bold('Select & Extract / Import')} flows.")
        print(f"\n  Would you like to start the BLE workflow now?")
        print("    1) BLE Select & Extract")
        print("    2) BLE Import")
        print("    3) Return to helper menu")
        
        flow_choice = tui.ask("Select option", default="3")
        if flow_choice == "1":
            run_export_flow()
        elif flow_choice == "2":
            run_import_flow()
