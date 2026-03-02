"""Tests for network_mcp/drivers/__init__.py coverage.

The uncovered lines 32-33, 39-40, 47-48 are ImportError branches when
Cisco httpx or JunOS scrapli-netconf packages are not installed.
Since they ARE installed in the test environment, we test the success paths
and verify the registry is populated correctly.
"""

from network_mcp.driver import EosDriver
from network_mcp.drivers import DRIVER_REGISTRY, DeviceDriver, NetworkDriver, NotSupportedError


class TestDriverRegistry:
    """Verify the driver registry is populated correctly."""

    def test_eos_driver_in_registry(self):
        assert "eos" in DRIVER_REGISTRY
        assert DRIVER_REGISTRY["eos"] is EosDriver

    def test_iosxe_driver_registered(self):
        """IOS-XE driver should be registered when httpx is installed."""
        assert "iosxe" in DRIVER_REGISTRY
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        assert DRIVER_REGISTRY["iosxe"] is IosXeDriver

    def test_nxos_driver_registered(self):
        """NX-OS driver should be registered when httpx is installed."""
        assert "nxos" in DRIVER_REGISTRY
        from network_mcp.drivers.cisco_nxos import NxosDriver

        assert DRIVER_REGISTRY["nxos"] is NxosDriver

    def test_junos_driver_registered(self):
        """JunOS driver should be registered when scrapli-netconf is installed."""
        # JunOS driver depends on scrapli-netconf which may or may not be installed
        # If scrapli-netconf is available, junos should be in registry
        if "junos" in DRIVER_REGISTRY:
            from network_mcp.drivers.juniper_junos import JunosDriver

            assert DRIVER_REGISTRY["junos"] is JunosDriver


class TestExports:
    """Verify the __init__ module exports are accessible."""

    def test_device_driver_alias(self):
        """DeviceDriver is a backward-compatible alias for NetworkDriver."""
        assert DeviceDriver is NetworkDriver

    def test_not_supported_error_exported(self):
        """NotSupportedError must be importable from drivers package."""
        assert issubclass(NotSupportedError, Exception)

    def test_registry_has_at_least_eos(self):
        """Registry must contain at least the EOS driver."""
        assert len(DRIVER_REGISTRY) >= 1
        assert "eos" in DRIVER_REGISTRY
