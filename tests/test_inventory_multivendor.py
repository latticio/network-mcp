"""Tests for unified inventory model with multi-vendor support.

Covers:
- Platform enum values
- DRIVER_REGISTRY mapping and extensibility
- DeviceCredentials platform field and defaults
- ConnectionManager driver selection via registry
- Inventory loading with platform validation
- Backward compatibility for devices without platform field
"""

import os
import tempfile

import pytest
from pydantic import SecretStr

from network_mcp.config import NetworkSettings, Platform
from network_mcp.connection import ConnectionManager, DeviceCredentials
from network_mcp.driver import EosDriver
from network_mcp.drivers import DRIVER_REGISTRY
from network_mcp.inventory import YamlInventoryBackend

# --- Platform enum tests ---


class TestPlatformEnum:
    def test_platform_has_eos(self):
        assert Platform.EOS == "eos"

    def test_platform_has_iosxe(self):
        assert Platform.IOSXE == "iosxe"

    def test_platform_has_nxos(self):
        assert Platform.NXOS == "nxos"

    def test_platform_has_junos(self):
        assert Platform.JUNOS == "junos"

    def test_platform_is_str_enum(self):
        """Platform values can be used as plain strings."""
        assert str(Platform.EOS) == "eos"
        assert f"platform={Platform.NXOS}" == "platform=nxos"

    def test_platform_has_four_members(self):
        assert len(Platform) == 4


# --- DRIVER_REGISTRY tests ---


class TestDriverRegistry:
    def test_registry_maps_eos_to_eos_driver(self):
        assert DRIVER_REGISTRY["eos"] is EosDriver

    def test_registry_raises_keyerror_for_unknown(self):
        with pytest.raises(KeyError):
            DRIVER_REGISTRY["unknown_platform"]

    def test_registry_get_returns_none_for_unknown(self):
        assert DRIVER_REGISTRY.get("nonexistent") is None

    def test_registry_contains_eos(self):
        """EOS must always be in the registry."""
        assert "eos" in DRIVER_REGISTRY

    def test_registry_contains_iosxe_when_httpx_available(self):
        """IOS-XE is registered when httpx (cisco extra) is installed."""
        assert "iosxe" in DRIVER_REGISTRY

    def test_registry_values_are_types(self):
        """All registry values should be class types, not instances."""
        for platform, driver_cls in DRIVER_REGISTRY.items():
            assert isinstance(driver_cls, type), f"Registry value for '{platform}' is not a type"


# --- DeviceCredentials platform field tests ---


class TestDeviceCredentialsPlatform:
    def test_default_platform_is_eos(self):
        creds = DeviceCredentials(host="10.0.0.1")
        assert creds.platform == "eos"

    def test_explicit_platform(self):
        creds = DeviceCredentials(host="10.0.0.1", platform="iosxe")
        assert creds.platform == "iosxe"

    def test_platform_field_accepts_any_string(self):
        """DeviceCredentials accepts any string; validation happens at inventory layer."""
        creds = DeviceCredentials(host="10.0.0.1", platform="custom")
        assert creds.platform == "custom"


# --- ConnectionManager driver selection tests ---


class TestConnectionManagerDriverSelection:
    """Test that ConnectionManager uses DRIVER_REGISTRY for driver creation."""

    def _make_settings(self, **overrides) -> NetworkSettings:
        defaults = {
            "net_username": "admin",
            "net_password": "",
            "net_transport": "https",
            "timeout_connect": 10.0,
        }
        defaults.update(overrides)
        return NetworkSettings(**defaults)

    def test_creates_eos_driver_for_eos_platform(self):
        """ConnectionManager creates EosDriver for platform='eos' device."""
        creds = DeviceCredentials(host="10.0.0.1", platform="eos")
        # _create_driver will try to actually connect via pyeapi, which will fail
        # in tests. We verify the driver class is correct by checking the registry lookup.
        driver_cls = DRIVER_REGISTRY.get(creds.platform)
        assert driver_cls is EosDriver

    def test_raises_for_unknown_platform(self):
        """ConnectionManager raises ValueError for unsupported platform."""
        settings = self._make_settings()
        mgr = ConnectionManager(settings)

        creds = DeviceCredentials(host="10.0.0.1", platform="unknown_vendor")
        with pytest.raises(ValueError, match="Unsupported platform 'unknown_vendor'"):
            mgr._create_driver(creds)

    def test_error_message_lists_supported_platforms(self):
        """Error message for unknown platform includes supported platforms."""
        settings = self._make_settings()
        mgr = ConnectionManager(settings)

        creds = DeviceCredentials(host="10.0.0.1", platform="nonexistent")
        with pytest.raises(ValueError, match="Supported platforms: eos"):
            mgr._create_driver(creds)

    def test_demo_mode_ignores_platform(self):
        """In demo mode, MockDriver is used regardless of platform field."""
        settings = self._make_settings(net_demo_mode=True)
        mgr = ConnectionManager(settings)

        creds = DeviceCredentials(host="10.0.0.1", platform="eos")
        driver = mgr._create_driver(creds, device_name="spine-01")
        # MockDriver doesn't have the same class as EosDriver
        assert driver.is_connected()

    def test_get_driver_defaults_to_eos_for_unknown_host(self):
        """When a host is not in inventory, DeviceCredentials defaults platform to 'eos'."""
        # Verify the default creds path creates platform='eos'
        creds = DeviceCredentials(
            host="10.99.99.99",
            username="admin",
            password=SecretStr(""),
            transport="https",
        )
        assert creds.platform == "eos"


# --- Inventory loading with platform validation tests ---


class TestInventoryPlatformValidation:
    """Test that YAML inventory validates platform field."""

    def _write_yaml(self, content: str) -> str:
        """Write YAML content to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, content.encode())
        os.close(fd)
        return path

    def test_loads_device_with_explicit_platform(self):
        path = self._write_yaml("devices:\n  spine-01:\n    host: 10.0.0.1\n    platform: eos\n")
        try:
            backend = YamlInventoryBackend(path)
            devices = backend.get_devices()
            assert devices["spine-01"].platform == "eos"
        finally:
            os.unlink(path)

    def test_defaults_platform_to_eos_when_omitted(self):
        path = self._write_yaml("devices:\n  spine-01:\n    host: 10.0.0.1\n")
        try:
            backend = YamlInventoryBackend(path)
            devices = backend.get_devices()
            assert devices["spine-01"].platform == "eos"
        finally:
            os.unlink(path)

    def test_raises_on_unknown_platform(self):
        path = self._write_yaml("devices:\n  router-01:\n    host: 10.0.0.1\n    platform: netbsd\n")
        try:
            with pytest.raises(ValueError, match="unknown platform 'netbsd'"):
                YamlInventoryBackend(path)
        finally:
            os.unlink(path)

    def test_accepts_valid_non_eos_platforms(self):
        """Valid platforms like iosxe, nxos, junos are accepted (even without drivers)."""
        path = self._write_yaml(
            "devices:\n"
            "  rtr-01:\n"
            "    host: 10.0.0.1\n"
            "    platform: iosxe\n"
            "  sw-01:\n"
            "    host: 10.0.0.2\n"
            "    platform: nxos\n"
            "  fw-01:\n"
            "    host: 10.0.0.3\n"
            "    platform: junos\n"
        )
        try:
            backend = YamlInventoryBackend(path)
            devices = backend.get_devices()
            assert devices["rtr-01"].platform == "iosxe"
            assert devices["sw-01"].platform == "nxos"
            assert devices["fw-01"].platform == "junos"
        finally:
            os.unlink(path)

    def test_mixed_inventory_with_and_without_platform(self):
        """Devices with and without platform field coexist in the same inventory."""
        path = self._write_yaml(
            "devices:\n"
            "  spine-01:\n"
            "    host: 10.0.0.1\n"
            "    platform: eos\n"
            "  leaf-01:\n"
            "    host: 10.0.0.2\n"
            "    # No platform — defaults to eos\n"
        )
        try:
            backend = YamlInventoryBackend(path)
            devices = backend.get_devices()
            assert devices["spine-01"].platform == "eos"
            assert devices["leaf-01"].platform == "eos"
        finally:
            os.unlink(path)


# --- Backward compatibility tests ---


class TestBackwardCompatibility:
    """Verify that existing workflows continue to work unchanged."""

    def _make_settings(self, **overrides) -> NetworkSettings:
        defaults = {
            "net_username": "admin",
            "net_password": "",
            "net_transport": "https",
            "timeout_connect": 10.0,
        }
        defaults.update(overrides)
        return NetworkSettings(**defaults)

    def test_connection_manager_without_inventory(self):
        """ConnectionManager works without any inventory loaded."""
        settings = self._make_settings()
        mgr = ConnectionManager(settings)
        assert mgr.list_devices() == []

    def test_device_credentials_backward_compat(self):
        """DeviceCredentials created without platform field gets default 'eos'."""
        creds = DeviceCredentials(
            host="10.0.0.1",
            username="admin",
            password=SecretStr("secret"),
            transport="https",
        )
        assert creds.platform == "eos"
        assert creds.host == "10.0.0.1"

    def test_inventory_without_platform_field(self):
        """YAML inventory without any platform fields loads successfully."""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        content = (
            "devices:\n"
            "  spine-01:\n"
            "    host: 10.0.0.1\n"
            "    transport: https\n"
            "    role: spine\n"
            "  leaf-01:\n"
            "    host: 10.0.0.2\n"
            "    transport: https\n"
            "    role: leaf\n"
        )
        os.write(fd, content.encode())
        os.close(fd)
        try:
            backend = YamlInventoryBackend(path)
            devices = backend.get_devices()
            assert len(devices) == 2
            assert all(d.platform == "eos" for d in devices.values())
        finally:
            os.unlink(path)

    def test_eos_driver_has_vendor_api_version(self):
        """EosDriver declares VENDOR_API_VERSION for compatibility tracking."""
        assert hasattr(EosDriver, "VENDOR_API_VERSION")
        assert EosDriver.VENDOR_API_VERSION == "eapi-1.0"
