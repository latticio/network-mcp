"""Demo mode mock inventory — 6-device spine-leaf topology.

Creates a DemoInventoryBackend that satisfies the InventoryBackend protocol.
"""

from __future__ import annotations

from pydantic import SecretStr

from network_mcp.connection import DeviceCredentials

# Pre-built credentials for the 6-device demo topology
DEMO_DEVICES: dict[str, DeviceCredentials] = {
    "spine-01": DeviceCredentials(
        host="10.0.0.1",
        username="admin",
        password=SecretStr(""),
        transport="https",
        platform="eos-mock",
        role="spine",
        groups=["spines", "dc1"],
        tags=["demo"],
    ),
    "spine-02": DeviceCredentials(
        host="10.0.0.2",
        username="admin",
        password=SecretStr(""),
        transport="https",
        platform="eos-mock",
        role="spine",
        groups=["spines", "dc1"],
        tags=["demo"],
    ),
    "leaf-01": DeviceCredentials(
        host="10.0.1.1",
        username="admin",
        password=SecretStr(""),
        transport="https",
        platform="eos-mock",
        role="leaf",
        groups=["leaves", "dc1", "rack-01"],
        tags=["demo", "mlag"],
        mlag_peer="leaf-02",
    ),
    "leaf-02": DeviceCredentials(
        host="10.0.1.2",
        username="admin",
        password=SecretStr(""),
        transport="https",
        platform="eos-mock",
        role="leaf",
        groups=["leaves", "dc1", "rack-01"],
        tags=["demo", "mlag"],
        mlag_peer="leaf-01",
    ),
    "leaf-03": DeviceCredentials(
        host="10.0.2.1",
        username="admin",
        password=SecretStr(""),
        transport="https",
        platform="eos-mock",
        role="leaf",
        groups=["leaves", "dc1", "rack-02"],
        tags=["demo", "mlag"],
        mlag_peer="leaf-04",
    ),
    "leaf-04": DeviceCredentials(
        host="10.0.2.2",
        username="admin",
        password=SecretStr(""),
        transport="https",
        platform="eos-mock",
        role="leaf",
        groups=["leaves", "dc1", "rack-02"],
        tags=["demo", "mlag"],
        mlag_peer="leaf-03",
    ),
}


class DemoInventoryBackend:
    """Inventory backend for demo mode — returns a fixed 6-device topology."""

    def get_devices(self) -> dict[str, DeviceCredentials]:
        return dict(DEMO_DEVICES)

    def get_device(self, name: str) -> DeviceCredentials | None:
        return DEMO_DEVICES.get(name)
