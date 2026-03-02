"""Inventory backend protocol and implementations.

Provides pluggable inventory sources for device lookup.
Ships with the YAML file backend; additional backends can be
registered via the plugin system.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import yaml  # type: ignore[import-untyped]
from pydantic import SecretStr

from network_mcp.config import Platform
from network_mcp.connection import DeviceCredentials

logger = logging.getLogger("network-mcp")

# Set of valid platform strings for inventory validation
_VALID_PLATFORMS = {p.value for p in Platform}


@runtime_checkable
class InventoryBackend(Protocol):
    """Protocol for inventory data sources."""

    def get_devices(self) -> dict[str, DeviceCredentials]:
        """Return all known devices keyed by name."""
        ...

    def get_device(self, name: str) -> DeviceCredentials | None:
        """Return credentials for a single device by name, or None."""
        ...


class YamlInventoryBackend:
    """Load device inventory from a YAML file (devices.yaml)."""

    def __init__(
        self,
        path: str,
        default_username: str = "admin",
        default_password: str = "",
        default_transport: str = "https",
    ) -> None:
        self._devices: dict[str, DeviceCredentials] = {}
        self._load(path, default_username, default_password, default_transport)

    def _load(self, path: str, default_username: str, default_password: str, default_transport: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as e:
            raise ValueError(f"Failed to load inventory file '{path}': {e}") from e

        if data is None:
            raise ValueError(f"Inventory file '{path}' is empty")

        devices = data.get("devices")
        if not isinstance(devices, dict):
            raise ValueError(f"Inventory file '{path}' must contain a 'devices' mapping")

        seen_hosts: dict[str, str] = {}  # host IP/name -> first device name that used it

        for name, device in devices.items():
            if not isinstance(device, dict):
                logger.warning("Skipping invalid device entry '%s' in inventory", name)
                continue

            if "host" not in device:
                raise ValueError(f"Device '{name}' is missing required 'host' field in inventory")

            platform = device.get("platform", "eos")
            if platform not in _VALID_PLATFORMS:
                raise ValueError(
                    f"Device '{name}' has unknown platform '{platform}'. Valid platforms: {sorted(_VALID_PLATFORMS)}"
                )

            host = device["host"]

            # Detect duplicate host IPs/names across devices
            if host in seen_hosts:
                logger.warning(
                    "Duplicate host '%s': devices '%s' and '%s' share the same host address",
                    host,
                    seen_hosts[host],
                    name,
                )
            else:
                seen_hosts[host] = name

            self._devices[name] = DeviceCredentials(
                host=host,
                username=device.get("username", default_username),
                password=SecretStr(device.get("password", default_password)),
                transport=device.get("transport", default_transport),
                port=device.get("port", 443),
                platform=platform,
                role=device.get("role"),
                groups=device.get("groups", []),
                tags=device.get("tags", []),
                mlag_peer=device.get("mlag_peer"),
                tenants=device.get("tenants", []),
            )

    def get_devices(self) -> dict[str, DeviceCredentials]:
        return dict(self._devices)

    def get_device(self, name: str) -> DeviceCredentials | None:
        return self._devices.get(name)
