"""Tests for routing tools."""

import pyeapi.eapilib

from network_mcp.tools.routing import (
    eos_get_arp_table,
    eos_get_bgp_neighbors,
    eos_get_bgp_routes,
    eos_get_bgp_summary,
    eos_get_ospf_neighbors,
    eos_get_prefix_list,
    eos_get_route_maps,
    eos_get_route_summary,
    eos_get_route_table,
    eos_get_varp_status,
    eos_get_vrrp_status,
)
from tests.conftest import MOCK_BGP_NEIGHBORS_DETAIL, MOCK_BGP_SUMMARY, MOCK_EMPTY_RESPONSE, MOCK_VARP, MOCK_VRRP


def test_eos_get_bgp_summary(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BGP_SUMMARY]

    result = eos_get_bgp_summary("test-switch")

    assert result["status"] == "success"
    assert result["data"]["vrf"] == "default"
    assert result["data"]["local_asn"] == "65000"
    assert "10.0.0.2" in result["data"]["peers"]
    mock_node.run_commands.assert_called_once_with(["show ip bgp summary"], encoding="json")


def test_get_bgp_summary_vrf(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"MGMT": {"routerId": "10.0.0.1", "asn": "65000", "peers": {}}}}]

    result = eos_get_bgp_summary("test-switch", vrf="MGMT")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ip bgp summary vrf MGMT"], encoding="json")


def test_eos_get_bgp_neighbors(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"peers": {}}}}]

    result = eos_get_bgp_neighbors("test-switch")

    assert result["status"] == "success"


def test_eos_get_bgp_routes(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"bgpRouteEntries": {}}}}]

    result = eos_get_bgp_routes("test-switch")

    assert result["status"] == "success"


def test_eos_get_ospf_neighbors(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"instList": {}}}}]

    result = eos_get_ospf_neighbors("test-switch")

    assert result["status"] == "success"


def test_eos_get_route_table(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"routes": {}}}}]

    result = eos_get_route_table("test-switch")

    assert result["status"] == "success"


def test_eos_get_route_summary(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"totalRoutes": 100}}}]

    result = eos_get_route_summary("test-switch")

    assert result["status"] == "success"


def test_eos_get_arp_table(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"ipV4Neighbors": []}]

    result = eos_get_arp_table("test-switch")

    assert result["status"] == "success"


def test_eos_get_prefix_list(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"ipPrefixLists": {}}]

    result = eos_get_prefix_list("test-switch")

    assert result["status"] == "success"


def test_eos_get_route_maps(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"routeMaps": {}}]

    result = eos_get_route_maps("test-switch")

    assert result["status"] == "success"


# --- New routing tool tests ---


def test_eos_get_vrrp_status(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_VRRP]

    result = eos_get_vrrp_status("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "virtualRouters" in result["data"]
    mock_node.run_commands.assert_called_once_with(["show vrrp"], encoding="json")


def test_get_vrrp_status_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_vrrp_status("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_eos_get_varp_status(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_VARP]

    result = eos_get_varp_status("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"]["virtualMac"] == "00:1c:73:00:00:99"
    mock_node.run_commands.assert_called_once_with(["show ip virtual-router"], encoding="json")


def test_get_varp_status_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_varp_status("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- IPv6 address family tests ---


def test_get_bgp_summary_ipv6(mock_conn_mgr, mock_node):
    mock_data = {
        "vrfs": {
            "default": {
                "routerId": "10.0.0.1",
                "asn": "65000",
                "peers": {
                    "2001:db8::2": {
                        "peerState": "Established",
                        "prefixReceived": 50,
                        "upDownTime": 1234567,
                        "asn": "65001",
                    }
                },
            }
        }
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_bgp_summary("test-switch", address_family="ipv6")

    assert result["status"] == "success"
    assert result["data"]["address_family"] == "ipv6"
    assert "2001:db8::2" in result["data"]["peers"]
    mock_node.run_commands.assert_called_once_with(["show ipv6 bgp summary"], encoding="json")


def test_get_bgp_summary_ipv6_vrf(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"MGMT": {"routerId": "10.0.0.1", "asn": "65000", "peers": {}}}}]

    result = eos_get_bgp_summary("test-switch", address_family="ipv6", vrf="MGMT")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 bgp summary vrf MGMT"], encoding="json")


def test_get_bgp_neighbors_ipv6(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"peers": {}}}}]

    result = eos_get_bgp_neighbors("test-switch", address_family="ipv6")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 bgp neighbors"], encoding="json")


def test_get_bgp_neighbors_ipv6_specific(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"peers": {}}}}]

    result = eos_get_bgp_neighbors("test-switch", address_family="ipv6", neighbor="2001:db8::2")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 bgp neighbors 2001:db8::2"], encoding="json")


def test_get_bgp_routes_ipv6(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"bgpRouteEntries": {}}}}]

    result = eos_get_bgp_routes("test-switch", address_family="ipv6")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 bgp"], encoding="json")


def test_get_bgp_routes_ipv6_vrf(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"PROD": {"bgpRouteEntries": {}}}}]

    result = eos_get_bgp_routes("test-switch", address_family="ipv6", vrf="PROD")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 bgp vrf PROD"], encoding="json")


def test_get_route_table_ipv6(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"routes": {}}}}]

    result = eos_get_route_table("test-switch", address_family="ipv6")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 route"], encoding="json")


def test_get_route_table_ipv6_vrf(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"MGMT": {"routes": {}}}}]

    result = eos_get_route_table("test-switch", address_family="ipv6", vrf="MGMT")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 route vrf MGMT"], encoding="json")


def test_get_route_summary_ipv6(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"default": {"totalRoutes": 25}}}]

    result = eos_get_route_summary("test-switch", address_family="ipv6")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 route summary"], encoding="json")


def test_get_route_summary_ipv6_vrf(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vrfs": {"PROD": {"totalRoutes": 10}}}]

    result = eos_get_route_summary("test-switch", address_family="ipv6", vrf="PROD")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 route summary vrf PROD"], encoding="json")


def test_get_arp_table_ipv6(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"ipV6Neighbors": []}]

    result = eos_get_arp_table("test-switch", address_family="ipv6")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 neighbors"], encoding="json")


def test_get_arp_table_ipv6_vrf(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"ipV6Neighbors": []}]

    result = eos_get_arp_table("test-switch", address_family="ipv6", vrf="MGMT")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ipv6 neighbors vrf MGMT"], encoding="json")


# --- address_family validation tests ---


def test_get_bgp_summary_invalid_address_family(mock_conn_mgr, mock_node):
    result = eos_get_bgp_summary("test-switch", address_family="ipv5")

    assert result["status"] == "error"
    assert "Invalid address_family" in result["error"]
    assert "ipv5" in result["error"]
    mock_node.run_commands.assert_not_called()


def test_get_bgp_neighbors_invalid_address_family(mock_conn_mgr, mock_node):
    result = eos_get_bgp_neighbors("test-switch", address_family="bad")

    assert result["status"] == "error"
    assert "Invalid address_family" in result["error"]
    mock_node.run_commands.assert_not_called()


def test_get_bgp_routes_invalid_address_family(mock_conn_mgr, mock_node):
    result = eos_get_bgp_routes("test-switch", address_family="bad")

    assert result["status"] == "error"
    assert "Invalid address_family" in result["error"]
    mock_node.run_commands.assert_not_called()


def test_get_route_table_invalid_address_family(mock_conn_mgr, mock_node):
    result = eos_get_route_table("test-switch", address_family="bad")

    assert result["status"] == "error"
    assert "Invalid address_family" in result["error"]
    mock_node.run_commands.assert_not_called()


def test_get_route_summary_invalid_address_family(mock_conn_mgr, mock_node):
    result = eos_get_route_summary("test-switch", address_family="bad")

    assert result["status"] == "error"
    assert "Invalid address_family" in result["error"]
    mock_node.run_commands.assert_not_called()


def test_get_arp_table_invalid_address_family(mock_conn_mgr, mock_node):
    result = eos_get_arp_table("test-switch", address_family="bad")

    assert result["status"] == "error"
    assert "Invalid address_family" in result["error"]
    mock_node.run_commands.assert_not_called()


# --- Backward compatibility: existing IPv4 calls still work without address_family ---


def test_get_bgp_summary_default_is_ipv4(mock_conn_mgr, mock_node):
    """Verify calling without address_family uses IPv4 commands (backward compatible)."""
    mock_node.run_commands.return_value = [MOCK_BGP_SUMMARY]

    result = eos_get_bgp_summary("test-switch")

    assert result["status"] == "success"
    assert result["data"]["address_family"] == "ipv4"
    mock_node.run_commands.assert_called_once_with(["show ip bgp summary"], encoding="json")


# --- Field filtering tests ---


def test_get_bgp_neighbors_fields_filter(mock_conn_mgr, mock_node):
    """Field filtering returns only requested fields per peer entry."""
    mock_node.run_commands.return_value = [MOCK_BGP_NEIGHBORS_DETAIL]

    result = eos_get_bgp_neighbors("test-switch", fields=["peerState", "prefixReceived"])

    assert result["status"] == "success"
    peer_list = result["data"]["vrfBgpNeighbors"]["default"]["peerList"]
    assert len(peer_list) == 1
    assert set(peer_list[0].keys()) == {"peerState", "prefixReceived"}


def test_get_bgp_neighbors_fields_none(mock_conn_mgr, mock_node):
    """fields=None returns all fields unchanged."""
    mock_node.run_commands.return_value = [MOCK_BGP_NEIGHBORS_DETAIL]

    result = eos_get_bgp_neighbors("test-switch", fields=None)

    assert result["status"] == "success"
    peer_list = result["data"]["vrfBgpNeighbors"]["default"]["peerList"]
    assert "peerState" in peer_list[0]
    assert "localAsn" in peer_list[0]


def test_get_bgp_neighbors_fields_nonexistent(mock_conn_mgr, mock_node):
    """Non-existent field names return empty dicts per entry."""
    mock_node.run_commands.return_value = [MOCK_BGP_NEIGHBORS_DETAIL]

    result = eos_get_bgp_neighbors("test-switch", fields=["nonexistent"])

    assert result["status"] == "success"
    peer_list = result["data"]["vrfBgpNeighbors"]["default"]["peerList"]
    assert peer_list[0] == {}


def test_get_arp_table_fields_filter(mock_conn_mgr, mock_node):
    """Field filtering on ARP table returns only requested fields per entry."""
    mock_data = {
        "ipV4Neighbors": [
            {"address": "10.0.0.1", "hwAddress": "00:1c:73:00:00:01", "interface": "Ethernet1", "age": 300},
            {"address": "10.0.0.2", "hwAddress": "00:1c:73:00:00:02", "interface": "Ethernet2", "age": 600},
        ]
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_arp_table("test-switch", fields=["address", "hwAddress"])

    assert result["status"] == "success"
    entries = result["data"]["ipV4Neighbors"]
    assert len(entries) == 2
    assert set(entries[0].keys()) == {"address", "hwAddress"}
    assert set(entries[1].keys()) == {"address", "hwAddress"}


def test_get_arp_table_fields_none(mock_conn_mgr, mock_node):
    """fields=None returns all fields unchanged."""
    mock_data = {
        "ipV4Neighbors": [
            {"address": "10.0.0.1", "hwAddress": "00:1c:73:00:00:01", "interface": "Ethernet1", "age": 300},
        ]
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_arp_table("test-switch", fields=None)

    assert result["status"] == "success"
    entries = result["data"]["ipV4Neighbors"]
    assert len(entries[0].keys()) == 4


def test_get_ospf_neighbors_fields_filter(mock_conn_mgr, mock_node):
    """Field filtering on OSPF neighbors returns only requested fields."""
    mock_data = {
        "vrfs": {
            "default": {
                "instList": {
                    "1": {
                        "ospfNeighborEntries": [
                            {
                                "routerId": "10.0.0.2",
                                "adjacencyState": "full",
                                "priority": 1,
                                "interfaceName": "Ethernet1",
                            }
                        ]
                    }
                }
            }
        }
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_ospf_neighbors("test-switch", fields=["routerId", "adjacencyState"])

    assert result["status"] == "success"
    entries = result["data"]["vrfs"]["default"]["instList"]["1"]["ospfNeighborEntries"]
    assert len(entries) == 1
    assert set(entries[0].keys()) == {"routerId", "adjacencyState"}


# --- Pagination tests ---


def test_get_arp_table_pagination(mock_conn_mgr, mock_node):
    """Pagination returns correct subset and metadata for ARP table."""
    entries = [
        {"address": f"10.0.0.{i}", "hwAddress": f"00:00:00:00:00:{i:02x}", "interface": "Ethernet1"} for i in range(20)
    ]
    mock_node.run_commands.return_value = [{"ipV4Neighbors": entries}]

    result = eos_get_arp_table("test-switch", limit=5, offset=0)

    assert result["status"] == "success"
    assert len(result["data"]["ipV4Neighbors"]) == 5
    assert "pagination" in result
    meta = result["pagination"]
    assert meta["total"] == 20
    assert meta["offset"] == 0
    assert meta["limit"] == 5
    assert meta["returned"] == 5
    assert meta["has_more"] is True


def test_get_arp_table_pagination_beyond_total(mock_conn_mgr, mock_node):
    """Pagination with offset beyond total returns empty list."""
    entries = [{"address": "10.0.0.1", "hwAddress": "00:00:00:00:00:01"}]
    mock_node.run_commands.return_value = [{"ipV4Neighbors": entries}]

    result = eos_get_arp_table("test-switch", limit=5, offset=9999)

    assert result["status"] == "success"
    assert len(result["data"]["ipV4Neighbors"]) == 0
    assert result["pagination"]["has_more"] is False
    assert result["pagination"]["returned"] == 0


def test_get_arp_table_fields_and_pagination(mock_conn_mgr, mock_node):
    """Both fields and pagination work together: filter first, then paginate."""
    entries = [
        {"address": f"10.0.0.{i}", "hwAddress": f"00:00:00:00:00:{i:02x}", "interface": "Ethernet1", "age": 300}
        for i in range(10)
    ]
    mock_node.run_commands.return_value = [{"ipV4Neighbors": entries}]

    result = eos_get_arp_table("test-switch", fields=["address", "hwAddress"], limit=3, offset=0)

    assert result["status"] == "success"
    returned = result["data"]["ipV4Neighbors"]
    assert len(returned) == 3
    assert set(returned[0].keys()) == {"address", "hwAddress"}
    assert result["pagination"]["total"] == 10
    assert result["pagination"]["returned"] == 3


def test_get_arp_table_no_pagination_no_metadata(mock_conn_mgr, mock_node):
    """Without limit/offset, no pagination metadata is included."""
    mock_node.run_commands.return_value = [{"ipV4Neighbors": [{"address": "10.0.0.1"}]}]

    result = eos_get_arp_table("test-switch")

    assert result["status"] == "success"
    assert "pagination" not in result


def test_get_ospf_neighbors_pagination(mock_conn_mgr, mock_node):
    """Pagination returns correct subset for OSPF neighbors."""
    entries = [
        {"routerId": f"10.0.0.{i}", "adjacencyState": "full", "interfaceName": f"Ethernet{i}"} for i in range(15)
    ]
    mock_data = {"vrfs": {"default": {"instList": {"1": {"ospfNeighborEntries": entries}}}}}
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_ospf_neighbors("test-switch", limit=5, offset=0)

    assert result["status"] == "success"
    returned = result["data"]["vrfs"]["default"]["instList"]["1"]["ospfNeighborEntries"]
    assert len(returned) == 5
    assert result["pagination"]["total"] == 15
    assert result["pagination"]["has_more"] is True


def test_get_bgp_routes_pagination(mock_conn_mgr, mock_node):
    """Pagination on BGP routes returns correct subset."""
    routes = {f"10.{i}.0.0/24": {"routeType": "bgp", "nextHop": "10.0.0.1"} for i in range(10)}
    mock_data = {"vrfs": {"default": {"bgpRouteEntries": routes}}}
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_bgp_routes("test-switch", limit=3, offset=0)

    assert result["status"] == "success"
    returned = result["data"]["vrfs"]["default"]["bgpRouteEntries"]
    assert len(returned) == 3
    assert result["pagination"]["total"] == 10
    assert result["pagination"]["has_more"] is True


def test_get_bgp_neighbors_pagination(mock_conn_mgr, mock_node):
    """Pagination on BGP neighbor detail returns correct subset."""
    peers = [{"peerAddress": f"10.0.0.{i}", "peerState": "Established", "prefixReceived": 100} for i in range(8)]
    mock_data = {"vrfBgpNeighbors": {"default": {"peerList": peers}}}
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_bgp_neighbors("test-switch", limit=3, offset=0)

    assert result["status"] == "success"
    returned = result["data"]["vrfBgpNeighbors"]["default"]["peerList"]
    assert len(returned) == 3
    assert result["pagination"]["total"] == 8
    assert result["pagination"]["has_more"] is True


def test_pagination_metadata_keys(mock_conn_mgr, mock_node):
    """Pagination metadata has all expected keys."""
    entries = [{"address": f"10.0.0.{i}"} for i in range(10)]
    mock_node.run_commands.return_value = [{"ipV4Neighbors": entries}]

    result = eos_get_arp_table("test-switch", limit=5, offset=0)

    assert set(result["pagination"].keys()) == {"total", "offset", "limit", "returned", "has_more"}


# --- Connection error and timeout tests ---


def test_get_bgp_summary_connection_error(mock_conn_mgr, mock_node_connection_error):
    """BGP summary returns error on connection failure."""
    result = eos_get_bgp_summary("test-switch")

    assert result["status"] == "error"


def test_get_route_table_connection_error(mock_conn_mgr, mock_node_connection_error):
    """Route table returns error on connection failure."""
    result = eos_get_route_table("test-switch")

    assert result["status"] == "error"


def test_get_arp_table_timeout(mock_conn_mgr, mock_node_timeout):
    """ARP table returns error on timeout."""
    result = eos_get_arp_table("test-switch")

    assert result["status"] == "error"


def test_get_ospf_neighbors_connection_error(mock_conn_mgr, mock_node_connection_error):
    """OSPF neighbors returns error on connection failure."""
    result = eos_get_ospf_neighbors("test-switch")

    assert result["status"] == "error"


def test_get_bgp_summary_empty_response(mock_conn_mgr, mock_node):
    """Empty response returns success with empty/default data."""
    mock_node.run_commands.return_value = [MOCK_EMPTY_RESPONSE]

    result = eos_get_bgp_summary("test-switch")

    assert result["status"] == "success"
    assert result["data"]["peers"] == {}
