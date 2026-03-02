"""Tests for safety infrastructure: read-only mode, command validation, input validation."""

from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.helpers import check_read_only, validate_config_commands
from network_mcp.server import settings

# --- Read-only mode enforcement ---


class TestReadOnlyMode:
    def test_default_is_read_only(self):
        """Default settings should have read-only mode enabled."""
        result = check_read_only()
        assert result is not None
        assert "Write operations are disabled" in result

    def test_read_only_false_allows_writes(self):
        """Setting net_read_only=False should allow writes."""
        with patch.object(settings, "net_read_only", False):
            result = check_read_only()
            assert result is None

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        """Write tools return error when net_read_only=True."""
        from network_mcp.tools.vlans import eos_create_vlan

        result = eos_create_vlan("test-switch", 100, "TEST")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]
        # Ensure no config commands were sent
        mock_node.config.assert_not_called()

    def test_write_allowed_when_not_read_only(self, mock_conn_mgr, mock_node):
        """Write tools work when net_read_only=False."""
        with patch.object(settings, "net_read_only", False):
            from network_mcp.tools.vlans import eos_create_vlan

            mock_node.config.return_value = [{}]
            result = eos_create_vlan("test-switch", 100, "TEST")
            assert result["status"] == "success"

    def test_all_write_tools_check_read_only(self, mock_conn_mgr, mock_node):
        """All write tools should respect read-only mode."""
        from network_mcp.tools.config_mgmt import (
            eos_push_config_commands,
            eos_save_running_config,
        )
        from network_mcp.tools.interfaces import (
            eos_configure_interface_description,
            eos_no_shutdown_interface,
            eos_shutdown_interface,
        )
        from network_mcp.tools.vlans import (
            eos_assign_interface_vlan,
            eos_create_vlan,
            eos_delete_vlan,
            eos_rename_vlan,
        )

        tools = [
            lambda: eos_create_vlan("switch", 100, "T"),
            lambda: eos_delete_vlan("switch", 100),
            lambda: eos_rename_vlan("switch", 100, "T"),
            lambda: eos_assign_interface_vlan("switch", "Ethernet1", 100),
            lambda: eos_configure_interface_description("switch", "Ethernet1", "d"),
            lambda: eos_shutdown_interface("switch", "Ethernet1"),
            lambda: eos_no_shutdown_interface("switch", "Ethernet1"),
            lambda: eos_push_config_commands("switch", ["vlan 10"]),
            lambda: eos_save_running_config("switch"),
        ]

        for tool_fn in tools:
            result = tool_fn()
            assert result["status"] == "error", f"Tool should be blocked: {tool_fn}"
            assert "Write operations are disabled" in result["error"]


# --- Command validation ---


class TestValidateConfigCommands:
    def test_allowed_commands(self):
        assert validate_config_commands(["vlan 100", "name TEST"]) is None

    def test_denied_reload(self):
        result = validate_config_commands(["reload"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_denied_write_erase(self):
        result = validate_config_commands(["write erase"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_denied_zerotouch(self):
        result = validate_config_commands(["zerotouch cancel"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_denied_delete(self):
        result = validate_config_commands(["delete flash:startup-config"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_denied_format(self):
        result = validate_config_commands(["format flash:"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_denied_boot_system(self):
        result = validate_config_commands(["boot system flash:bad.swi"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_denied_aaa_root(self):
        result = validate_config_commands(["aaa root secret 0 pass"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_case_insensitive(self):
        result = validate_config_commands(["RELOAD"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_mixed_commands_one_denied(self):
        result = validate_config_commands(["vlan 100", "reload"])
        assert result is not None
        assert "blocked" in result.lower()

    def test_empty_list(self):
        assert validate_config_commands([]) is None


# --- Input validation in write tools ---


class TestInputValidation:
    @pytest.fixture(autouse=True)
    def _allow_writes(self):
        with patch.object(settings, "net_read_only", False):
            yield

    def test_create_vlan_validates_host(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_create_vlan

        with pytest.raises(ToolError):
            eos_create_vlan("", 100, "TEST")

    def test_shutdown_interface_validates_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_shutdown_interface

        result = eos_shutdown_interface("test-switch", "invalid name")
        assert result["status"] == "error"
        assert "interface" in result["error"].lower()

    def test_assign_interface_vlan_validates_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_assign_interface_vlan

        result = eos_assign_interface_vlan("test-switch", "bad", 100)
        assert result["status"] == "error"
        assert "interface" in result["error"].lower()

    def test_configure_description_validates_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_description

        result = eos_configure_interface_description("test-switch", "", "desc")
        assert result["status"] == "error"

    def test_no_shutdown_validates_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_no_shutdown_interface

        result = eos_no_shutdown_interface("test-switch", "x")
        assert result["status"] == "error"

    def test_save_running_config_validates_host(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_save_running_config

        result = eos_save_running_config("")
        assert result["status"] == "error"
        assert "Host" in result["error"] or "host" in result["error"].lower()
