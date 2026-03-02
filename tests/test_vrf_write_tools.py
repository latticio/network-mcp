"""Tests for VRF configuration write tools (create VRF, interface assignment, route leaking)."""

from unittest.mock import patch

import pytest

from network_mcp.server import settings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


# --- eos_create_vrf ---


class TestCreateVrf:
    def test_success_import_and_export(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        mock_node.config.return_value = [{}]
        result = eos_create_vrf(
            "test-switch",
            "PROD",
            "65001:100",
            [
                {"rt": "65001:100", "direction": "import"},
                {"rt": "65001:200", "direction": "export"},
            ],
        )

        assert result["status"] == "success"
        assert result["action"] == "create_vrf"
        mock_node.config.assert_called_once_with(
            [
                "vrf instance PROD",
                "rd 65001:100",
                "route-target import 65001:100",
                "route-target export 65001:200",
            ]
        )

    def test_success_both_direction(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        mock_node.config.return_value = [{}]
        result = eos_create_vrf(
            "test-switch",
            "TENANT-A",
            "10.0.0.1:100",
            [{"rt": "65001:100", "direction": "both"}],
        )

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "vrf instance TENANT-A",
                "rd 10.0.0.1:100",
                "route-target import 65001:100",
                "route-target export 65001:100",
            ]
        )

    def test_success_import_only(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        mock_node.config.return_value = [{}]
        result = eos_create_vrf(
            "test-switch",
            "MGMT",
            "65001:50",
            [{"rt": "65001:50", "direction": "import"}],
        )

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "vrf instance MGMT",
                "rd 65001:50",
                "route-target import 65001:50",
            ]
        )

    def test_invalid_rd_format(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        result = eos_create_vrf(
            "test-switch",
            "PROD",
            "bad-rd",
            [{"rt": "65001:100", "direction": "both"}],
        )

        assert result["status"] == "error"
        assert "Invalid route distinguisher" in result["error"]
        mock_node.config.assert_not_called()

    def test_empty_rd(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        result = eos_create_vrf(
            "test-switch",
            "PROD",
            "",
            [{"rt": "65001:100", "direction": "both"}],
        )

        assert result["status"] == "error"
        assert "empty" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_direction(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        result = eos_create_vrf(
            "test-switch",
            "PROD",
            "65001:100",
            [{"rt": "65001:100", "direction": "inbound"}],
        )

        assert result["status"] == "error"
        assert "Invalid route target direction" in result["error"]
        mock_node.config.assert_not_called()

    def test_empty_route_targets(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        result = eos_create_vrf("test-switch", "PROD", "65001:100", [])

        assert result["status"] == "error"
        assert "route target" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        result = eos_create_vrf(
            "test-switch",
            "",
            "65001:100",
            [{"rt": "65001:100", "direction": "both"}],
        )

        assert result["status"] == "error"
        assert "name" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_multiple_route_targets(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        mock_node.config.return_value = [{}]
        result = eos_create_vrf(
            "test-switch",
            "PROD",
            "65001:100",
            [
                {"rt": "65001:100", "direction": "both"},
                {"rt": "65002:200", "direction": "import"},
                {"rt": "65003:300", "direction": "export"},
            ],
        )

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "vrf instance PROD",
                "rd 65001:100",
                "route-target import 65001:100",
                "route-target export 65001:100",
                "route-target import 65002:200",
                "route-target export 65003:300",
            ]
        )

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_create_vrf

        with patch.object(settings, "net_read_only", True):
            result = eos_create_vrf(
                "test-switch",
                "PROD",
                "65001:100",
                [{"rt": "65001:100", "direction": "both"}],
            )
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_assign_interface_to_vrf ---


class TestAssignInterfaceToVrf:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_assign_interface_to_vrf

        mock_node.config.return_value = [{}]
        result = eos_assign_interface_to_vrf("test-switch", "Ethernet1", "PROD")

        assert result["status"] == "success"
        assert result["action"] == "assign_interface_to_vrf"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "vrf PROD"])

    def test_success_loopback(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_assign_interface_to_vrf

        mock_node.config.return_value = [{}]
        result = eos_assign_interface_to_vrf("test-switch", "Loopback0", "MGMT")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Loopback0", "vrf MGMT"])

    def test_success_vlan(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_assign_interface_to_vrf

        mock_node.config.return_value = [{}]
        result = eos_assign_interface_to_vrf("test-switch", "Vlan100", "TENANT-A")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Vlan100", "vrf TENANT-A"])

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_assign_interface_to_vrf

        result = eos_assign_interface_to_vrf("test-switch", "bad-intf", "PROD")

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_empty_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_assign_interface_to_vrf

        result = eos_assign_interface_to_vrf("test-switch", "", "PROD")

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_vrf_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_assign_interface_to_vrf

        result = eos_assign_interface_to_vrf("test-switch", "Ethernet1", "")

        assert result["status"] == "error"
        assert "vrf" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_assign_interface_to_vrf

        with patch.object(settings, "net_read_only", True):
            result = eos_assign_interface_to_vrf("test-switch", "Ethernet1", "PROD")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_vrf_route_leaking ---


class TestConfigureVrfRouteLeaking:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_configure_vrf_route_leaking

        mock_node.run_commands.return_value = [
            {"vrfs": {"default": {"asn": "65000", "routerId": "10.0.0.1", "peers": {}}}}
        ]
        mock_node.config.return_value = [{}]
        result = eos_configure_vrf_route_leaking("test-switch", "PROD", "DEV", "LEAK-FILTER")

        assert result["status"] == "success"
        assert result["action"] == "configure_vrf_route_leaking"
        mock_node.config.assert_called_once_with(
            [
                "router bgp 65000",
                "vrf DEV",
                "address-family ipv4",
                "import vrf PROD prefix-list LEAK-FILTER",
            ]
        )

    def test_verify_bgp_show_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_configure_vrf_route_leaking

        mock_node.run_commands.return_value = [
            {"vrfs": {"default": {"asn": "65100", "routerId": "10.0.0.1", "peers": {}}}}
        ]
        mock_node.config.return_value = [{}]
        result = eos_configure_vrf_route_leaking("test-switch", "SRC", "DST", "PL-FILTER")

        assert result["status"] == "success"
        mock_node.run_commands.assert_called_once_with(["show ip bgp summary"], encoding="json")
        mock_node.config.assert_called_once_with(
            [
                "router bgp 65100",
                "vrf DST",
                "address-family ipv4",
                "import vrf SRC prefix-list PL-FILTER",
            ]
        )

    def test_no_bgp_configured(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_configure_vrf_route_leaking

        mock_node.run_commands.return_value = [{"vrfs": {"default": {"asn": "", "routerId": "", "peers": {}}}}]
        result = eos_configure_vrf_route_leaking("test-switch", "PROD", "DEV", "LEAK-FILTER")

        assert result["status"] == "error"
        assert "BGP is not configured" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_source_vrf(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_configure_vrf_route_leaking

        result = eos_configure_vrf_route_leaking("test-switch", "", "DEV", "LEAK-FILTER")

        assert result["status"] == "error"
        assert "source_vrf" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_dest_vrf(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_configure_vrf_route_leaking

        result = eos_configure_vrf_route_leaking("test-switch", "PROD", "", "LEAK-FILTER")

        assert result["status"] == "error"
        assert "dest_vrf" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_prefix_list(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_configure_vrf_route_leaking

        result = eos_configure_vrf_route_leaking("test-switch", "PROD", "DEV", "")

        assert result["status"] == "error"
        assert "prefix_list" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vrf import eos_configure_vrf_route_leaking

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_vrf_route_leaking("test-switch", "PROD", "DEV", "LEAK-FILTER")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- validate_route_distinguisher ---


class TestValidateRouteDistinguisher:
    def test_valid_asn_format(self):
        from network_mcp.helpers import validate_route_distinguisher

        assert validate_route_distinguisher("65001:100") is None
        assert validate_route_distinguisher("1:1") is None
        assert validate_route_distinguisher("65535:65535") is None

    def test_valid_ip_format(self):
        from network_mcp.helpers import validate_route_distinguisher

        assert validate_route_distinguisher("10.0.0.1:100") is None
        assert validate_route_distinguisher("192.168.1.1:0") is None

    def test_invalid_format(self):
        from network_mcp.helpers import validate_route_distinguisher

        err = validate_route_distinguisher("bad-rd")
        assert err is not None
        assert "Invalid route distinguisher" in err

    def test_empty(self):
        from network_mcp.helpers import validate_route_distinguisher

        err = validate_route_distinguisher("")
        assert err is not None
        assert "empty" in err.lower()

    def test_missing_colon(self):
        from network_mcp.helpers import validate_route_distinguisher

        err = validate_route_distinguisher("65001100")
        assert err is not None
        assert "Invalid route distinguisher" in err
