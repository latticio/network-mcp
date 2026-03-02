"""Tests for new vendor-agnostic interface tools.

Tests net_get_interface_counters, net_get_interface_errors,
net_get_ip_interfaces, and net_get_transceiver_info.
Validates pagination, NotSupportedError handling, and response structure.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr

# --- Mock data ---

MOCK_COUNTERS = [
    {
        "interface": "Ethernet1",
        "in_octets": 1_000_000_000,
        "out_octets": 2_000_000_000,
        "in_packets": 5_000_000,
        "out_packets": 6_000_000,
        "in_broadcast": 100,
        "out_broadcast": 200,
        "in_multicast": 50_000,
        "out_multicast": 60_000,
    },
    {
        "interface": "Ethernet2",
        "in_octets": 500_000,
        "out_octets": 300_000,
        "in_packets": 1_000,
        "out_packets": 800,
        "in_broadcast": 10,
        "out_broadcast": 5,
        "in_multicast": 100,
        "out_multicast": 50,
    },
    {
        "interface": "Ethernet3",
        "in_octets": 0,
        "out_octets": 0,
        "in_packets": 0,
        "out_packets": 0,
        "in_broadcast": 0,
        "out_broadcast": 0,
        "in_multicast": 0,
        "out_multicast": 0,
    },
    {
        "interface": "Management1",
        "in_octets": 10_000_000,
        "out_octets": 8_000_000,
        "in_packets": 50_000,
        "out_packets": 40_000,
        "in_broadcast": 500,
        "out_broadcast": 100,
        "in_multicast": 1_000,
        "out_multicast": 500,
    },
]

MOCK_ERRORS = [
    {
        "interface": "Ethernet1",
        "in_errors": 0,
        "out_errors": 0,
        "in_crc": 0,
        "in_frame": 0,
        "in_runts": 0,
        "in_giants": 0,
    },
    {
        "interface": "Ethernet2",
        "in_errors": 42,
        "out_errors": 3,
        "in_crc": 10,
        "in_frame": 5,
        "in_runts": 2,
        "in_giants": 1,
    },
    {
        "interface": "Ethernet3",
        "in_errors": 0,
        "out_errors": 0,
        "in_crc": 0,
        "in_frame": 0,
        "in_runts": 0,
        "in_giants": 0,
    },
]

MOCK_IP_INTERFACES = [
    {
        "interface": "Loopback0",
        "ip_address": "10.0.0.1/32",
        "subnet": "32",
        "vrf": "default",
        "status": "up",
    },
    {
        "interface": "Vlan10",
        "ip_address": "192.168.10.1/24",
        "subnet": "24",
        "vrf": "MGMT",
        "status": "up",
    },
    {
        "interface": "Ethernet1",
        "ip_address": "10.1.1.1/30",
        "subnet": "30",
        "vrf": "default",
        "status": "up",
    },
]

MOCK_TRANSCEIVERS = [
    {
        "interface": "Ethernet1",
        "type": "10GBASE-SR",
        "tx_power": -2.1,
        "rx_power": -3.5,
        "temp": 32.5,
    },
    {
        "interface": "Ethernet2",
        "type": "10GBASE-LR",
        "tx_power": -1.8,
        "rx_power": -8.2,
        "temp": 35.0,
    },
    {
        "interface": "Ethernet49",
        "type": "100GBASE-SR4",
        "tx_power": -1.5,
        "rx_power": -2.0,
        "temp": 28.7,
    },
]


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with interface getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_interface_counters.return_value = MOCK_COUNTERS
    driver.get_interface_errors.return_value = MOCK_ERRORS
    driver.get_ip_interfaces.return_value = MOCK_IP_INTERFACES
    driver.get_transceiver_info.return_value = MOCK_TRANSCEIVERS
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# --- net_get_interface_counters ---


class TestNetGetInterfaceCounters:
    """Test the net_get_interface_counters common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 4

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data", "pagination"}

    def test_calls_get_interface_counters(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        net_get_interface_counters("spine-01")
        mock_driver.get_interface_counters.assert_called_once()

    def test_counter_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01")
        entry = result["data"][0]
        assert entry["interface"] == "Ethernet1"
        assert entry["in_octets"] == 1_000_000_000
        assert entry["out_octets"] == 2_000_000_000
        assert entry["in_packets"] == 5_000_000

    def test_pagination_page_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01", page=1, page_size=2)
        assert len(result["data"]) == 2
        assert result["data"][0]["interface"] == "Ethernet1"
        assert result["data"][1]["interface"] == "Ethernet2"
        assert result["pagination"]["total"] == 4
        assert result["pagination"]["has_more"] is True

    def test_pagination_page_2(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01", page=2, page_size=2)
        assert len(result["data"]) == 2
        assert result["data"][0]["interface"] == "Ethernet3"
        assert result["data"][1]["interface"] == "Management1"
        assert result["pagination"]["has_more"] is False

    def test_pagination_empty_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01", page=100, page_size=2)
        assert len(result["data"]) == 0
        assert result["pagination"]["returned"] == 0
        assert result["pagination"]["has_more"] is False

    def test_page_less_than_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01", page=0)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_size_zero(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01", page_size=0)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_page_size_too_large(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        result = net_get_interface_counters("spine-01", page_size=501)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        mock_driver.get_interface_counters.side_effect = NotSupportedError("not available")
        result = net_get_interface_counters("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_interface_counters("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_interface_counters("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_interface_counters("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_empty_counters(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_interface_counters

        mock_driver.get_interface_counters.return_value = []
        result = net_get_interface_counters("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []
        assert result["pagination"]["total"] == 0


# --- net_get_interface_errors ---


class TestNetGetInterfaceErrors:
    """Test the net_get_interface_errors common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 3

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data", "pagination"}

    def test_calls_get_interface_errors(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        net_get_interface_errors("spine-01")
        mock_driver.get_interface_errors.assert_called_once()

    def test_error_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors("spine-01")
        entry = result["data"][1]  # Ethernet2 has errors
        assert entry["interface"] == "Ethernet2"
        assert entry["in_errors"] == 42
        assert entry["out_errors"] == 3
        assert entry["in_crc"] == 10
        assert entry["in_frame"] == 5
        assert entry["in_runts"] == 2
        assert entry["in_giants"] == 1

    def test_pagination_page_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors("spine-01", page=1, page_size=2)
        assert len(result["data"]) == 2
        assert result["pagination"]["total"] == 3
        assert result["pagination"]["has_more"] is True

    def test_pagination_last_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors("spine-01", page=2, page_size=2)
        assert len(result["data"]) == 1
        assert result["pagination"]["has_more"] is False

    def test_pagination_empty_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors("spine-01", page=50, page_size=2)
        assert len(result["data"]) == 0
        assert result["pagination"]["returned"] == 0

    def test_page_less_than_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors("spine-01", page=0)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_size_out_of_range(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        result = net_get_interface_errors("spine-01", page_size=501)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        mock_driver.get_interface_errors.side_effect = NotSupportedError("not available")
        result = net_get_interface_errors("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_interface_errors("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_interface_errors("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_empty_errors(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_interface_errors

        mock_driver.get_interface_errors.return_value = []
        result = net_get_interface_errors("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []


# --- net_get_ip_interfaces ---


class TestNetGetIpInterfaces:
    """Test the net_get_ip_interfaces common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        result = net_get_ip_interfaces("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 3

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        result = net_get_ip_interfaces("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_ip_interfaces(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        net_get_ip_interfaces("spine-01")
        mock_driver.get_ip_interfaces.assert_called_once()

    def test_ip_interface_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        result = net_get_ip_interfaces("spine-01")
        loopback = result["data"][0]
        assert loopback["interface"] == "Loopback0"
        assert loopback["ip_address"] == "10.0.0.1/32"
        assert loopback["vrf"] == "default"
        assert loopback["status"] == "up"

    def test_vrf_assignment(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        result = net_get_ip_interfaces("spine-01")
        vlan10 = result["data"][1]
        assert vlan10["interface"] == "Vlan10"
        assert vlan10["vrf"] == "MGMT"

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        mock_driver.get_ip_interfaces.side_effect = NotSupportedError("not available")
        result = net_get_ip_interfaces("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_ip_interfaces("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_ip_interfaces("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_ip_interfaces("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_empty_ip_interfaces(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_ip_interfaces

        mock_driver.get_ip_interfaces.return_value = []
        result = net_get_ip_interfaces("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []


# --- net_get_transceiver_info ---


class TestNetGetTransceiverInfo:
    """Test the net_get_transceiver_info common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        result = net_get_transceiver_info("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 3

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        result = net_get_transceiver_info("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_transceiver_info(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        net_get_transceiver_info("spine-01")
        mock_driver.get_transceiver_info.assert_called_once()

    def test_transceiver_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        result = net_get_transceiver_info("spine-01")
        entry = result["data"][0]
        assert entry["interface"] == "Ethernet1"
        assert entry["type"] == "10GBASE-SR"
        assert entry["tx_power"] == -2.1
        assert entry["rx_power"] == -3.5
        assert entry["temp"] == 32.5

    def test_100g_transceiver(self, mock_common_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        result = net_get_transceiver_info("spine-01")
        entry = result["data"][2]
        assert entry["interface"] == "Ethernet49"
        assert entry["type"] == "100GBASE-SR4"

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        mock_driver.get_transceiver_info.side_effect = NotSupportedError("not available")
        result = net_get_transceiver_info("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_transceiver_info("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_transceiver_info("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_transceiver_info("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_empty_transceivers(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.interfaces import net_get_transceiver_info

        mock_driver.get_transceiver_info.return_value = []
        result = net_get_transceiver_info("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []


# --- net_get_trunk_interfaces ---

MOCK_TRUNKS = [
    {
        "interface": "Ethernet1",
        "mode": "trunk",
        "native_vlan": 1,
        "allowed_vlans": "1-100,200",
        "active_vlans": "1,10,20,100,200",
    },
    {
        "interface": "Port-Channel1",
        "mode": "trunk",
        "native_vlan": 99,
        "allowed_vlans": "ALL",
        "active_vlans": "1,10,20,30,99",
    },
    {
        "interface": "Ethernet49",
        "mode": "trunk",
        "native_vlan": 1,
        "allowed_vlans": "1-4094",
        "active_vlans": "1,10,20",
    },
]


@pytest.fixture
def mock_trunk_driver():
    """Create a mock NetworkDriver with trunk getter."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_trunk_interfaces.return_value = MOCK_TRUNKS
    return driver


@pytest.fixture
def mock_trunk_conn_mgr(mock_trunk_driver):
    """Patch conn_mgr.get_driver for trunk tests."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_trunk_driver) as patched:
        yield patched


class TestNetGetTrunkInterfaces:
    """Test the net_get_trunk_interfaces common tool."""

    def test_success_response(self, mock_trunk_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        result = net_get_trunk_interfaces("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 3

    def test_response_keys(self, mock_trunk_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        result = net_get_trunk_interfaces("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_trunk_interfaces(self, mock_trunk_conn_mgr, mock_trunk_driver):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        net_get_trunk_interfaces("spine-01")
        mock_trunk_driver.get_trunk_interfaces.assert_called_once()

    def test_trunk_data_structure(self, mock_trunk_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        result = net_get_trunk_interfaces("spine-01")
        entry = result["data"][0]
        assert entry["interface"] == "Ethernet1"
        assert entry["mode"] == "trunk"
        assert entry["native_vlan"] == 1
        assert entry["allowed_vlans"] == "1-100,200"
        assert entry["active_vlans"] == "1,10,20,100,200"

    def test_port_channel_trunk(self, mock_trunk_conn_mgr):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        result = net_get_trunk_interfaces("spine-01")
        entry = result["data"][1]
        assert entry["interface"] == "Port-Channel1"
        assert entry["native_vlan"] == 99
        assert entry["allowed_vlans"] == "ALL"

    def test_not_supported(self, mock_trunk_conn_mgr, mock_trunk_driver):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        mock_trunk_driver.get_trunk_interfaces.side_effect = NotSupportedError("not available")
        result = net_get_trunk_interfaces("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_trunk_interfaces("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_trunk_interfaces("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_trunk_interfaces("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_empty_trunks(self, mock_trunk_conn_mgr, mock_trunk_driver):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        mock_trunk_driver.get_trunk_interfaces.return_value = []
        result = net_get_trunk_interfaces("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []

    def test_cisco_vendor(self, mock_trunk_driver):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        mock_trunk_driver.vendor = "cisco"
        mock_trunk_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_trunk_driver):
            result = net_get_trunk_interfaces("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_trunk_driver):
        from network_mcp.tools.common.interfaces import net_get_trunk_interfaces

        mock_trunk_driver.vendor = "juniper"
        mock_trunk_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_trunk_driver):
            result = net_get_trunk_interfaces("junos-switch")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"
