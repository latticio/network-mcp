"""Tests for ACL assignment tools in security.py."""

from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.server import settings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


class TestApplyAclToInterface:
    def test_success_inbound(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl_to_interface

        mock_node.config.return_value = [{}]
        result = eos_apply_acl_to_interface("test-switch", "Ethernet1", "MGMT-ACCESS", "in")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["action"] == "apply_acl_to_interface"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "ip access-group MGMT-ACCESS in"])

    def test_success_outbound(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl_to_interface

        mock_node.config.return_value = [{}]
        result = eos_apply_acl_to_interface("test-switch", "Vlan100", "EGRESS-FILTER", "out")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Vlan100", "ip access-group EGRESS-FILTER out"])

    def test_invalid_direction(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl_to_interface

        result = eos_apply_acl_to_interface("test-switch", "Ethernet1", "MGMT-ACCESS", "both")
        assert result["status"] == "error"
        assert "direction" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl_to_interface

        result = eos_apply_acl_to_interface("test-switch", "bad-intf", "MGMT-ACCESS", "in")
        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_acl_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl_to_interface

        result = eos_apply_acl_to_interface("test-switch", "Ethernet1", "bad;acl", "in")
        assert result["status"] == "error"
        assert "acl_name" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl_to_interface

        with pytest.raises(ToolError):
            eos_apply_acl_to_interface("", "Ethernet1", "MGMT-ACCESS", "in")

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl_to_interface

        with patch.object(settings, "net_read_only", True):
            result = eos_apply_acl_to_interface("test-switch", "Ethernet1", "MGMT-ACCESS", "in")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


class TestRemoveAclFromInterface:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_remove_acl_from_interface

        mock_node.config.return_value = [{}]
        result = eos_remove_acl_from_interface("test-switch", "Ethernet1", "MGMT-ACCESS", "in")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["action"] == "remove_acl_from_interface"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "no ip access-group MGMT-ACCESS in"])

    def test_invalid_direction(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_remove_acl_from_interface

        result = eos_remove_acl_from_interface("test-switch", "Ethernet1", "MGMT-ACCESS", "invalid")
        assert result["status"] == "error"
        assert "direction" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_remove_acl_from_interface

        result = eos_remove_acl_from_interface("test-switch", "xyz123", "MGMT-ACCESS", "out")
        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()
