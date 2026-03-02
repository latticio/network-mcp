"""Tests for switching tools (MLAG, STP, MAC table, LLDP, port-channels, trunks)."""

import pyeapi.eapilib

from network_mcp.tools.switching import (
    eos_get_lldp_neighbor_detail,
    eos_get_lldp_neighbors,
    eos_get_mac_address_table,
    eos_get_mlag_config_sanity,
    eos_get_mlag_interfaces,
    eos_get_mlag_status,
    eos_get_port_channels,
    eos_get_spanning_tree,
    eos_get_trunk_interfaces,
)
from tests.conftest import (
    MOCK_LLDP_NEIGHBOR_DETAIL,
    MOCK_LLDP_NEIGHBORS,
    MOCK_MAC_ADDRESS_TABLE,
    MOCK_MLAG,
    MOCK_MLAG_CONFIG_SANITY,
    MOCK_MLAG_DETAIL,
    MOCK_MLAG_INTERFACES,
    MOCK_PORT_CHANNELS,
    MOCK_SPANNING_TREE,
    MOCK_TRUNK_INTERFACES,
)

# --- Existing tool tests (basic success) ---


def test_eos_get_mlag_status(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_MLAG, MOCK_MLAG_DETAIL]

    result = eos_get_mlag_status("test-switch")

    assert result["status"] == "success"
    assert result["data"]["domain_id"] == "mlag-domain-01"
    assert result["data"]["state"] == "active"
    mock_node.run_commands.assert_called_once_with(["show mlag", "show mlag detail"], encoding="json")


def test_eos_get_spanning_tree(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_SPANNING_TREE]

    result = eos_get_spanning_tree("test-switch")

    assert result["status"] == "success"
    assert result["data"] == MOCK_SPANNING_TREE


def test_eos_get_mac_address_table(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_MAC_ADDRESS_TABLE]

    result = eos_get_mac_address_table("test-switch")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show mac address-table"], encoding="json")


def test_get_mac_address_table_vlan_filter(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_MAC_ADDRESS_TABLE]

    result = eos_get_mac_address_table("test-switch", vlan=100)

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show mac address-table vlan 100"], encoding="json")


def test_eos_get_lldp_neighbors(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_LLDP_NEIGHBORS]

    result = eos_get_lldp_neighbors("test-switch")

    assert result["status"] == "success"


def test_eos_get_lldp_neighbor_detail(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_LLDP_NEIGHBOR_DETAIL]

    result = eos_get_lldp_neighbor_detail("test-switch", "Ethernet1")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show lldp neighbors Ethernet1 detail"], encoding="json")


def test_eos_get_port_channels(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_PORT_CHANNELS]

    result = eos_get_port_channels("test-switch")

    assert result["status"] == "success"


# --- New tool tests ---


def test_eos_get_mlag_config_sanity(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_MLAG_CONFIG_SANITY]

    result = eos_get_mlag_config_sanity("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"]["mlagActive"] is True
    assert result["data"]["response"] == "consistent"
    mock_node.run_commands.assert_called_once_with(["show mlag config-sanity"], encoding="json")


def test_get_mlag_config_sanity_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_mlag_config_sanity("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_eos_get_mlag_interfaces(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_MLAG_INTERFACES]

    result = eos_get_mlag_interfaces("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "Port-Channel10" in result["data"]["interfaces"]
    assert result["data"]["interfaces"]["Port-Channel10"]["localLinkStatus"] == "up"
    mock_node.run_commands.assert_called_once_with(["show mlag interfaces"], encoding="json")


def test_get_mlag_interfaces_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_mlag_interfaces("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_eos_get_trunk_interfaces(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_TRUNK_INTERFACES]

    result = eos_get_trunk_interfaces("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "Ethernet1" in result["data"]["trunks"]
    assert result["data"]["trunks"]["Ethernet1"]["nativeVlan"] == 1
    mock_node.run_commands.assert_called_once_with(["show interfaces trunk"], encoding="json")


def test_get_trunk_interfaces_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_trunk_interfaces("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- Field filtering tests ---


def test_get_lldp_neighbors_fields_filter(mock_conn_mgr, mock_node):
    """Field filtering returns only requested fields per LLDP neighbor entry."""
    mock_data = {
        "lldpNeighbors": [
            {"neighborDevice": "spine-01", "neighborPort": "Ethernet1", "port": "Ethernet1", "ttl": 120},
            {"neighborDevice": "spine-02", "neighborPort": "Ethernet2", "port": "Ethernet2", "ttl": 120},
        ]
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_lldp_neighbors("test-switch", fields=["neighborDevice", "neighborPort"])

    assert result["status"] == "success"
    neighbors = result["data"]["lldpNeighbors"]
    assert len(neighbors) == 2
    assert set(neighbors[0].keys()) == {"neighborDevice", "neighborPort"}
    assert set(neighbors[1].keys()) == {"neighborDevice", "neighborPort"}


def test_get_lldp_neighbors_fields_none(mock_conn_mgr, mock_node):
    """fields=None returns all fields unchanged."""
    mock_data = {
        "lldpNeighbors": [
            {"neighborDevice": "spine-01", "neighborPort": "Ethernet1", "port": "Ethernet1", "ttl": 120},
        ]
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_lldp_neighbors("test-switch", fields=None)

    assert result["status"] == "success"
    neighbors = result["data"]["lldpNeighbors"]
    assert len(neighbors[0].keys()) == 4


def test_get_lldp_neighbors_fields_nonexistent(mock_conn_mgr, mock_node):
    """Non-existent field names return empty dicts per entry."""
    mock_data = {
        "lldpNeighbors": [
            {"neighborDevice": "spine-01", "neighborPort": "Ethernet1", "port": "Ethernet1", "ttl": 120},
        ]
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_lldp_neighbors("test-switch", fields=["nonexistent"])

    assert result["status"] == "success"
    neighbors = result["data"]["lldpNeighbors"]
    assert neighbors[0] == {}


# --- Pagination tests ---


def test_get_lldp_neighbors_pagination(mock_conn_mgr, mock_node):
    """Pagination returns correct subset of LLDP neighbors."""
    mock_data = {
        "lldpNeighbors": [
            {"neighborDevice": f"switch-{i:02d}", "neighborPort": f"Ethernet{i}", "port": f"Ethernet{i}", "ttl": 120}
            for i in range(1, 11)
        ]
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_lldp_neighbors("test-switch", limit=3, offset=0)

    assert result["status"] == "success"
    assert len(result["data"]["lldpNeighbors"]) == 3
    assert "pagination" in result
    assert result["pagination"]["total"] == 10
    assert result["pagination"]["returned"] == 3
    assert result["pagination"]["has_more"] is True


def test_get_lldp_neighbors_pagination_last_page(mock_conn_mgr, mock_node):
    """Pagination at end returns remaining items with has_more=False."""
    mock_data = {
        "lldpNeighbors": [{"neighborDevice": f"switch-{i:02d}", "neighborPort": f"Ethernet{i}"} for i in range(1, 6)]
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_lldp_neighbors("test-switch", limit=3, offset=3)

    assert result["status"] == "success"
    assert len(result["data"]["lldpNeighbors"]) == 2
    assert result["pagination"]["has_more"] is False
    assert result["pagination"]["returned"] == 2


def test_get_lldp_neighbors_fields_and_pagination(mock_conn_mgr, mock_node):
    """Both fields and pagination work together on LLDP neighbors."""
    mock_data = {
        "lldpNeighbors": [
            {"neighborDevice": f"switch-{i:02d}", "neighborPort": f"Ethernet{i}", "port": f"Ethernet{i}", "ttl": 120}
            for i in range(1, 8)
        ]
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_lldp_neighbors("test-switch", fields=["neighborDevice"], limit=2, offset=0)

    assert result["status"] == "success"
    neighbors = result["data"]["lldpNeighbors"]
    assert len(neighbors) == 2
    assert set(neighbors[0].keys()) == {"neighborDevice"}
    assert result["pagination"]["total"] == 7
    assert result["pagination"]["returned"] == 2


def test_get_lldp_neighbors_no_pagination(mock_conn_mgr, mock_node):
    """Without limit/offset, no pagination metadata is included."""
    mock_node.run_commands.return_value = [MOCK_LLDP_NEIGHBORS]

    result = eos_get_lldp_neighbors("test-switch")

    assert result["status"] == "success"
    assert "pagination" not in result


def test_get_mac_address_table_pagination_uses_paginate_list(mock_conn_mgr, mock_node):
    """MAC address table pagination works correctly (uses paginate_list internally)."""
    entries = [{"macAddress": f"00:00:00:00:00:{i:02x}", "interface": f"Ethernet{i}"} for i in range(1, 11)]
    mock_data = {"unicastTable": {"tableEntries": entries}}
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_mac_address_table("test-switch", limit=4, offset=0)

    assert result["status"] == "success"
    assert len(result["data"]["unicastTable"]["tableEntries"]) == 4
    assert result["pagination"]["total"] == 10
    assert result["pagination"]["returned"] == 4
    assert result["pagination"]["has_more"] is True
