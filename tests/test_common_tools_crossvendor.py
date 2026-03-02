"""Cross-vendor common tool tests.

Parameterize each vendor-agnostic tool test across all 3 mock drivers to verify
each tool returns consistent structure regardless of the underlying platform.
Uses patch.object on conn_mgr to inject the mock driver for each vendor.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from network_mcp.server import conn_mgr
from network_mcp.tools.common.device import net_get_device_info
from network_mcp.tools.common.interfaces import net_get_interface_detail, net_get_interfaces
from network_mcp.tools.common.routing import net_get_bgp_summary
from network_mcp.tools.common.switching import net_get_lldp_neighbors
from tests.mock_data.eos.mock_driver import MockEosDriver
from tests.mock_data.iosxe.mock_driver import MockIosXeDriver
from tests.mock_data.nxos.mock_driver import MockNxosDriver

# Create and connect all mock drivers
_eos = MockEosDriver()
_eos.connect()
_iosxe = MockIosXeDriver()
_iosxe.connect()
_nxos = MockNxosDriver()
_nxos.connect()

ALL_DRIVERS = [
    pytest.param(_eos, id="eos"),
    pytest.param(_iosxe, id="iosxe"),
    pytest.param(_nxos, id="nxos"),
]


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestNetGetDeviceInfo:
    """Verify net_get_device_info returns consistent structure across vendors."""

    def test_success_status(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_device_info("test-host")
        assert result["status"] == "success"

    def test_required_keys(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_device_info("test-host")
        assert "device" in result
        assert "vendor" in result
        assert "platform" in result
        assert "data" in result

    def test_device_matches_host(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_device_info("test-host")
        assert result["device"] == "test-host"

    def test_vendor_is_valid(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_device_info("test-host")
        assert result["vendor"] in ("arista", "cisco")

    def test_platform_is_valid(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_device_info("test-host")
        assert result["platform"] in ("eos", "iosxe", "nxos")

    def test_data_has_facts(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_device_info("test-host")
        data = result["data"]
        assert "hostname" in data
        assert "model" in data
        assert "version" in data


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestNetGetInterfaces:
    """Verify net_get_interfaces returns consistent structure across vendors."""

    def test_success_status(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_interfaces("test-host")
        assert result["status"] == "success"

    def test_required_keys(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_interfaces("test-host")
        assert "device" in result
        assert "vendor" in result
        assert "platform" in result
        assert "data" in result

    def test_data_is_dict(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_interfaces("test-host")
        assert isinstance(result["data"], dict)

    def test_interface_fields(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_interfaces("test-host")
        for _intf_name, intf_data in result["data"].items():
            assert "is_up" in intf_data
            assert "is_enabled" in intf_data
            assert "speed" in intf_data

    def test_field_filtering(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_interfaces("test-host", fields=["is_up", "speed"])
        for _intf_name, intf_data in result["data"].items():
            assert set(intf_data.keys()) <= {"is_up", "speed"}


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestNetGetInterfaceDetail:
    """Verify net_get_interface_detail returns consistent structure across vendors."""

    def test_nonexistent_interface_returns_error(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_interface_detail("test-host", "NonExistent99")
        assert result["status"] == "error"
        assert "not found" in result["error"]


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestNetGetBgpSummary:
    """Verify net_get_bgp_summary returns consistent structure across vendors."""

    def test_success_status(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_bgp_summary("test-host")
        assert result["status"] == "success"

    def test_required_keys(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_bgp_summary("test-host")
        assert "device" in result
        assert "vendor" in result
        assert "platform" in result
        assert "data" in result

    def test_data_has_bgp_fields(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_bgp_summary("test-host")
        data = result["data"]
        assert "router_id" in data
        assert "as_number" in data
        assert "peers" in data


@pytest.mark.parametrize("driver", ALL_DRIVERS)
class TestNetGetLldpNeighbors:
    """Verify net_get_lldp_neighbors returns consistent structure across vendors."""

    def test_success_status(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_lldp_neighbors("test-host")
        assert result["status"] == "success"

    def test_required_keys(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_lldp_neighbors("test-host")
        assert "device" in result
        assert "vendor" in result
        assert "platform" in result
        assert "data" in result

    def test_data_is_dict(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_lldp_neighbors("test-host")
        assert isinstance(result["data"], dict)

    def test_neighbor_fields(self, driver):
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_get_lldp_neighbors("test-host")
        for _intf, neighbors in result["data"].items():
            assert isinstance(neighbors, list)
            for neighbor in neighbors:
                assert "hostname" in neighbor
                assert "port" in neighbor
