"""Tests for VLAN tools (read and write)."""

from unittest.mock import patch

import pyeapi.eapilib
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.tools.vlans import (
    eos_assign_interface_vlan,
    eos_create_vlan,
    eos_delete_vlan,
    eos_get_vlan_detail,
    eos_get_vlans,
    eos_rename_vlan,
)
from tests.conftest import MOCK_EMPTY_RESPONSE, MOCK_VLANS

# --- eos_get_vlans ---


class TestGetVlans:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_VLANS]
        result = eos_get_vlans("test-switch")
        assert result["status"] == "success"
        assert "1" in result["data"]
        assert "100" in result["data"]
        assert result["data"]["100"]["name"] == "SERVERS"

    def test_empty_vlans(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"vlans": {}}]
        result = eos_get_vlans("test-switch")
        assert result["status"] == "success"
        assert result["data"] == {}

    def test_with_field_filtering(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_VLANS]
        result = eos_get_vlans("test-switch", fields=["name"])
        assert result["status"] == "success"
        assert "name" in result["data"]["100"]

    def test_with_pagination(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_VLANS]
        result = eos_get_vlans("test-switch", limit=1, offset=0)
        assert result["status"] == "success"
        assert "pagination" in result
        assert result["pagination"]["returned"] == 1

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_vlans("bad-host")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        with pytest.raises(ToolError, match="Host is required"):
            eos_get_vlans("")


# --- eos_get_vlan_detail ---


class TestGetVlanDetail:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_VLANS]
        result = eos_get_vlan_detail("test-switch", 100)
        assert result["status"] == "success"
        assert result["data"]["name"] == "SERVERS"

    def test_vlan_not_found(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"vlans": {}}]
        result = eos_get_vlan_detail("test-switch", 999)
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_invalid_id_zero(self, mock_conn_mgr, mock_node):
        result = eos_get_vlan_detail("test-switch", 0)
        assert result["status"] == "error"
        assert "must be between" in result["error"]

    def test_invalid_id_4095(self, mock_conn_mgr, mock_node):
        result = eos_get_vlan_detail("test-switch", 4095)
        assert result["status"] == "error"
        assert "must be between" in result["error"]

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_vlan_detail("bad-host", 100)
        assert result["status"] == "error"
        assert "Connection to" in result["error"]


# --- eos_create_vlan ---


class TestCreateVlan:
    @patch("network_mcp.tools.vlans.run_config_command")
    def test_success(self, mock_run_config, mock_conn_mgr, mock_node):
        mock_run_config.return_value = {"status": "success", "device": "test-switch", "action": "create_vlan"}
        result = eos_create_vlan("test-switch", 200, "STORAGE")
        assert result["status"] == "success"
        mock_run_config.assert_called_once()

    def test_invalid_id_zero(self, mock_conn_mgr, mock_node):
        result = eos_create_vlan("test-switch", 0, "BAD")
        assert result["status"] == "error"
        assert "must be between" in result["error"]

    def test_invalid_id_4095(self, mock_conn_mgr, mock_node):
        result = eos_create_vlan("test-switch", 4095, "BAD")
        assert result["status"] == "error"
        assert "must be between" in result["error"]

    def test_invalid_name_special_chars(self, mock_conn_mgr, mock_node):
        result = eos_create_vlan("test-switch", 100, "bad@name!")
        assert result["status"] == "error"
        assert "alphanumeric" in result["error"]

    def test_invalid_name_too_long(self, mock_conn_mgr, mock_node):
        result = eos_create_vlan("test-switch", 100, "a" * 33)
        assert result["status"] == "error"
        assert "32 characters" in result["error"]

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_create_vlan("test-switch", 100, "TEST")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]


# --- eos_delete_vlan ---


class TestDeleteVlan:
    @patch("network_mcp.tools.vlans.run_config_command")
    def test_success(self, mock_run_config, mock_conn_mgr, mock_node):
        mock_run_config.return_value = {"status": "success", "device": "test-switch", "action": "delete_vlan"}
        result = eos_delete_vlan("test-switch", 100)
        assert result["status"] == "success"
        mock_run_config.assert_called_once()

    def test_invalid_id(self, mock_conn_mgr, mock_node):
        result = eos_delete_vlan("test-switch", 0)
        assert result["status"] == "error"
        assert "must be between" in result["error"]

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_delete_vlan("test-switch", 100)
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]


# --- eos_rename_vlan ---


class TestRenameVlan:
    @patch("network_mcp.tools.vlans.run_config_command")
    def test_success(self, mock_run_config, mock_conn_mgr, mock_node):
        mock_run_config.return_value = {"status": "success", "device": "test-switch", "action": "rename_vlan"}
        result = eos_rename_vlan("test-switch", 100, "NEW-NAME")
        assert result["status"] == "success"
        mock_run_config.assert_called_once()

    def test_invalid_id(self, mock_conn_mgr, mock_node):
        result = eos_rename_vlan("test-switch", 0, "NEW-NAME")
        assert result["status"] == "error"
        assert "must be between" in result["error"]

    def test_invalid_name(self, mock_conn_mgr, mock_node):
        result = eos_rename_vlan("test-switch", 100, "bad@name!")
        assert result["status"] == "error"
        assert "alphanumeric" in result["error"]

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_rename_vlan("test-switch", 100, "NEW-NAME")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]


# --- eos_assign_interface_vlan ---


class TestAssignInterfaceVlan:
    @patch("network_mcp.tools.vlans.run_config_command")
    def test_success(self, mock_run_config, mock_conn_mgr, mock_node):
        mock_run_config.return_value = {
            "status": "success",
            "device": "test-switch",
            "action": "assign_interface_vlan",
        }
        result = eos_assign_interface_vlan("test-switch", "Ethernet1", 100)
        assert result["status"] == "success"

    def test_invalid_vlan_id(self, mock_conn_mgr, mock_node):
        result = eos_assign_interface_vlan("test-switch", "Ethernet1", 0)
        assert result["status"] == "error"
        assert "must be between" in result["error"]

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        result = eos_assign_interface_vlan("test-switch", "x", 100)
        assert result["status"] == "error"

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_assign_interface_vlan("test-switch", "Ethernet1", 100)
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]


# --- Empty and error response tests ---


class TestVlanErrorScenarios:
    def test_get_vlans_empty_response(self, mock_conn_mgr, mock_node_empty):
        """Empty device response returns success with empty data."""
        result = eos_get_vlans("test-switch")
        assert result["status"] == "success"
        assert result["data"] == {}

    def test_get_vlan_detail_empty_response(self, mock_conn_mgr, mock_node_empty):
        """Empty device response means VLAN not found."""
        result = eos_get_vlan_detail("test-switch", 100)
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_get_vlans_timeout(self, mock_conn_mgr, mock_node_timeout):
        """Timeout returns error."""
        result = eos_get_vlans("test-switch")
        assert result["status"] == "error"

    def test_get_vlan_detail_connection_refused(self, mock_conn_mgr, mock_node_connection_error):
        """Connection error returns error."""
        result = eos_get_vlan_detail("test-switch", 100)
        assert result["status"] == "error"

    def test_get_vlans_malformed_response(self, mock_conn_mgr, mock_node):
        """Missing 'vlans' key in response returns empty data."""
        mock_node.run_commands.return_value = [MOCK_EMPTY_RESPONSE]
        result = eos_get_vlans("test-switch")
        assert result["status"] == "success"
        assert result["data"] == {}
