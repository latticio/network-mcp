"""Vendor-agnostic common tools.

These tools work with any platform that implements the NetworkDriver protocol.
They dispatch to the appropriate vendor driver via normalized getters, returning
a consistent output format regardless of the underlying platform.

Tool naming convention:
- net_* — common tools (vendor-agnostic, this module)
- eos_* — Arista-specific tools
- iosxe_* — Cisco IOS-XE specific
- nxos_* — Cisco NX-OS specific (see tools/cisco/)
- junos_* — Juniper specific
"""

from __future__ import annotations


def _filter_fields(data: dict[str, dict], fields: list[str]) -> dict[str, dict]:
    """Filter each interface dict to include only the requested fields.

    Args:
        data: Mapping of interface name to interface attributes dict.
        fields: List of field names to retain.

    Returns:
        Filtered dict with only the requested fields per interface.
    """
    return {name: {k: v for k, v in attrs.items() if k in fields} for name, attrs in data.items()}
