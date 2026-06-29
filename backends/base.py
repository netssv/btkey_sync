"""
backends/base.py

Contract that Windows and Linux backends must fulfill. Keeping this interface
small and stable is what ensures cli.py, exporters/, and importers/ never need
scattered 'if windows / elif linux' branches throughout the codebase — that
branching lives ONLY in cli.py at the moment of selecting the backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from models import BondKey, DiscoveredDevice


class BluetoothBackend(ABC):
    """OS-specific backend for reading/writing BLE bondings."""

    name: str  # "windows" | "linux", used in logs and filenames

    @abstractmethod
    def check_prerequisites(self) -> List[str]:
        """
        Returns a list of environment issues (missing permissions, tools not
        installed, etc.). Empty list = all OK.
        Does not raise exceptions; the caller decides whether to proceed or abort.
        """
        raise NotImplementedError

    @abstractmethod
    def list_devices(self) -> List[DiscoveredDevice]:
        """
        Step 2 of the flow: lists all locally paired BLE devices, with enough
        info for the user to choose which one to export/migrate.
        """
        raise NotImplementedError

    @abstractmethod
    def extract_bond_key(self, device: DiscoveredDevice) -> BondKey:
        """Extracts the complete cryptographic material of a chosen device."""
        raise NotImplementedError

    @abstractmethod
    def import_bond_key(self, bond: BondKey, target_device_mac: str | None = None,
                        source_device_mac: str | None = None) -> Path:
        """
        Writes/updates the bonding in the local storage of this OS, for the
        case of LOCAL migration (same-OS to same-OS, e.g. after reinstalling,
        or synchronizing two Linux machines). Returns the final path where
        it was written.

        target_device_mac allows forcing a different MAC than the one in the
        BondKey (typical case: the device rotated its RPA in the other OS).

        source_device_mac allows copying config/profiles from an old MAC
        address directory if present (e.g. RPA migration).
        """
        raise NotImplementedError

    @abstractmethod
    def restart_bluetooth_stack(self) -> None:
        """Restarts the Bluetooth service/daemon to force re-reading the state from disk."""
        raise NotImplementedError

    @abstractmethod
    def remove_bond_key(self, adapter_mac: str, device_mac: str, remove_backups: bool = False) -> None:
        """
        Removes the bonding key folder or registry entry for a device to start over.
        Accepts the adapter MAC and device MAC. Optionally deletes backup files.
        """
        raise NotImplementedError
