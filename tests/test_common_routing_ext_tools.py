"""Tests for vendor-agnostic routing tools (extended).

Tests net_get_ospf_neighbors, net_get_ospf_interfaces, net_get_route_table,
net_get_route_summary, net_get_arp_table, and net_get_bgp_neighbors.
Validates pagination, protocol filtering, VRF parameter, NotSupportedError
handling, empty results, and standard response structure.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr

# --- Mock data ---

MOCK_OSPF_NEIGHBORS = [
    {
        "neighbor_id": "10.0.0.1",
        "state": "Full",
        "interface": "Ethernet1",
        "area": "0.0.0.0",  # noqa: S104
        "uptime": "01:15:30",
    },
    {
        "neighbor_id": "10.0.0.2",
        "state": "Full",
        "interface": "Ethernet2",
        "area": "0.0.0.0",  # noqa: S104
        "uptime": "00:45:12",
    },
    {
        "neighbor_id": "10.0.0.3",
        "state": "Init",
        "interface": "Ethernet3",
        "area": "0.0.0.1",
        "uptime": "00:00:05",
    },
]

MOCK_OSPF_INTERFACES = [
    {
        "interface": "Ethernet1",
        "area": "0.0.0.0",  # noqa: S104
        "network_type": "point-to-point",
        "cost": 10,
        "neighbor_count": 1,
    },
    {
        "interface": "Ethernet2",
        "area": "0.0.0.0",  # noqa: S104
        "network_type": "broadcast",
        "cost": 20,
        "neighbor_count": 2,
    },
    {
        "interface": "Loopback0",
        "area": "0.0.0.0",  # noqa: S104
        "network_type": "loopback",
        "cost": 1,
        "neighbor_count": 0,
    },
    {
        "interface": "Vlan100",
        "area": "0.0.0.1",
        "network_type": "broadcast",
        "cost": 10,
        "neighbor_count": 3,
    },
]

MOCK_ROUTE_SUMMARY = {
    "total": 150,
    "connected": 10,
    "static": 5,
    "bgp": 100,
    "ospf": 30,
    "isis": 5,
}

MOCK_ARP_TABLE = [
    {"ip": "10.0.0.1", "mac": "00:1a:2b:3c:4d:5e", "interface": "Ethernet1", "age": 120.5},
    {"ip": "10.0.0.2", "mac": "00:1a:2b:3c:4d:5f", "interface": "Ethernet2", "age": 60.0},
    {"ip": "10.0.0.3", "mac": "00:1a:2b:3c:4d:60", "interface": "Ethernet3", "age": -1},
    {"ip": "192.168.1.1", "mac": "aa:bb:cc:dd:ee:ff", "interface": "Vlan10", "age": 300.0},
    {"ip": "192.168.1.2", "mac": "aa:bb:cc:dd:ee:01", "interface": "Vlan10", "age": 45.0},
]

MOCK_BGP_NEIGHBORS = [
    {
        "neighbor": "10.0.0.1",
        "remote_as": 65001,
        "state": "Established",
        "prefixes_received": 1500,
        "prefixes_sent": 200,
        "uptime": "3d12h",
        "description": "spine-01",
    },
    {
        "neighbor": "10.0.0.2",
        "remote_as": 65002,
        "state": "Established",
        "prefixes_received": 800,
        "prefixes_sent": 200,
        "uptime": "1d06h",
        "description": "spine-02",
    },
    {
        "neighbor": "10.0.0.3",
        "remote_as": 65003,
        "state": "Active",
        "prefixes_received": 0,
        "prefixes_sent": 0,
        "uptime": "",
        "description": "",
    },
]


def _generate_large_route_table(count: int) -> list[dict]:
    """Generate a large route table for pagination tests."""
    return [
        {
            "prefix": f"10.{i // 256}.{i % 256}.0/24",
            "next_hop": "10.0.0.1",
            "protocol": "bgp" if i % 3 == 0 else ("ospf" if i % 3 == 1 else "connected"),
            "metric": i * 10,
            "preference": 20 if i % 3 == 0 else (110 if i % 3 == 1 else 0),
        }
        for i in range(count)
    ]


def _generate_large_arp_table(count: int) -> list[dict]:
    """Generate a large ARP table for pagination tests."""
    return [
        {
            "ip": f"10.{i // 256}.{i % 256}.1",
            "mac": f"00:11:22:33:{i // 256:02x}:{i % 256:02x}",
            "interface": f"Ethernet{(i % 48) + 1}",
            "age": float(i * 10),
        }
        for i in range(count)
    ]


MOCK_ROUTES_SMALL = [
    {"prefix": "10.0.0.0/24", "next_hop": "10.0.0.1", "protocol": "connected", "metric": 0, "preference": 0},
    {"prefix": "10.1.0.0/24", "next_hop": "10.0.0.1", "protocol": "bgp", "metric": 100, "preference": 20},
    {"prefix": "10.2.0.0/24", "next_hop": "10.0.0.2", "protocol": "ospf", "metric": 50, "preference": 110},
    {"prefix": "10.3.0.0/24", "next_hop": "10.0.0.1", "protocol": "static", "metric": 0, "preference": 1},
    {"prefix": "0.0.0.0/0", "next_hop": "10.0.0.1", "protocol": "static", "metric": 0, "preference": 1},
]


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with routing getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_ospf_neighbors.return_value = MOCK_OSPF_NEIGHBORS
    driver.get_ospf_interfaces.return_value = MOCK_OSPF_INTERFACES
    driver.get_route_table.return_value = MOCK_ROUTES_SMALL
    driver.get_route_summary.return_value = MOCK_ROUTE_SUMMARY
    driver.get_arp_table.return_value = MOCK_ARP_TABLE
    driver.get_bgp_neighbors.return_value = MOCK_BGP_NEIGHBORS
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# ===========================================================================
# net_get_ospf_neighbors
# ===========================================================================


class TestNetGetOspfNeighbors:
    """Test the net_get_ospf_neighbors common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        result = net_get_ospf_neighbors("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 3

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        result = net_get_ospf_neighbors("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_ospf_neighbors(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        net_get_ospf_neighbors("spine-01")
        mock_driver.get_ospf_neighbors.assert_called_once_with(vrf="default")

    def test_vrf_parameter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        net_get_ospf_neighbors("spine-01", vrf="MGMT")
        mock_driver.get_ospf_neighbors.assert_called_once_with(vrf="MGMT")

    def test_neighbor_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        result = net_get_ospf_neighbors("spine-01")
        entry = result["data"][0]
        assert entry["neighbor_id"] == "10.0.0.1"
        assert entry["state"] == "Full"
        assert entry["interface"] == "Ethernet1"
        assert entry["area"] == "0.0.0.0"  # noqa: S104
        assert entry["uptime"] == "01:15:30"

    def test_empty_neighbors(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        mock_driver.get_ospf_neighbors.return_value = []
        result = net_get_ospf_neighbors("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        mock_driver.get_ospf_neighbors.side_effect = NotSupportedError("not available")
        result = net_get_ospf_neighbors("spine-01")
        assert result["status"] == "not_supported"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_ospf_neighbors("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_ospf_neighbors("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_ospf_neighbors("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_vendor(self, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_neighbors

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_ospf_neighbors("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"


# ===========================================================================
# net_get_ospf_interfaces
# ===========================================================================


class TestNetGetOspfInterfaces:
    """Test the net_get_ospf_interfaces common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        result = net_get_ospf_interfaces("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 4

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        result = net_get_ospf_interfaces("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_ospf_interfaces(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        net_get_ospf_interfaces("spine-01")
        mock_driver.get_ospf_interfaces.assert_called_once_with(vrf="default")

    def test_vrf_parameter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        net_get_ospf_interfaces("spine-01", vrf="VRF-A")
        mock_driver.get_ospf_interfaces.assert_called_once_with(vrf="VRF-A")

    def test_interface_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        result = net_get_ospf_interfaces("spine-01")
        entry = result["data"][0]
        assert entry["interface"] == "Ethernet1"
        assert entry["area"] == "0.0.0.0"  # noqa: S104
        assert entry["network_type"] == "point-to-point"
        assert entry["cost"] == 10
        assert entry["neighbor_count"] == 1

    def test_loopback_interface(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        result = net_get_ospf_interfaces("spine-01")
        loopback = result["data"][2]
        assert loopback["interface"] == "Loopback0"
        assert loopback["network_type"] == "loopback"
        assert loopback["neighbor_count"] == 0

    def test_empty_interfaces(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        mock_driver.get_ospf_interfaces.return_value = []
        result = net_get_ospf_interfaces("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        mock_driver.get_ospf_interfaces.side_effect = NotSupportedError("not available")
        result = net_get_ospf_interfaces("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_ospf_interfaces("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_ospf_interfaces("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_ospf_interfaces("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.routing import net_get_ospf_interfaces

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_ospf_interfaces("junos-rtr")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"


# ===========================================================================
# net_get_route_table
# ===========================================================================


class TestNetGetRouteTable:
    """Test the net_get_route_table common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 5

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data", "pagination"}

    def test_calls_get_route_table(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        net_get_route_table("spine-01")
        mock_driver.get_route_table.assert_called_once_with(protocol="", vrf="default", limit=10000)

    def test_protocol_filter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        net_get_route_table("spine-01", protocol="bgp")
        mock_driver.get_route_table.assert_called_once_with(protocol="bgp", vrf="default", limit=10000)

    def test_vrf_parameter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        net_get_route_table("spine-01", vrf="MGMT")
        mock_driver.get_route_table.assert_called_once_with(protocol="", vrf="MGMT", limit=10000)

    def test_route_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01")
        entry = result["data"][0]
        assert entry["prefix"] == "10.0.0.0/24"
        assert entry["next_hop"] == "10.0.0.1"
        assert entry["protocol"] == "connected"
        assert entry["metric"] == 0
        assert entry["preference"] == 0

    def test_default_pagination(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01")
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["returned"] == 5
        assert result["pagination"]["has_more"] is False

    def test_pagination_page_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01", page=1, page_size=2)
        assert len(result["data"]) == 2
        assert result["data"][0]["prefix"] == "10.0.0.0/24"
        assert result["data"][1]["prefix"] == "10.1.0.0/24"
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["has_more"] is True

    def test_pagination_page_2(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01", page=2, page_size=2)
        assert len(result["data"]) == 2
        assert result["data"][0]["prefix"] == "10.2.0.0/24"
        assert result["data"][1]["prefix"] == "10.3.0.0/24"

    def test_pagination_last_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01", page=3, page_size=2)
        assert len(result["data"]) == 1
        assert result["data"][0]["prefix"] == "0.0.0.0/0"
        assert result["pagination"]["has_more"] is False

    def test_pagination_empty_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01", page=100, page_size=2)
        assert len(result["data"]) == 0
        assert result["pagination"]["returned"] == 0
        assert result["pagination"]["has_more"] is False

    def test_page_less_than_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01", page=0)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_negative(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01", page=-1)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_size_zero(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01", page_size=0)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_page_size_too_large(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_table

        result = net_get_route_table("spine-01", page_size=501)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_large_table_pagination(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        large_routes = _generate_large_route_table(1200)
        mock_driver.get_route_table.return_value = large_routes
        result = net_get_route_table("spine-01", page=1, page_size=100)
        assert len(result["data"]) == 100
        assert result["pagination"]["total"] == 1200
        assert result["pagination"]["has_more"] is True

    def test_large_table_last_page(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        large_routes = _generate_large_route_table(1200)
        mock_driver.get_route_table.return_value = large_routes
        result = net_get_route_table("spine-01", page=12, page_size=100)
        assert len(result["data"]) == 100
        assert result["pagination"]["has_more"] is False

    def test_large_table_page_13_empty(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        large_routes = _generate_large_route_table(1200)
        mock_driver.get_route_table.return_value = large_routes
        result = net_get_route_table("spine-01", page=13, page_size=100)
        assert len(result["data"]) == 0
        assert result["pagination"]["has_more"] is False

    def test_empty_route_table(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        mock_driver.get_route_table.return_value = []
        result = net_get_route_table("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []
        assert result["pagination"]["total"] == 0

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        mock_driver.get_route_table.side_effect = NotSupportedError("not available")
        result = net_get_route_table("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.routing import net_get_route_table

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_route_table("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.routing import net_get_route_table

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_route_table("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.routing import net_get_route_table

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_route_table("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_page_validation_skips_driver_call(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        net_get_route_table("spine-01", page=0)
        mock_driver.get_route_table.assert_not_called()

    def test_page_size_validation_skips_driver_call(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_table

        net_get_route_table("spine-01", page_size=501)
        mock_driver.get_route_table.assert_not_called()


# ===========================================================================
# net_get_route_summary
# ===========================================================================


class TestNetGetRouteSummary:
    """Test the net_get_route_summary common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_summary

        result = net_get_route_summary("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_summary

        result = net_get_route_summary("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_route_summary(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_summary

        net_get_route_summary("spine-01")
        mock_driver.get_route_summary.assert_called_once()

    def test_summary_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_route_summary

        result = net_get_route_summary("spine-01")
        data = result["data"]
        assert data["total"] == 150
        assert data["connected"] == 10
        assert data["static"] == 5
        assert data["bgp"] == 100
        assert data["ospf"] == 30
        assert data["isis"] == 5

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_summary

        mock_driver.get_route_summary.side_effect = NotSupportedError("not available")
        result = net_get_route_summary("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.routing import net_get_route_summary

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_route_summary("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.routing import net_get_route_summary

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_route_summary("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.routing import net_get_route_summary

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_route_summary("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.routing import net_get_route_summary

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_route_summary("iosxe-rtr")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"


# ===========================================================================
# net_get_arp_table
# ===========================================================================


class TestNetGetArpTable:
    """Test the net_get_arp_table common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 5

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data", "pagination"}

    def test_calls_get_arp_table(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_arp_table

        net_get_arp_table("spine-01")
        mock_driver.get_arp_table.assert_called_once()

    def test_arp_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01")
        entry = result["data"][0]
        assert entry["ip"] == "10.0.0.1"
        assert entry["mac"] == "00:1a:2b:3c:4d:5e"
        assert entry["interface"] == "Ethernet1"
        assert entry["age"] == 120.5

    def test_negative_age(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01")
        entry = result["data"][2]
        assert entry["age"] == -1

    def test_default_pagination(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01")
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["returned"] == 5
        assert result["pagination"]["has_more"] is False

    def test_pagination_page_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01", page=1, page_size=2)
        assert len(result["data"]) == 2
        assert result["data"][0]["ip"] == "10.0.0.1"
        assert result["data"][1]["ip"] == "10.0.0.2"
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["has_more"] is True

    def test_pagination_page_2(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01", page=2, page_size=2)
        assert len(result["data"]) == 2
        assert result["data"][0]["ip"] == "10.0.0.3"
        assert result["data"][1]["ip"] == "192.168.1.1"

    def test_pagination_last_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01", page=3, page_size=2)
        assert len(result["data"]) == 1
        assert result["data"][0]["ip"] == "192.168.1.2"
        assert result["pagination"]["has_more"] is False

    def test_pagination_empty_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01", page=100, page_size=2)
        assert len(result["data"]) == 0
        assert result["pagination"]["returned"] == 0
        assert result["pagination"]["has_more"] is False

    def test_page_less_than_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01", page=0)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_negative(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01", page=-1)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_size_zero(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01", page_size=0)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_page_size_too_large(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_arp_table

        result = net_get_arp_table("spine-01", page_size=501)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_large_arp_table_pagination(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_arp_table

        large_arp = _generate_large_arp_table(1500)
        mock_driver.get_arp_table.return_value = large_arp
        result = net_get_arp_table("spine-01", page=1, page_size=100)
        assert len(result["data"]) == 100
        assert result["pagination"]["total"] == 1500
        assert result["pagination"]["has_more"] is True

    def test_large_arp_table_middle_page(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_arp_table

        large_arp = _generate_large_arp_table(1500)
        mock_driver.get_arp_table.return_value = large_arp
        result = net_get_arp_table("spine-01", page=8, page_size=100)
        assert len(result["data"]) == 100
        assert result["pagination"]["has_more"] is True

    def test_empty_arp_table(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_arp_table

        mock_driver.get_arp_table.return_value = []
        result = net_get_arp_table("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []
        assert result["pagination"]["total"] == 0

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_arp_table

        mock_driver.get_arp_table.side_effect = NotSupportedError("not available")
        result = net_get_arp_table("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.routing import net_get_arp_table

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_arp_table("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.routing import net_get_arp_table

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_arp_table("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.routing import net_get_arp_table

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_arp_table("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_page_validation_skips_driver_call(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_arp_table

        net_get_arp_table("spine-01", page=0)
        mock_driver.get_arp_table.assert_not_called()

    def test_page_size_validation_skips_driver_call(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_arp_table

        net_get_arp_table("spine-01", page_size=501)
        mock_driver.get_arp_table.assert_not_called()


# ===========================================================================
# net_get_bgp_neighbors
# ===========================================================================


class TestNetGetBgpNeighbors:
    """Test the net_get_bgp_neighbors common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        result = net_get_bgp_neighbors("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 3

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        result = net_get_bgp_neighbors("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_bgp_neighbors(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        net_get_bgp_neighbors("spine-01")
        mock_driver.get_bgp_neighbors.assert_called_once_with(vrf="default")

    def test_vrf_parameter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        net_get_bgp_neighbors("spine-01", vrf="PROD")
        mock_driver.get_bgp_neighbors.assert_called_once_with(vrf="PROD")

    def test_neighbor_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        result = net_get_bgp_neighbors("spine-01")
        entry = result["data"][0]
        assert entry["neighbor"] == "10.0.0.1"
        assert entry["remote_as"] == 65001
        assert entry["state"] == "Established"
        assert entry["prefixes_received"] == 1500
        assert entry["prefixes_sent"] == 200
        assert entry["uptime"] == "3d12h"
        assert entry["description"] == "spine-01"

    def test_inactive_neighbor(self, mock_common_conn_mgr):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        result = net_get_bgp_neighbors("spine-01")
        entry = result["data"][2]
        assert entry["state"] == "Active"
        assert entry["prefixes_received"] == 0
        assert entry["prefixes_sent"] == 0
        assert entry["uptime"] == ""
        assert entry["description"] == ""

    def test_empty_neighbors(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        mock_driver.get_bgp_neighbors.return_value = []
        result = net_get_bgp_neighbors("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        mock_driver.get_bgp_neighbors.side_effect = NotSupportedError("not available")
        result = net_get_bgp_neighbors("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_bgp_neighbors("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_bgp_neighbors("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_bgp_neighbors("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_bgp_neighbors("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.routing import net_get_bgp_neighbors

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_bgp_neighbors("junos-rtr")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"
