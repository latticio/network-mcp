"""Tests for routing policy write tools (prefix-list, route-map, static route, OSPF, BGP timers, BGP auth)."""

from unittest.mock import patch

import pytest

from network_mcp.server import settings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


# --- eos_create_prefix_list ---


class TestCreatePrefixList:
    def test_success_single_entry(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        mock_node.config.return_value = [{}]
        result = eos_create_prefix_list(
            "test-switch",
            "ALLOW-DEFAULT",
            [{"seq": 10, "action": "permit", "prefix": "0.0.0.0/0"}],
        )

        assert result["status"] == "success"
        assert result["action"] == "create_prefix_list"
        mock_node.config.assert_called_once_with(
            ["no ip prefix-list ALLOW-DEFAULT", "ip prefix-list ALLOW-DEFAULT seq 10 permit 0.0.0.0/0"]
        )

    def test_success_multiple_entries(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        mock_node.config.return_value = [{}]
        entries = [
            {"seq": 10, "action": "permit", "prefix": "10.0.0.0/8"},
            {"seq": 20, "action": "deny", "prefix": "192.168.0.0/16"},
        ]
        result = eos_create_prefix_list("test-switch", "MY-PL", entries)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "no ip prefix-list MY-PL",
                "ip prefix-list MY-PL seq 10 permit 10.0.0.0/8",
                "ip prefix-list MY-PL seq 20 deny 192.168.0.0/16",
            ]
        )

    def test_success_with_ge_le(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        mock_node.config.return_value = [{}]
        entries = [{"seq": 10, "action": "permit", "prefix": "10.0.0.0/8", "ge": 16, "le": 24}]
        result = eos_create_prefix_list("test-switch", "PL-GE-LE", entries)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            ["no ip prefix-list PL-GE-LE", "ip prefix-list PL-GE-LE seq 10 permit 10.0.0.0/8 ge 16 le 24"]
        )

    def test_idempotent_removes_existing(self, mock_conn_mgr, mock_node):
        """Verify the first command is 'no ip prefix-list ...' for idempotent behavior."""
        from network_mcp.tools.routing import eos_create_prefix_list

        mock_node.config.return_value = [{}]
        result = eos_create_prefix_list(
            "test-switch", "EXISTING-PL", [{"seq": 10, "action": "permit", "prefix": "10.0.0.0/8"}]
        )

        assert result["status"] == "success"
        commands = mock_node.config.call_args[0][0]
        assert commands[0] == "no ip prefix-list EXISTING-PL"

    def test_invalid_action(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        result = eos_create_prefix_list(
            "test-switch", "BAD-PL", [{"seq": 10, "action": "allow", "prefix": "10.0.0.0/8"}]
        )
        assert result["status"] == "error"
        assert "Invalid action" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_prefix(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        result = eos_create_prefix_list(
            "test-switch", "BAD-PL", [{"seq": 10, "action": "permit", "prefix": "not-a-prefix"}]
        )
        assert result["status"] == "error"
        assert "prefix" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_seq_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        result = eos_create_prefix_list(
            "test-switch", "BAD-PL", [{"seq": 0, "action": "permit", "prefix": "10.0.0.0/8"}]
        )
        assert result["status"] == "error"
        assert "seq" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_seq_too_large(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        result = eos_create_prefix_list(
            "test-switch", "BAD-PL", [{"seq": 99999, "action": "permit", "prefix": "10.0.0.0/8"}]
        )
        assert result["status"] == "error"
        assert "seq" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_empty_entries(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        result = eos_create_prefix_list("test-switch", "EMPTY-PL", [])
        assert result["status"] == "error"
        assert "empty" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        result = eos_create_prefix_list(
            "test-switch", "bad;name", [{"seq": 10, "action": "permit", "prefix": "10.0.0.0/8"}]
        )
        assert result["status"] == "error"
        assert "name" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_ge_exceeds_le(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        result = eos_create_prefix_list(
            "test-switch", "PL", [{"seq": 10, "action": "permit", "prefix": "10.0.0.0/8", "ge": 28, "le": 16}]
        )
        assert result["status"] == "error"
        assert "ge" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_ge_below_mask(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        result = eos_create_prefix_list(
            "test-switch", "PL", [{"seq": 10, "action": "permit", "prefix": "10.0.0.0/16", "ge": 8}]
        )
        assert result["status"] == "error"
        assert "ge" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_prefix_list

        with patch.object(settings, "net_read_only", True):
            result = eos_create_prefix_list(
                "test-switch", "PL", [{"seq": 10, "action": "permit", "prefix": "10.0.0.0/8"}]
            )
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_create_route_map ---


class TestCreateRouteMap:
    def test_success_basic(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        mock_node.config.return_value = [{}]
        clauses = [
            {
                "seq": 10,
                "action": "permit",
                "match": ["ip address prefix-list PL1"],
                "set": ["local-preference 200"],
            }
        ]
        result = eos_create_route_map("test-switch", "EXPORT-MAP", clauses)

        assert result["status"] == "success"
        assert result["action"] == "create_route_map"
        mock_node.config.assert_called_once_with(
            [
                "no route-map EXPORT-MAP",
                "route-map EXPORT-MAP permit 10",
                "match ip address prefix-list PL1",
                "set local-preference 200",
            ]
        )

    def test_success_multiple_clauses(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        mock_node.config.return_value = [{}]
        clauses = [
            {"seq": 10, "action": "permit", "match": ["ip address prefix-list PL1"]},
            {"seq": 20, "action": "deny"},
        ]
        result = eos_create_route_map("test-switch", "RM-MULTI", clauses)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "no route-map RM-MULTI",
                "route-map RM-MULTI permit 10",
                "match ip address prefix-list PL1",
                "route-map RM-MULTI deny 20",
            ]
        )

    def test_idempotent_removes_existing(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        mock_node.config.return_value = [{}]
        clauses = [{"seq": 10, "action": "permit"}]
        result = eos_create_route_map("test-switch", "OLD-RM", clauses)

        assert result["status"] == "success"
        commands = mock_node.config.call_args[0][0]
        assert commands[0] == "no route-map OLD-RM"

    def test_invalid_action(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        result = eos_create_route_map("test-switch", "RM", [{"seq": 10, "action": "accept"}])
        assert result["status"] == "error"
        assert "Invalid action" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_seq(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        result = eos_create_route_map("test-switch", "RM", [{"seq": 0, "action": "permit"}])
        assert result["status"] == "error"
        assert "seq" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_empty_clauses(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        result = eos_create_route_map("test-switch", "RM", [])
        assert result["status"] == "error"
        assert "empty" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        result = eos_create_route_map("test-switch", "bad|name", [{"seq": 10, "action": "permit"}])
        assert result["status"] == "error"
        assert "name" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_match_statement(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        result = eos_create_route_map("test-switch", "RM", [{"seq": 10, "action": "permit", "match": ["bad;match"]}])
        assert result["status"] == "error"
        assert "match statement" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_set_statement(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        result = eos_create_route_map("test-switch", "RM", [{"seq": 10, "action": "permit", "set": ["bad;set"]}])
        assert result["status"] == "error"
        assert "set statement" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_route_map

        with patch.object(settings, "net_read_only", True):
            result = eos_create_route_map("test-switch", "RM", [{"seq": 10, "action": "permit"}])
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_create_static_route ---


class TestCreateStaticRoute:
    def test_success_default_vrf(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        mock_node.config.return_value = [{}]
        result = eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1")

        assert result["status"] == "success"
        assert result["action"] == "create_static_route"
        mock_node.config.assert_called_once_with(["ip route 10.1.0.0/24 10.0.0.1 1"])

    def test_success_with_vrf(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        mock_node.config.return_value = [{}]
        result = eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1", vrf="PROD")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["ip route vrf PROD 10.1.0.0/24 10.0.0.1 1"])

    def test_success_with_admin_distance(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        mock_node.config.return_value = [{}]
        result = eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1", admin_distance=200)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["ip route 10.1.0.0/24 10.0.0.1 200"])

    def test_success_with_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        mock_node.config.return_value = [{}]
        result = eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1", name="backup-route")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["ip route 10.1.0.0/24 10.0.0.1 1 name backup-route"])

    def test_success_with_vrf_and_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        mock_node.config.return_value = [{}]
        result = eos_create_static_route(
            "test-switch", "10.1.0.0/24", "10.0.0.1", vrf="MGMT", admin_distance=150, name="mgmt-route"
        )

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["ip route vrf MGMT 10.1.0.0/24 10.0.0.1 150 name mgmt-route"])

    def test_invalid_prefix(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        result = eos_create_static_route("test-switch", "invalid", "10.0.0.1")
        assert result["status"] == "error"
        assert "prefix" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_next_hop(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        result = eos_create_static_route("test-switch", "10.1.0.0/24", "bad-ip")
        assert result["status"] == "error"
        assert "ip" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_admin_distance_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        result = eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1", admin_distance=0)
        assert result["status"] == "error"
        assert "admin_distance" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_admin_distance_too_large(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        result = eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1", admin_distance=256)
        assert result["status"] == "error"
        assert "admin_distance" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_vrf_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        result = eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1", vrf="bad;vrf")
        assert result["status"] == "error"
        mock_node.config.assert_not_called()

    def test_without_vrf_no_vrf_in_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        mock_node.config.return_value = [{}]
        eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1")

        cmd = mock_node.config.call_args[0][0][0]
        assert "vrf" not in cmd

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_static_route

        with patch.object(settings, "net_read_only", True):
            result = eos_create_static_route("test-switch", "10.1.0.0/24", "10.0.0.1")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_ospf ---


class TestConfigureOspf:
    def test_success_basic(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        mock_node.config.return_value = [{}]
        result = eos_configure_ospf("test-switch", 1, router_id="10.0.0.1")

        assert result["status"] == "success"
        assert result["action"] == "configure_ospf"
        mock_node.config.assert_called_once_with(["router ospf 1", "router-id 10.0.0.1"])

    def test_success_with_networks(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        mock_node.config.return_value = [{}]
        networks = [{"prefix": "10.0.0.0", "wildcard": "0.0.0.255", "area": "0"}]
        result = eos_configure_ospf("test-switch", 1, networks=networks)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["router ospf 1", "network 10.0.0.0 0.0.0.255 area 0"])

    def test_success_with_passive_interfaces(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        mock_node.config.return_value = [{}]
        result = eos_configure_ospf("test-switch", 1, passive_interfaces=["Loopback0", "Vlan100"])

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            ["router ospf 1", "passive-interface Loopback0", "passive-interface Vlan100"]
        )

    def test_success_full_config(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        mock_node.config.return_value = [{}]
        networks = [
            {"prefix": "10.0.0.0", "wildcard": "0.0.0.255", "area": "0"},
            {"prefix": "172.16.0.0", "wildcard": "0.0.255.255", "area": "1"},
        ]
        result = eos_configure_ospf(
            "test-switch",
            1,
            router_id="10.0.0.1",
            networks=networks,
            passive_interfaces=["Loopback0"],
        )

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "router ospf 1",
                "router-id 10.0.0.1",
                "network 10.0.0.0 0.0.0.255 area 0",
                "network 172.16.0.0 0.0.255.255 area 1",
                "passive-interface Loopback0",
            ]
        )

    def test_invalid_process_id_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        result = eos_configure_ospf("test-switch", 0)
        assert result["status"] == "error"
        assert "process_id" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_process_id_too_large(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        result = eos_configure_ospf("test-switch", 70000)
        assert result["status"] == "error"
        assert "process_id" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_router_id(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        result = eos_configure_ospf("test-switch", 1, router_id="not-an-ip")
        assert result["status"] == "error"
        assert "router_id" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_network_missing_area(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        result = eos_configure_ospf("test-switch", 1, networks=[{"prefix": "10.0.0.0", "wildcard": "0.0.0.255"}])
        assert result["status"] == "error"
        assert "area" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_passive_interface_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        result = eos_configure_ospf("test-switch", 1, passive_interfaces=["bad-intf"])
        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_ospf

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_ospf("test-switch", 1, router_id="10.0.0.1")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_bgp_timers ---


class TestConfigureBgpTimers:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        mock_node.config.return_value = [{}]
        result = eos_configure_bgp_timers("test-switch", 65000, 10, 30)

        assert result["status"] == "success"
        assert result["action"] == "configure_bgp_timers"
        mock_node.config.assert_called_once_with(["router bgp 65000", "timers bgp 10 30"])

    def test_success_minimum_values(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        mock_node.config.return_value = [{}]
        result = eos_configure_bgp_timers("test-switch", 65000, 1, 3)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["router bgp 65000", "timers bgp 1 3"])

    def test_invalid_asn(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        result = eos_configure_bgp_timers("test-switch", 0, 10, 30)
        assert result["status"] == "error"
        assert "ASN" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_keepalive_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        result = eos_configure_bgp_timers("test-switch", 65000, 0, 30)
        assert result["status"] == "error"
        assert "keepalive" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_keepalive_too_large(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        result = eos_configure_bgp_timers("test-switch", 65000, 3601, 10803)
        assert result["status"] == "error"
        assert "keepalive" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_holdtime_too_small(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        result = eos_configure_bgp_timers("test-switch", 65000, 1, 2)
        assert result["status"] == "error"
        assert "holdtime" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_holdtime_too_large(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        result = eos_configure_bgp_timers("test-switch", 65000, 10, 7201)
        assert result["status"] == "error"
        assert "holdtime" in result["error"]
        mock_node.config.assert_not_called()

    def test_holdtime_less_than_3x_keepalive(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        result = eos_configure_bgp_timers("test-switch", 65000, 10, 29)
        assert result["status"] == "error"
        assert "3x" in result["error"] or "3" in result["error"]
        assert "keepalive" in result["error"]
        mock_node.config.assert_not_called()

    def test_holdtime_exactly_3x_keepalive(self, mock_conn_mgr, mock_node):
        """holdtime == 3x keepalive should succeed."""
        from network_mcp.tools.routing import eos_configure_bgp_timers

        mock_node.config.return_value = [{}]
        result = eos_configure_bgp_timers("test-switch", 65000, 10, 30)

        assert result["status"] == "success"

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_timers

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_bgp_timers("test-switch", 65000, 10, 30)
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_bgp_auth ---


class TestConfigureBgpAuth:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_auth

        mock_node.config.return_value = [{}]
        result = eos_configure_bgp_auth("test-switch", 65000, "10.0.0.2", "s3cretKey")

        assert result["status"] == "success"
        assert result["action"] == "configure_bgp_auth"
        mock_node.config.assert_called_once_with(["router bgp 65000", "neighbor 10.0.0.2 password 7 s3cretKey"])

    def test_invalid_asn(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_auth

        result = eos_configure_bgp_auth("test-switch", 0, "10.0.0.2", "key")
        assert result["status"] == "error"
        assert "ASN" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_neighbor_ip(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_auth

        result = eos_configure_bgp_auth("test-switch", 65000, "bad-ip", "key")
        assert result["status"] == "error"
        assert "ip" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_neighbor_ip_injection(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_auth

        result = eos_configure_bgp_auth("test-switch", 65000, "10.0.0.2;drop", "key")
        assert result["status"] == "error"
        mock_node.config.assert_not_called()

    def test_empty_md5_key(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_auth

        result = eos_configure_bgp_auth("test-switch", 65000, "10.0.0.2", "")
        assert result["status"] == "error"
        assert "md5_key" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_md5_key_injection(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_auth

        result = eos_configure_bgp_auth("test-switch", 65000, "10.0.0.2", "key;bad")
        assert result["status"] == "error"
        assert "md5_key" in result["error"]
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_configure_bgp_auth

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_bgp_auth("test-switch", 65000, "10.0.0.2", "key")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- Validator unit tests ---


class TestValidateIpPrefix:
    def test_valid_prefix(self):
        from network_mcp.helpers import validate_ip_prefix

        assert validate_ip_prefix("10.0.0.0/8") is None
        assert validate_ip_prefix("192.168.1.0/24") is None
        assert validate_ip_prefix("0.0.0.0/0") is None

    def test_invalid_no_mask(self):
        from network_mcp.helpers import validate_ip_prefix

        err = validate_ip_prefix("10.0.0.0")
        assert err is not None
        assert "CIDR" in err

    def test_invalid_mask_too_large(self):
        from network_mcp.helpers import validate_ip_prefix

        err = validate_ip_prefix("10.0.0.0/33")
        assert err is not None

    def test_invalid_octet_too_large(self):
        from network_mcp.helpers import validate_ip_prefix

        err = validate_ip_prefix("256.0.0.0/8")
        assert err is not None

    def test_invalid_format(self):
        from network_mcp.helpers import validate_ip_prefix

        err = validate_ip_prefix("not-a-prefix/8")
        assert err is not None


class TestValidateIpAddress:
    def test_valid_address(self):
        from network_mcp.helpers import validate_ip_address

        assert validate_ip_address("10.0.0.1") is None
        assert validate_ip_address("192.168.1.1") is None
        assert validate_ip_address("0.0.0.0") is None  # noqa: S104
        assert validate_ip_address("255.255.255.255") is None

    def test_invalid_format(self):
        from network_mcp.helpers import validate_ip_address

        err = validate_ip_address("not-an-ip")
        assert err is not None

    def test_invalid_octet_too_large(self):
        from network_mcp.helpers import validate_ip_address

        err = validate_ip_address("256.0.0.1")
        assert err is not None

    def test_invalid_with_mask(self):
        from network_mcp.helpers import validate_ip_address

        err = validate_ip_address("10.0.0.0/8")
        assert err is not None
