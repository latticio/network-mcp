"""Tests for BGP write tools in routing.py."""

from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.server import settings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


class TestCreateBgpNeighbor:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        mock_node.config.return_value = [{}]
        result = eos_create_bgp_neighbor("test-switch", 65000, "10.0.0.2", 65001)

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["action"] == "create_bgp_neighbor"
        mock_node.config.assert_called_once_with(["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"])

    def test_success_with_description(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        mock_node.config.return_value = [{}]
        result = eos_create_bgp_neighbor("test-switch", 65000, "10.0.0.2", 65001, description="spine-01")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "router bgp 65000",
                "neighbor 10.0.0.2 remote-as 65001",
                "neighbor 10.0.0.2 description spine-01",
            ]
        )

    def test_invalid_local_asn_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        result = eos_create_bgp_neighbor("test-switch", 0, "10.0.0.2", 65001)
        assert result["status"] == "error"
        assert "ASN" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_local_asn_too_large(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        result = eos_create_bgp_neighbor("test-switch", 4294967296, "10.0.0.2", 65001)
        assert result["status"] == "error"
        assert "ASN" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_remote_asn(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        result = eos_create_bgp_neighbor("test-switch", 65000, "10.0.0.2", 0)
        assert result["status"] == "error"
        assert "ASN" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_neighbor_ip(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        result = eos_create_bgp_neighbor("test-switch", 65000, "invalid;ip", 65001)
        assert result["status"] == "error"
        assert "neighbor_ip" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_description(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        result = eos_create_bgp_neighbor("test-switch", 65000, "10.0.0.2", 65001, description="bad;desc")
        assert result["status"] == "error"
        assert "description" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        with pytest.raises(ToolError):
            eos_create_bgp_neighbor("", 65000, "10.0.0.2", 65001)

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_create_bgp_neighbor

        with patch.object(settings, "net_read_only", True):
            result = eos_create_bgp_neighbor("test-switch", 65000, "10.0.0.2", 65001)
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


class TestDeleteBgpNeighbor:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_delete_bgp_neighbor

        mock_node.config.return_value = [{}]
        result = eos_delete_bgp_neighbor("test-switch", 65000, "10.0.0.2")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["action"] == "delete_bgp_neighbor"
        mock_node.config.assert_called_once_with(["router bgp 65000", "no neighbor 10.0.0.2"])

    def test_invalid_asn(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_delete_bgp_neighbor

        result = eos_delete_bgp_neighbor("test-switch", -1, "10.0.0.2")
        assert result["status"] == "error"
        assert "ASN" in result["error"]

    def test_invalid_neighbor_ip(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_delete_bgp_neighbor

        result = eos_delete_bgp_neighbor("test-switch", 65000, "bad|ip")
        assert result["status"] == "error"
        assert "neighbor_ip" in result["error"]


class TestEnableBgpAf:
    def test_success_ipv4_unicast(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_enable_bgp_af

        mock_node.config.return_value = [{}]
        result = eos_enable_bgp_af("test-switch", 65000, "ipv4 unicast", "10.0.0.2")

        assert result["status"] == "success"
        assert result["action"] == "enable_bgp_af"
        mock_node.config.assert_called_once_with(
            ["router bgp 65000", "address-family ipv4 unicast", "neighbor 10.0.0.2 activate"]
        )

    def test_success_ipv6_unicast(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_enable_bgp_af

        mock_node.config.return_value = [{}]
        result = eos_enable_bgp_af("test-switch", 65000, "ipv6 unicast", "10.0.0.2")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            ["router bgp 65000", "address-family ipv6 unicast", "neighbor 10.0.0.2 activate"]
        )

    def test_success_evpn(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_enable_bgp_af

        mock_node.config.return_value = [{}]
        result = eos_enable_bgp_af("test-switch", 65000, "evpn", "10.0.0.2")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            ["router bgp 65000", "address-family evpn", "neighbor 10.0.0.2 activate"]
        )

    def test_invalid_address_family(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_enable_bgp_af

        result = eos_enable_bgp_af("test-switch", 65000, "invalid-af", "10.0.0.2")
        assert result["status"] == "error"
        assert "address_family" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_asn(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_enable_bgp_af

        result = eos_enable_bgp_af("test-switch", 0, "ipv4 unicast", "10.0.0.2")
        assert result["status"] == "error"
        assert "ASN" in result["error"]

    def test_invalid_neighbor_ip(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_enable_bgp_af

        result = eos_enable_bgp_af("test-switch", 65000, "ipv4 unicast", "bad;ip")
        assert result["status"] == "error"
        assert "neighbor_ip" in result["error"]

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.routing import eos_enable_bgp_af

        with patch.object(settings, "net_read_only", True):
            result = eos_enable_bgp_af("test-switch", 65000, "ipv4 unicast", "10.0.0.2")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()
