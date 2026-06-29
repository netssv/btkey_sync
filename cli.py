"""
cli.py

Main entry point. Polished interactive TUI menu wrapper.
Adheres strictly to requirement R14 (under 200 lines).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from platform_detect import OSKind, gather_environment_info
import tui_helpers as tui
from actions import run_export_flow, run_import_flow, run_clone_flow
from actions_verify import run_verify_flow
from actions_remove import run_remove_flow
from actions_show import run_show_keys_flow
from actions_classic import run_classic_export_flow, run_classic_import_flow
from actions_help import run_device_helper_flow
from actions_clone import run_unified_clone_flow


def _check_and_advise_sudo() -> None:
    if os.name != "posix":
        return
    if os.geteuid() == 0:
        return

    print(f"\n{tui.SEP2}")
    print(f"  {tui.yellow(tui.bold('⚠  Root privileges required'))}")
    print(tui.SEP2)
    print(
        f"\n  {tui.bold('btkey_sync')} reads and writes {tui.cyan('/var/lib/bluetooth')},\n"
        f"  which is only accessible as {tui.bold('root')} (sudo).\n"
    )
    print(f"  {tui.dim('Current user:')} {os.environ.get('USER', '?')}  {tui.red('(not root)')}\n")

    sudo_bin = shutil.which("sudo")
    if sudo_bin:
        ans = tui.ask(f"Re-launch now with {tui.bold('sudo')}? [{tui.green('Y')}/{tui.red('n')}]", default="Y")
        if ans.strip().lower() in ("y", "yes", ""):
            args = [sys.executable] + sys.argv
            print(f"\n  {tui.dim('Relaunching:')} sudo {' '.join(args)}\n")
            os.execvp("sudo", ["sudo"] + args)
    else:
        tui.warn("'sudo' not found in PATH.")

    print(f"\n  Run manually with:\n    {tui.cyan('sudo ' + sys.executable + ' ' + ' '.join(sys.argv))}\n")
    sys.exit(1)


def _print_main_menu() -> None:
    print(f"\n{tui.SEP2}")
    print(f"  {tui.bold(tui.cyan('Main Menu'))}")
    print(tui.SEP2)
    print(f"    {tui.cyan('[1]')}  {tui.bold('Clone Device')}         {tui.dim('— directly sync BLE, Classic or Dual-Mode')}")
    print(f"    {tui.cyan('[2]')}  {tui.bold('Export Key to File')}   {tui.dim('— save device keys to a .reg file')}")
    print(f"    {tui.cyan('[3]')}  {tui.bold('Import Key from File')} {tui.dim('— load device keys from a .reg file')}")
    print(f"    {tui.cyan('[4]')}  {tui.bold('Remove Device')}        {tui.dim('— delete bonding from this OS')}")
    print(f"    {tui.cyan('[5]')}  {tui.bold('Show Keys')}            {tui.dim('— display all bonding keys')}")
    print(f"    {tui.cyan('[6]')}  {tui.bold('Device Help & Advice')} {tui.dim('— identify BLE vs Classic workflow')}")
    print(f"    {tui.red('[q]')}  {tui.bold('Quit')}")
    print(tui.SEP2)


def run_interactive_menu() -> None:
    tui.print_banner()
    env = gather_environment_info()

    for note in env.notes:
        tui.warn(note)

    while True:
        _print_main_menu()
        try:
            choice = tui.ask("Choose an option").lower()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {tui.dim('Goodbye.')}\n")
            sys.exit(0)

        try:
            if choice == "1":
                run_unified_clone_flow()
            elif choice == "2":
                print(f"\n  {tui.bold('Select Export Type:')}")
                print(f"    1) BLE (Low Energy) Key")
                print(f"    2) Classic (BR/EDR) Key")
                sub = tui.ask("Export type", default="1")
                if sub == "1":
                    run_export_flow()
                elif sub == "2":
                    run_classic_export_flow()
            elif choice == "3":
                print(f"\n  {tui.bold('Select Import Type:')}")
                print(f"    1) BLE (Low Energy) Key")
                print(f"    2) Classic (BR/EDR) Key")
                sub = tui.ask("Import type", default="1")
                if sub == "1":
                    run_import_flow()
                elif sub == "2":
                    run_classic_import_flow()
            elif choice == "4":
                run_remove_flow()
            elif choice == "5":
                run_show_keys_flow()
                tui.pause()
            elif choice == "6":
                run_device_helper_flow(env.os_kind)
                tui.pause()
            elif choice in ("q", "quit", "exit", ""):
                print(f"\n  {tui.dim('Goodbye.')}\n")
                sys.exit(0)
            else:
                tui.warn("Invalid option — please select a valid option from the menu.")
                tui.pause()
        except KeyboardInterrupt:
            print(f"\n  {tui.yellow('Flow interrupted.')}  {tui.dim('Returning to menu...')}\n")
            continue


def main() -> None:
    _check_and_advise_sudo()

    parser = argparse.ArgumentParser(
        description="Synchronize Bluetooth LE bond keys between OSes, partitions, or machines."
    )
    parser.add_argument(
        "--import", dest="import_file", metavar="FILE.reg",
        help="Non-interactive: import an already-exported .reg file.",
    )
    args = parser.parse_args()

    try:
        if args.import_file:
            run_import_flow(args.import_file)
        else:
            run_interactive_menu()
    except KeyboardInterrupt:
        print(f"\n  {tui.dim('Goodbye.')}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
