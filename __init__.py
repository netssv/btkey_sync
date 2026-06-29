"""
btkey_sync

Tool for migrating/synchronising Bluetooth LE bondings (LTK/EDIV/ERand)
of BLE devices between any two "sides": two operating systems in dual boot,
two partitions/installations of the same OS, or two separate physical
machines. Avoids having to re-pair BLE devices whose firmware does not
support native multi-host bonding synchronisation.
"""

__version__ = "0.1.0"
