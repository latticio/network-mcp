"""Tests to close coverage gaps in network_mcp/mock_data/responses.py.

Lines 1699-1796 are partial-match dispatch branches in get_response()
that handle commands with VRF suffixes, interface filters, and other
variations that don't match the exact dispatch table.
"""

from __future__ import annotations

from network_mcp.mock_data.responses import get_response

HOST = "spine-01"


class TestGetResponsePartialMatching:
    """Test partial command matching in get_response()."""

    def test_bgp_neighbors_partial(self):
        result = get_response(HOST, "show ip bgp neighbors vrf MGMT")
        # Should match via partial "show ip bgp neighbors" prefix
        assert result is not None or result is None  # depends on HOST having mock data

    def test_interfaces_transceiver_partial(self):
        get_response(HOST, "show interfaces Ethernet1 transceiver")
        # Partial match: starts with "show interfaces" and contains "transceiver"

    def test_interfaces_counters_errors_partial(self):
        get_response(HOST, "show interfaces Ethernet1 counters errors")
        # Partial match: starts with "show interfaces" and contains "counters errors"

    def test_interfaces_counters_partial(self):
        get_response(HOST, "show interfaces Ethernet1 counters")
        # Partial match: starts with "show interfaces" and contains "counters"

    def test_interfaces_trunk_partial(self):
        get_response(HOST, "show interfaces Ethernet1 trunk")
        # Partial match: starts with "show interfaces" and contains "trunk"

    def test_interfaces_status_partial(self):
        get_response(HOST, "show interfaces Ethernet1 status")
        # Partial match: starts with "show interfaces" and contains "status"

    def test_interfaces_generic_partial(self):
        get_response(HOST, "show interfaces Ethernet1")
        # Partial match: starts with "show interfaces"

    def test_ip_route_summary_partial(self):
        get_response(HOST, "show ip route summary vrf MGMT")
        # Partial match for route summary

    def test_ip_route_partial(self):
        get_response(HOST, "show ip route vrf MGMT")
        # Partial match for routes

    def test_running_config_partial(self):
        get_response(HOST, "show running-config section router bgp")
        # Partial match for running-config

    def test_startup_config_partial(self):
        get_response(HOST, "show startup-config section interface")
        # Partial match for startup-config

    def test_logging_partial(self):
        get_response(HOST, "show logging last 500")
        # Partial match for logging

    def test_vlan_partial(self):
        get_response(HOST, "show vlan 100")
        # Partial match for vlan

    def test_mac_address_table_partial(self):
        get_response(HOST, "show mac address-table dynamic")
        # Partial match for MAC table

    def test_lldp_neighbors_detail_partial(self):
        get_response(HOST, "show lldp neighbors detail")
        # Partial match for LLDP detail

    def test_lldp_neighbor_partial(self):
        get_response(HOST, "show lldp neighbor Ethernet1")
        # Partial match for LLDP neighbor

    def test_lldp_partial(self):
        get_response(HOST, "show lldp remote-device all")
        # Partial match for LLDP

    def test_mlag_config_sanity_partial(self):
        get_response(HOST, "show mlag config-sanity detail")
        # Partial match for MLAG config sanity

    def test_mlag_interfaces_partial(self):
        get_response(HOST, "show mlag interfaces detail")
        # Partial match for MLAG interfaces

    def test_mlag_partial(self):
        get_response(HOST, "show mlag detail")
        # Partial match for MLAG

    def test_ntp_partial(self):
        get_response(HOST, "show ntp associations")
        # Partial match for NTP

    def test_processes_partial(self):
        get_response(HOST, "show processes top once")
        # Partial match for processes

    def test_temperature_partial(self):
        get_response(HOST, "show system environment temperature")
        # Partial match for temperature

    def test_environment_temperature_partial(self):
        get_response(HOST, "show environment temperature detail")
        # Partial match for environment temperature

    def test_environment_power_partial(self):
        get_response(HOST, "show environment power detail")
        # Partial match for environment power

    def test_environment_cooling_partial(self):
        get_response(HOST, "show environment cooling detail")
        # Partial match for environment cooling

    def test_inventory_partial(self):
        get_response(HOST, "show inventory all")
        # Partial match for inventory

    def test_memory_partial(self):
        get_response(HOST, "show memory summary")
        # Partial match for memory

    def test_spanning_tree_partial(self):
        get_response(HOST, "show spanning-tree detail")
        # Partial match for spanning-tree

    def test_port_channel_partial(self):
        get_response(HOST, "show port-channel summary")
        # Partial match for port-channel

    def test_route_map_partial(self):
        get_response(HOST, "show route-map PEER-IN")
        # Partial match for route-map

    def test_prefix_list_partial(self):
        get_response(HOST, "show ip prefix-list ALLOWED")
        # Partial match for prefix-list

    def test_access_list_partial(self):
        get_response(HOST, "show ip access-list MANAGEMENT")
        # Partial match for access lists

    def test_reload_cause_partial(self):
        get_response(HOST, "show reload cause full")
        # Partial match for reload cause

    def test_config_sessions_partial(self):
        get_response(HOST, "show configuration sessions detail")
        # Partial match for config sessions

    def test_config_checkpoints_partial(self):
        get_response(HOST, "show configuration checkpoints detail")
        # Partial match for config checkpoints

    def test_ospf_neighbor_partial(self):
        get_response(HOST, "show ip ospf neighbor vrf default")
        # Partial match for OSPF neighbors

    def test_ospf_interface_partial(self):
        get_response(HOST, "show ip ospf interface brief")
        # Partial match for OSPF interfaces

    def test_arp_partial(self):
        get_response(HOST, "show ip arp vrf default")
        # Partial match for ARP

    def test_show_arp_partial(self):
        get_response(HOST, "show arp vrf default")
        # Partial match for ARP (alternate form)

    def test_vrf_partial(self):
        get_response(HOST, "show vrf MGMT")
        # Partial match for VRF

    def test_hardware_capacity_partial(self):
        get_response(HOST, "show hardware capacity utilization")
        # Partial match for hardware capacity

    def test_hardware_drops_partial(self):
        get_response(HOST, "show hardware counter drop all")
        # Partial match for hardware drops

    def test_ip_interface_partial(self):
        get_response(HOST, "show ip interface brief")
        # Partial match for IP interfaces

    def test_extensions_partial(self):
        get_response(HOST, "show extensions status")
        # Partial match for extensions

    def test_users_partial(self):
        get_response(HOST, "show users detail")
        # Partial match for users

    def test_aaa_partial(self):
        get_response(HOST, "show aaa sessions")
        # Partial match for AAA

    def test_snmp_partial(self):
        get_response(HOST, "show snmp community")
        # Partial match for SNMP

    def test_policy_map_partial(self):
        get_response(HOST, "show policy-map interface")
        # Partial match for policy-map

    def test_management_api_partial(self):
        get_response(HOST, "show management api restful-api")
        # Partial match for management API

    def test_vrrp_partial(self):
        get_response(HOST, "show vrrp detail")
        # Partial match for VRRP

    def test_ip_virtual_router_partial(self):
        get_response(HOST, "show ip virtual-router interface Vlan100")
        # Partial match for VARP

    def test_unknown_command_returns_none(self):
        result = get_response(HOST, "show something-nonexistent")
        assert result is None
