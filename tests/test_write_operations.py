"""Tests for write operation tools (VLAN, interface, config_mgmt)."""

from unittest.mock import patch

import pytest

from network_mcp.server import settings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


# --- VLAN write tools ---


class TestCreateVlan:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_create_vlan

        mock_node.config.return_value = [{}]
        result = eos_create_vlan("test-switch", 100, "SERVERS")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["action"] == "create_vlan"
        mock_node.config.assert_called_once_with(["vlan 100", "name SERVERS"])

    def test_invalid_vlan_id(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_create_vlan

        result = eos_create_vlan("test-switch", 0, "BAD")
        assert result["status"] == "error"
        assert "VLAN ID" in result["error"]

    def test_invalid_vlan_id_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_create_vlan

        result = eos_create_vlan("test-switch", 4095, "BAD")
        assert result["status"] == "error"
        assert "VLAN ID" in result["error"]


class TestDeleteVlan:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_delete_vlan

        mock_node.config.return_value = [{}]
        result = eos_delete_vlan("test-switch", 100)

        assert result["status"] == "success"
        assert result["action"] == "delete_vlan"
        mock_node.config.assert_called_once_with(["no vlan 100"])

    def test_invalid_vlan_id(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_delete_vlan

        result = eos_delete_vlan("test-switch", -1)
        assert result["status"] == "error"


class TestRenameVlan:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_rename_vlan

        mock_node.config.return_value = [{}]
        result = eos_rename_vlan("test-switch", 100, "NEW-NAME")

        assert result["status"] == "success"
        assert result["action"] == "rename_vlan"
        mock_node.config.assert_called_once_with(["vlan 100", "name NEW-NAME"])


class TestAssignInterfaceVlan:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_assign_interface_vlan

        mock_node.config.return_value = [{}]
        result = eos_assign_interface_vlan("test-switch", "Ethernet1", 100)

        assert result["status"] == "success"
        assert result["action"] == "assign_interface_vlan"
        mock_node.config.assert_called_once_with(
            ["interface Ethernet1", "switchport mode access", "switchport access vlan 100"]
        )

    def test_invalid_vlan_id(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_assign_interface_vlan

        result = eos_assign_interface_vlan("test-switch", "Ethernet1", 5000)
        assert result["status"] == "error"
        assert "VLAN ID" in result["error"]

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.vlans import eos_assign_interface_vlan

        result = eos_assign_interface_vlan("test-switch", "bad-intf", 100)
        assert result["status"] == "error"
        assert "interface" in result["error"].lower()


# --- Interface write tools ---


class TestConfigureInterfaceDescription:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_description

        mock_node.config.return_value = [{}]
        result = eos_configure_interface_description("test-switch", "Ethernet1", "to-spine-01")

        assert result["status"] == "success"
        assert result["action"] == "configure_interface_description"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "description to-spine-01"])

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_configure_interface_description

        result = eos_configure_interface_description("test-switch", "invalid", "desc")
        assert result["status"] == "error"


class TestShutdownInterface:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_shutdown_interface

        mock_node.config.return_value = [{}]
        result = eos_shutdown_interface("test-switch", "Ethernet1")

        assert result["status"] == "success"
        assert result["action"] == "shutdown_interface"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "shutdown"])

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_shutdown_interface

        result = eos_shutdown_interface("test-switch", "invalid name")
        assert result["status"] == "error"


class TestNoShutdownInterface:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.interfaces import eos_no_shutdown_interface

        mock_node.config.return_value = [{}]
        result = eos_no_shutdown_interface("test-switch", "Ethernet1")

        assert result["status"] == "success"
        assert result["action"] == "no_shutdown_interface"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "no shutdown"])


# --- Config management write tools ---


class TestPushConfigCommands:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_push_config_commands

        mock_node.config.return_value = [{}]
        commands = ["interface Ethernet1", "description test"]
        result = eos_push_config_commands("test-switch", commands)

        assert result["status"] == "success"
        assert result["action"] == "push_config_commands"
        mock_node.config.assert_called_once_with(commands)

    def test_empty_commands(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_push_config_commands

        result = eos_push_config_commands("test-switch", [])
        assert result["status"] == "error"
        assert "No commands" in result["error"]

    def test_denied_reload(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_push_config_commands

        result = eos_push_config_commands("test-switch", ["reload"])
        assert result["status"] == "error"
        assert "blocked" in result["error"].lower()

    def test_denied_write_erase(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_push_config_commands

        result = eos_push_config_commands("test-switch", ["write erase"])
        assert result["status"] == "error"
        assert "blocked" in result["error"].lower()


class TestSaveRunningConfig:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_save_running_config

        mock_node.run_commands.return_value = [{}]
        result = eos_save_running_config("test-switch")

        assert result["status"] == "success"
        assert result["action"] == "save_running_config"
        mock_node.run_commands.assert_called_once_with(["copy running-config startup-config"])
