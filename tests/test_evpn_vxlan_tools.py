"""Tests for EVPN/VXLAN data center fabric tools."""

import pyeapi.eapilib

from network_mcp.tools.evpn_vxlan import (
    eos_get_bgp_evpn_instance,
    eos_get_bgp_evpn_summary,
    eos_get_evpn_route_type_imet,
    eos_get_evpn_route_type_macip,
    eos_get_vxlan_address_table,
    eos_get_vxlan_config_sanity,
    eos_get_vxlan_interface,
    eos_get_vxlan_vni,
    eos_get_vxlan_vtep,
)
from tests.conftest import (
    MOCK_BGP_EVPN_INSTANCE,
    MOCK_BGP_EVPN_SUMMARY,
    MOCK_EVPN_ROUTE_TYPE_IMET,
    MOCK_EVPN_ROUTE_TYPE_MACIP,
    MOCK_VXLAN_ADDRESS_TABLE,
    MOCK_VXLAN_CONFIG_SANITY,
    MOCK_VXLAN_INTERFACE,
    MOCK_VXLAN_VNI,
    MOCK_VXLAN_VTEP,
)

# --- get_vxlan_interface ---


def test_get_vxlan_interface_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_VXLAN_INTERFACE]

    result = eos_get_vxlan_interface("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"]["srcIpIntf"] == "Loopback1"
    assert result["data"]["srcIpAddr"] == "10.111.253.3"
    assert result["data"]["udpPort"] == 4789
    assert "112" in result["data"]["vlanToVniMap"]
    assert "TENANT" in result["data"]["vrfToVniMap"]
    mock_node.run_commands.assert_called_once_with(["show interfaces vxlan1"], encoding="json")


def test_get_vxlan_interface_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_vxlan_interface("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_vxlan_vtep ---


def test_get_vxlan_vtep_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_VXLAN_VTEP]

    result = eos_get_vxlan_vtep("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "10.111.253.1" in result["data"]["vteps"]
    assert "10.111.253.4" in result["data"]["vteps"]
    assert len(result["data"]["vteps"]) == 2
    mock_node.run_commands.assert_called_once_with(["show vxlan vtep"], encoding="json")


def test_get_vxlan_vtep_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_vxlan_vtep("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_vxlan_vni ---


def test_get_vxlan_vni_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_VXLAN_VNI]

    result = eos_get_vxlan_vni("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "112" in result["data"]["vniBindings"]
    assert result["data"]["vniBindings"]["112"]["vlan"] == 112
    assert "5001" in result["data"]["vniBindingsToVrf"]
    assert result["data"]["vniBindingsToVrf"]["5001"]["vrfName"] == "TENANT"
    mock_node.run_commands.assert_called_once_with(["show vxlan vni"], encoding="json")


def test_get_vxlan_vni_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_vxlan_vni("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_vxlan_address_table ---


def test_get_vxlan_address_table_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_VXLAN_ADDRESS_TABLE]

    result = eos_get_vxlan_address_table("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "112" in result["data"]["addresses"]
    assert result["data"]["addresses"]["112"][0]["macAddress"] == "00:1c:73:c0:c6:17"
    assert result["data"]["addresses"]["112"][0]["vtepAddr"] == "10.111.253.1"
    mock_node.run_commands.assert_called_once_with(["show vxlan address-table"], encoding="json")


def test_get_vxlan_address_table_with_vlan(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_VXLAN_ADDRESS_TABLE]

    result = eos_get_vxlan_address_table("test-switch", vlan=112)

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show vxlan address-table vlan 112"], encoding="json")


def test_get_vxlan_address_table_invalid_vlan(mock_conn_mgr, mock_node):
    result = eos_get_vxlan_address_table("test-switch", vlan=9999)

    assert result["status"] == "error"
    assert "VLAN ID" in result["error"]


def test_get_vxlan_address_table_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_vxlan_address_table("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_vxlan_config_sanity ---


def test_get_vxlan_config_sanity_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_VXLAN_CONFIG_SANITY]

    result = eos_get_vxlan_config_sanity("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    categories = result["data"]["categories"]
    assert categories["localVtep"]["allCheckPass"] is True
    assert categories["mlag"]["allCheckPass"] is True
    assert categories["pd"]["allCheckPass"] is True
    mock_node.run_commands.assert_called_once_with(["show vxlan config-sanity detail"], encoding="json")


def test_get_vxlan_config_sanity_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_vxlan_config_sanity("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_bgp_evpn_summary ---


def test_get_bgp_evpn_summary_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BGP_EVPN_SUMMARY]

    result = eos_get_bgp_evpn_summary("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    peers = result["data"]["default"]["peers"]
    assert "10.111.0.1" in peers
    assert peers["10.111.0.1"]["peerState"] == "Established"
    assert peers["10.111.0.1"]["prefixReceived"] == 42
    assert "10.111.0.2" in peers
    mock_node.run_commands.assert_called_once_with(["show bgp evpn summary"], encoding="json")


def test_get_bgp_evpn_summary_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_bgp_evpn_summary("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_bgp_evpn_instance ---


def test_get_bgp_evpn_instance_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_BGP_EVPN_INSTANCE]

    result = eos_get_bgp_evpn_instance("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    instance = result["data"]["evpnInstances"]["VLAN 112"]
    assert instance["rd"] == "10.111.254.3:112"
    assert "112:112" in instance["importRts"]
    assert "112:112" in instance["exportRts"]
    mock_node.run_commands.assert_called_once_with(["show bgp evpn instance"], encoding="json")


def test_get_bgp_evpn_instance_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_bgp_evpn_instance("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_evpn_route_type_macip ---


def test_get_evpn_route_type_macip_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVPN_ROUTE_TYPE_MACIP]

    result = eos_get_evpn_route_type_macip("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    routes = result["data"]["evpnRoutes"]
    assert len(routes) == 1
    route_key = list(routes.keys())[0]
    assert "mac-ip" in route_key
    assert routes[route_key]["evpnRoutePaths"][0]["routeType"]["active"] is True
    mock_node.run_commands.assert_called_once_with(["show bgp evpn route-type mac-ip"], encoding="json")


def test_get_evpn_route_type_macip_with_vni(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVPN_ROUTE_TYPE_MACIP]

    result = eos_get_evpn_route_type_macip("test-switch", vni=112)

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show bgp evpn route-type mac-ip vni 112"], encoding="json")


def test_get_evpn_route_type_macip_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_evpn_route_type_macip("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_evpn_route_type_imet ---


def test_get_evpn_route_type_imet_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVPN_ROUTE_TYPE_IMET]

    result = eos_get_evpn_route_type_imet("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    routes = result["data"]["evpnRoutes"]
    assert len(routes) == 1
    route_key = list(routes.keys())[0]
    assert "imet" in route_key
    assert routes[route_key]["evpnRoutePaths"][0]["routeType"]["valid"] is True
    mock_node.run_commands.assert_called_once_with(["show bgp evpn route-type imet"], encoding="json")


def test_get_evpn_route_type_imet_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_evpn_route_type_imet("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]
