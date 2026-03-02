"""Tests for configurable keepalive interval."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from network_mcp.connection import ConnectionManager
from tests.conftest import make_test_settings


class TestKeepaliveIntervalConfig:
    """Test keepalive_interval setting in NetworkSettings."""

    def test_default_is_60(self):
        s = make_test_settings()
        assert s.keepalive_interval == 60

    def test_custom_value(self):
        s = make_test_settings(keepalive_interval=120)
        assert s.keepalive_interval == 120

    def test_minimum_boundary_accepted(self):
        s = make_test_settings(keepalive_interval=10)
        assert s.keepalive_interval == 10

    def test_maximum_boundary_accepted(self):
        s = make_test_settings(keepalive_interval=600)
        assert s.keepalive_interval == 600

    def test_below_minimum_rejected(self):
        with pytest.raises(ValidationError, match="keepalive_interval"):
            make_test_settings(keepalive_interval=9)

    def test_above_maximum_rejected(self):
        with pytest.raises(ValidationError, match="keepalive_interval"):
            make_test_settings(keepalive_interval=601)


class TestKeepaliveIntervalPropagation:
    """Test that keepalive_interval propagates from settings to drivers."""

    def test_connection_manager_stores_keepalive(self):
        settings = make_test_settings(keepalive_interval=120)
        mgr = ConnectionManager(settings=settings)
        assert mgr._keepalive_interval == 120

    def test_connection_manager_default_keepalive(self):
        settings = make_test_settings()
        mgr = ConnectionManager(settings=settings)
        assert mgr._keepalive_interval == 60

    @patch("network_mcp.driver.pyeapi.connect")
    def test_keepalive_propagates_to_eos_driver(self, mock_connect):
        """Verify keepalive_interval reaches EosDriver via connect() kwargs."""
        mock_node = MagicMock()
        mock_connect.return_value = mock_node

        settings = make_test_settings(keepalive_interval=180)
        mgr = ConnectionManager(settings=settings)
        driver = mgr.get_driver("10.0.0.1")

        assert driver._keepalive_interval == 180


class TestKeepaliveIntervalDrivers:
    """Test keepalive_interval in individual driver constructors."""

    def test_eos_driver_default(self):
        from network_mcp.driver import EosDriver

        d = EosDriver()
        assert d._keepalive_interval == 60

    def test_eos_driver_custom_via_connect(self):
        from network_mcp.driver import EosDriver

        d = EosDriver()
        with patch("network_mcp.driver.pyeapi.connect", return_value=MagicMock()):
            d.connect("10.0.0.1", "https", "admin", "pass", keepalive_interval=200)
        assert d._keepalive_interval == 200

    def test_iosxe_driver_default(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        d = IosXeDriver()
        assert d._keepalive_interval == 60

    def test_iosxe_driver_custom_via_connect(self):
        from network_mcp.drivers.cisco_iosxe import IosXeDriver

        d = IosXeDriver()
        with (
            patch("network_mcp.drivers.cisco_iosxe.HAS_HTTPX", False),
            patch("network_mcp.drivers.cisco_iosxe.HAS_SCRAPLI", False),
        ):
            d.connect("10.0.0.1", "ssh", "admin", "pass", keepalive_interval=300)
        assert d._keepalive_interval == 300

    def test_nxos_driver_default(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        d = NxosDriver()
        assert d._keepalive_interval == 60

    def test_nxos_driver_custom_via_connect(self):
        from network_mcp.drivers.cisco_nxos import NxosDriver

        d = NxosDriver()
        with (
            patch("network_mcp.drivers.cisco_nxos.HAS_HTTPX", False),
            patch("network_mcp.drivers.cisco_nxos.HAS_SCRAPLI", False),
        ):
            d.connect("10.0.0.1", "ssh", "admin", "pass", keepalive_interval=150)
        assert d._keepalive_interval == 150

    def test_junos_driver_default(self):
        from network_mcp.drivers.juniper_junos import JunosDriver

        d = JunosDriver()
        assert d._keepalive_interval == 60

    def test_junos_driver_custom_via_connect(self):
        from network_mcp.drivers.juniper_junos import JunosDriver

        d = JunosDriver()
        with (
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI_NETCONF", False),
            patch("network_mcp.drivers.juniper_junos.HAS_SCRAPLI", False),
        ):
            d.connect("10.0.0.1", "ssh", "admin", "pass", keepalive_interval=400)
        assert d._keepalive_interval == 400
