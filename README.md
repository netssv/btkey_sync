# btkey_sync

Syncs Bluetooth LE bonding (LTK / EDIV / ERand) from BLE devices between
any two "sides": dual-boot operating systems, two partitions/installs of
the same OS, or two separate physical machines — without having to re-pair
the physical device on every switch.

> See `REQUIREMENTS.md` for the full functional scope and `AGENTS.md` if
> you're using an AI agent (Claude Code, etc.) to maintain/extend this project.

## Why this exists

Some budget BLE devices (mice, keyboards) use private MAC addresses that
rotate between pairing sessions (RPA). Windows and Linux store bonding
data in completely different formats and paths:

|                    | Windows                                                                      | Linux (BlueZ)                               |
| ------------------ | ---------------------------------------------------------------------------- | ------------------------------------------- |
| Location           | `HKLM\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys`             | `/var/lib/bluetooth/<adapter>/<device>/info` |
| Required access    | SYSTEM account (Administrator is not enough)                                 | root                                        |
| EDIV/ERand format  | Hexadecimal                                                                  | Decimal                                     |
| Hot reload         | —                                                                            | No: requires `systemctl restart bluetooth`  |

This project automates extraction, conversion, and writing on both sides,
leaving the exchangeable files in an `exports/` folder intended to be copied
manually between partitions (there's no way to sync this live without a
daemon running on both OSes simultaneously).

## Installation

No external dependencies required for normal use (only Python ≥3.10 stdlib).

```bash
git clone <this-repo>
cd btkey_sync
# Optional, only if running tests with pytest:
pip install -e ".[dev]" --break-system-packages
```

## Usage

### Export (on the system where the device IS connecting successfully)

**Linux:**

```bash
sudo python3 -m btkey_sync
```

**Windows** (PowerShell or CMD as Administrator):

```powershell
python -m btkey_sync
```

Interactive flow:

1. Detects the OS automatically.
2. Lists BLE devices with bonding found.
3. You choose which one to export.
4. Generates `exports/<MAC>__<os>__<timestamp>.reg` + `.json` with the same info in plain text.

### Copy the file to the destination

Copy the generated `.reg` file (USB, shared partition, local network,
whatever you have at hand) to the `exports/` folder of the target
installation — this can be the other OS (dual boot), another
partition/install of the same OS, or the equivalent on another machine.

### Import (on the system that needs the bonding)

```bash
sudo python3 -m btkey_sync --import filename.reg
```

Shows locally known devices, lets you confirm or change the destination MAC
(important if the device rotated its RPA address since last time), writes
the bonding, and automatically restarts the Bluetooth stack.

## Project structure

```
btkey_sync/
├── cli.py                  # orchestrates the 4-step flow
├── models.py                # BondKey: OS-agnostic bonding representation
├── platform_detect.py       # Windows/Linux detection + environment validation
├── storage.py                # manages the exports/ folder and naming convention
├── backends/
│   ├── base.py                # interface all backends must implement
│   ├── windows_backend.py     # Windows registry via SYSTEM scheduled task
│   └── linux_backend.py       # BlueZ info files in /var/lib/bluetooth
├── exporters/reg_exporter.py  # BondKey -> .reg content
└── importers/reg_importer.py  # .reg -> BondKey
```

## Testing

```bash
python3 tests/test_parsing.py
# or, if you installed pytest:
pytest
```

Tests use data from a real confirmed-working migration
(`tests/fixtures/sample_mouse.reg`), including the round-trip case with
a MAC change (rotated RPA).

To test a backend without touching your real system, inject a temporary
directory:

```python
from pathlib import Path
from btkey_sync.backends.linux_backend import LinuxBluetoothBackend

backend = LinuxBluetoothBackend(bluetooth_dir=Path("/tmp/fake_bluetooth"))
```

## Known limitations

- No automatic/live sync between the two sides — always requires a manual
  step of copying the exported file to the destination.
- BLE only (SMP/LTK). Does not cover Bluetooth Classic (BR/EDR).
- If the device uses RPA and rotates its address, the export→copy→import
  cycle must be repeated; this project does not resolve IRK automatically.
- Windows and Linux only. No macOS backend (see `AGENTS.md` if you want
  to add one).

## License / use

Personal project for managing your own equipment. Use at your own
discretion; you are touching internal structures not officially documented
by Microsoft or the BlueZ project.
