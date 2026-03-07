"""Tests for vendor-agnostic common tools (net_* prefix).

Validates that common tools dispatch to NetworkDriver normalized getters
and return the expected response structure with vendor/platform metadata.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr

# --- Mock normalized getter responses ---

MOCK_FACTS = {
    "hostname": "spine-01",
    "model": "DCS-7050TX3-48C8",
    "version": "4.32.1F",
    "serial": "SSJ17856312",
    "uptime_seconds": 864000,
    "vendor": "arista",
    "platform": "eos",
}

MOCK_INTERFACES = {
    "Ethernet1": {
        "is_up": True,
        "is_enabled": True,
        "description": "uplink to spine-02",
        "speed": 100000,
        "mtu": 9214,
        "mac_address": "00:1c:73:aa:bb:01",
    },
    "Ethernet2": {
        "is_up": False,
        "is_enabled": False,
        "description": "",
        "speed": 1000,
        "mtu": 1500,
        "mac_address": "00:1c:73:aa:bb:02",
    },
    "Management1": {
        "is_up": True,
        "is_enabled": True,
        "description": "oob-mgmt",
        "speed": 1000,
        "mtu": 1500,
        "mac_address": "00:1c:73:aa:bb:ff",
    },
}

MOCK_BGP_SUMMARY = {
    "router_id": "10.0.0.1",
    "as_number": 65000,
    "peers": {
        "10.0.0.2": {
            "state": "Established",
            "prefixes_received": 150,
            "uptime": 1234567,
        },
        "10.0.0.3": {
            "state": "Active",
            "prefixes_received": 0,
            "uptime": 100,
        },
    },
}

MOCK_BGP_SUMMARY_VRF = {
    "router_id": "10.100.0.1",
    "as_number": 65100,
    "peers": {
        "10.100.0.2": {
            "state": "Established",
            "prefixes_received": 42,
            "uptime": 999999,
        },
    },
}

MOCK_LLDP_NEIGHBORS = {
    "Ethernet1": [
        {
            "hostname": "spine-02.lab",
            "port": "Ethernet49/1",
            "system_description": "Arista Networks EOS version 4.32.1F",
        },
    ],
    "Ethernet2": [
        {
            "hostname": "leaf-01.lab",
            "port": "Ethernet1",
            "system_description": "Arista Networks EOS version 4.31.0F",
        },
    ],
}


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with normalized getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_facts.return_value = MOCK_FACTS
    driver.get_interfaces.return_value = MOCK_INTERFACES
    driver.get_bgp_summary.return_value = MOCK_BGP_SUMMARY
    driver.get_lldp_neighbors.return_value = MOCK_LLDP_NEIGHBORS
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# --- net_get_device_info ---


class TestNetGetDeviceInfo:
    """Test the net_get_device_info common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.device import net_get_device_info

        result = net_get_device_info("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert result["data"] == MOCK_FACTS

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.device import net_get_device_info

        result = net_get_device_info("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_error_response(self):
        from network_mcp.tools.common.device import net_get_device_info

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("Device unreachable")):
            result = net_get_device_info("bad-host")
        assert result["status"] == "error"
        assert result["device"] == "bad-host"
        assert "Device unreachable" in result["error"]

    def test_calls_get_facts(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.device import net_get_device_info

        net_get_device_info("spine-01")
        mock_driver.get_facts.assert_called_once()


# --- net_get_interfaces ---


class TestNetGetInterfaces:
    """Test the net_get_interfaces common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert "Ethernet1" in result["data"]
        assert "Ethernet2" in result["data"]
        assert "Management1" in result["data"]

    def test_fields_filter(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces("spine-01", fields=["is_up", "speed"])
        assert result["status"] == "success"
        for intf_data in result["data"].values():
            assert set(intf_data.keys()) == {"is_up", "speed"}

    def test_fields_filter_single_field(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces("spine-01", fields=["description"])
        assert result["status"] == "success"
        eth1 = result["data"]["Ethernet1"]
        assert set(eth1.keys()) == {"description"}
        assert eth1["description"] == "uplink to spine-02"

    def test_no_fields_returns_all(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        result = net_get_interfaces("spine-01")
        expected_keys = {"is_up", "is_enabled", "description", "speed", "mtu", "mac_address"}
        for intf_data in result["data"].values():
            assert set(intf_data.keys()) == expected_keys

    def test_error_response(self):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=Exception("timeout")):
            result = net_get_interfaces("bad-host")
        assert result["status"] == "error"
        assert "timeout" in result["error"]


# --- net_get_interface_detail ---


class TestNetGetInterfaceDetail:
    """Test the net_get_interface_detail common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_detail

        result = net_get_interface_detail("spine-01", "Ethernet1")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert "Ethernet1" in result["data"]
        assert result["data"]["Ethernet1"]["is_up"] is True

    def test_interface_not_found(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_detail

        result = net_get_interface_detail("spine-01", "Ethernet99")
        assert result["status"] == "error"
        assert "Ethernet99" in result["error"]
        assert "not found" in result["error"]

    def test_error_response(self):
        from network_mcp.tools.common.interfaces import net_get_interface_detail

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_interface_detail("bad-host", "Ethernet1")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]


# --- net_get_bgp_summary ---


class TestNetGetBgpSummary:
    """Test the net_get_bgp_summary common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_bgp_summary

        result = net_get_bgp_summary("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert result["data"]["router_id"] == "10.0.0.1"
        assert result["data"]["as_number"] == 65000
        assert len(result["data"]["peers"]) == 2

    def test_vrf_parameter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_bgp_summary

        mock_driver.get_bgp_summary.return_value = MOCK_BGP_SUMMARY_VRF
        result = net_get_bgp_summary("spine-01", vrf="PROD")
        assert result["status"] == "success"
        assert result["data"]["router_id"] == "10.100.0.1"
        assert result["data"]["as_number"] == 65100
        mock_driver.get_bgp_summary.assert_called_once_with(vrf="PROD")

    def test_default_vrf(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_bgp_summary

        net_get_bgp_summary("spine-01")
        mock_driver.get_bgp_summary.assert_called_once_with(vrf="default")

    def test_error_response(self):
        from network_mcp.tools.common.routing import net_get_bgp_summary

        with patch.object(conn_mgr, "get_driver", side_effect=Exception("connection refused")):
            result = net_get_bgp_summary("bad-host")
        assert result["status"] == "error"
        assert "connection refused" in result["error"]


# --- net_get_lldp_neighbors ---


class TestNetGetLldpNeighbors:
    """Test the net_get_lldp_neighbors common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        result = net_get_lldp_neighbors("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert "Ethernet1" in result["data"]
        assert "Ethernet2" in result["data"]
        assert result["data"]["Ethernet1"][0]["hostname"] == "spine-02.lab"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        result = net_get_lldp_neighbors("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_error_response(self):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        with patch.object(conn_mgr, "get_driver", side_effect=Exception("auth failed")):
            result = net_get_lldp_neighbors("bad-host")
        assert result["status"] == "error"
        assert "auth failed" in result["error"]

    def test_calls_get_lldp_neighbors(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        net_get_lldp_neighbors("spine-01")
        mock_driver.get_lldp_neighbors.assert_called_once()
        mock_driver.get_interfaces.assert_called_once()

    def test_speed_included_when_interface_data_available(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        result = net_get_lldp_neighbors("spine-01")
        # Ethernet1 has speed=100000 Mbps → "100G"; Ethernet2 has speed=1000 Mbps → "1G"
        assert result["data"]["Ethernet1"][0]["local_port_speed"] == "100G"
        assert result["data"]["Ethernet2"][0]["local_port_speed"] == "1G"

    def test_speed_null_when_interface_data_unavailable(self, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        mock_driver.get_interfaces.side_effect = Exception("not supported")
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_lldp_neighbors("spine-01")
        assert result["status"] == "success"
        for neighbor_list in result["data"].values():
            for neighbor in neighbor_list:
                assert neighbor["local_port_speed"] is None


# --- _filter_fields utility ---


class TestFilterFields:
    """Test the _filter_fields utility in common __init__.py."""

    def test_filter_subset(self):
        from network_mcp.tools.common import _filter_fields

        data = {
            "Ethernet1": {"is_up": True, "speed": 1000, "mtu": 9214, "description": "test"},
        }
        result = _filter_fields(data, ["is_up", "speed"])
        assert result == {"Ethernet1": {"is_up": True, "speed": 1000}}

    def test_filter_nonexistent_field(self):
        from network_mcp.tools.common import _filter_fields

        data = {"Ethernet1": {"is_up": True, "speed": 1000}}
        result = _filter_fields(data, ["nonexistent"])
        assert result == {"Ethernet1": {}}

    def test_filter_empty_fields(self):
        from network_mcp.tools.common import _filter_fields

        data = {"Ethernet1": {"is_up": True, "speed": 1000}}
        result = _filter_fields(data, [])
        assert result == {"Ethernet1": {}}

    def test_filter_empty_data(self):
        from network_mcp.tools.common import _filter_fields

        result = _filter_fields({}, ["is_up"])
        assert result == {}


# --- handle_tool_errors decorator tests ---


class TestHandleToolErrorsOnCommonTools:
    """Verify @handle_tool_errors wraps common tools with standardized error dicts."""

    def test_net_get_device_info_timeout_error(self):
        from network_mcp.tools.common.device import net_get_device_info

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_device_info("spine-01")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "Timeout" in result["error"]
        assert "timed out" in result["error"]

    def test_net_get_interfaces_unexpected_error(self):
        from network_mcp.tools.common.interfaces import net_get_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = net_get_interfaces("leaf-01")
        assert result["status"] == "error"
        assert result["device"] == "leaf-01"
        assert "Unexpected error" in result["error"]
        assert "driver crash" in result["error"]

    def test_net_get_interface_detail_timeout_error(self):
        from network_mcp.tools.common.interfaces import net_get_interface_detail

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("connection timed out")):
            result = net_get_interface_detail("leaf-01", "Ethernet1")
        assert result["status"] == "error"
        assert result["device"] == "leaf-01"
        assert "Timeout" in result["error"]

    def test_net_get_bgp_summary_unexpected_error(self):
        from network_mcp.tools.common.routing import net_get_bgp_summary

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver init failed")):
            result = net_get_bgp_summary("spine-01")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "Unexpected error" in result["error"]

    def test_net_get_lldp_neighbors_timeout_error(self):
        from network_mcp.tools.common.switching import net_get_lldp_neighbors

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("read timeout")):
            result = net_get_lldp_neighbors("spine-01")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "Timeout" in result["error"]
