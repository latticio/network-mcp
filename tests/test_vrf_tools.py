"""Tests for VRF tools."""

import pyeapi.eapilib
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.tools.vrf import eos_get_vrf_detail, eos_get_vrfs
from tests.conftest import MOCK_VRF_DETAIL, MOCK_VRFS

# --- eos_get_vrfs ---


class TestGetVrfs:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_VRFS]
        result = eos_get_vrfs("test-switch")
        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert "default" in result["data"]
        assert "PROD" in result["data"]
        assert result["data"]["PROD"]["routeDistinguisher"] == "10.0.0.1:1"
        mock_node.run_commands.assert_called_once_with(["show vrf"], encoding="json")

    def test_empty_vrfs(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"vrfs": {}}]
        result = eos_get_vrfs("test-switch")
        assert result["status"] == "success"
        assert result["data"] == {}

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")
        result = eos_get_vrfs("bad-host")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = eos_get_vrfs("test-switch")
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        with pytest.raises(ToolError, match="Host is required"):
            eos_get_vrfs("")


# --- eos_get_vrf_detail ---


class TestGetVrfDetail:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_VRF_DETAIL]
        result = eos_get_vrf_detail("test-switch", name="PROD")
        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["data"]["routeDistinguisher"] == "10.0.0.1:1"
        assert "Vlan100" in result["data"]["interfaces"]
        assert "Vlan200" in result["data"]["interfaces"]
        mock_node.run_commands.assert_called_once_with(["show vrf PROD"], encoding="json")

    def test_not_found(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"vrfs": {}}]
        result = eos_get_vrf_detail("test-switch", name="NONEXISTENT")
        assert result["status"] == "error"
        assert result["device"] == "test-switch"
        assert "VRF NONEXISTENT not found" in result["error"]

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")
        result = eos_get_vrf_detail("bad-host", name="PROD")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = eos_get_vrf_detail("test-switch", name="PROD")
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_invalid_vrf_name_injection(self, mock_conn_mgr, mock_node):
        """VRF name with CLI injection characters should be rejected."""
        result = eos_get_vrf_detail("test-switch", name="PROD; reload")
        assert result["status"] == "error"

    def test_command_passes_name(self, mock_conn_mgr, mock_node):
        """Verify the VRF name is correctly passed in the EOS command."""
        mock_node.run_commands.return_value = [MOCK_VRF_DETAIL]
        eos_get_vrf_detail("test-switch", name="PROD")
        mock_node.run_commands.assert_called_once_with(["show vrf PROD"], encoding="json")

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        with pytest.raises(ToolError, match="Host is required"):
            eos_get_vrf_detail("", name="PROD")
