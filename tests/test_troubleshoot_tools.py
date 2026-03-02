"""Tests for troubleshooting tools: ping, traceroute, and interface flap detection."""

import pyeapi.eapilib
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.tools.troubleshoot import (
    eos_check_interface_flaps,
    eos_ping_from_device,
    eos_traceroute_from_device,
)
from tests.conftest import MOCK_INTERFACES_STATUS

# --- Mock responses ---

MOCK_PING_OUTPUT = {
    "output": (
        "PING 10.0.0.2 (10.0.0.2) 72(100) bytes of data.\n"
        "80 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.5 ms\n"
        "--- 10.0.0.2 ping statistics ---\n"
        "5 packets transmitted, 5 received, 0% packet loss, time 4ms\n"
    )
}

MOCK_TRACEROUTE_OUTPUT = {
    "output": (
        "traceroute to 10.0.0.2 (10.0.0.2), 30 hops max, 60 byte packets\n"
        " 1  10.0.0.1  0.5 ms  0.4 ms  0.3 ms\n"
        " 2  10.0.0.2  1.0 ms  0.9 ms  0.8 ms\n"
    )
}

MOCK_LOG_WITH_FLAPS = {
    "output": (
        "Jan  1 00:00:01 switch %LINEPROTO-5-UPDOWN: Ethernet1 changed state to down\n"
        "Jan  1 00:00:05 switch %LINEPROTO-5-UPDOWN: Ethernet1 changed state to up\n"
        "Jan  1 00:00:10 switch %SYS-5-CONFIG_I: Configured from console\n"
    )
}

MOCK_LOG_NO_FLAPS = {"output": "Jan  1 00:00:10 switch %SYS-5-CONFIG_I: Configured from console\n"}


# --- eos_ping_from_device ---


class TestPingFromDevice:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PING_OUTPUT]
        result = eos_ping_from_device("test-switch", "10.0.0.2")
        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert "0% packet loss" in result["data"]["output"]

    def test_uses_text_encoding(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PING_OUTPUT]
        eos_ping_from_device("test-switch", "10.0.0.2")
        mock_node.run_commands.assert_called_once_with(["ping 10.0.0.2 repeat 5"], encoding="text")

    def test_with_source(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PING_OUTPUT]
        eos_ping_from_device("test-switch", "10.0.0.2", source="Loopback0")
        mock_node.run_commands.assert_called_once_with(["ping 10.0.0.2 source Loopback0 repeat 5"], encoding="text")

    def test_with_custom_count(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PING_OUTPUT]
        eos_ping_from_device("test-switch", "10.0.0.2", count=10)
        mock_node.run_commands.assert_called_once_with(["ping 10.0.0.2 repeat 10"], encoding="text")

    def test_with_source_and_count(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PING_OUTPUT]
        eos_ping_from_device("test-switch", "10.0.0.2", source="10.1.1.1", count=3)
        mock_node.run_commands.assert_called_once_with(["ping 10.0.0.2 source 10.1.1.1 repeat 3"], encoding="text")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_ping_from_device("test-switch", "10.0.0.2")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        with pytest.raises(ToolError):
            eos_ping_from_device("", "10.0.0.2")


# --- eos_traceroute_from_device ---


class TestTracerouteFromDevice:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_OUTPUT]
        result = eos_traceroute_from_device("test-switch", "10.0.0.2")
        assert result["status"] == "success"
        assert "traceroute to 10.0.0.2" in result["data"]["output"]

    def test_uses_text_encoding(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_OUTPUT]
        eos_traceroute_from_device("test-switch", "10.0.0.2")
        mock_node.run_commands.assert_called_once_with(["traceroute 10.0.0.2"], encoding="text")

    def test_with_source(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_OUTPUT]
        eos_traceroute_from_device("test-switch", "10.0.0.2", source="Loopback0")
        mock_node.run_commands.assert_called_once_with(["traceroute 10.0.0.2 source Loopback0"], encoding="text")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_traceroute_from_device("test-switch", "10.0.0.2")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        with pytest.raises(ToolError):
            eos_traceroute_from_device("", "10.0.0.2")


# --- eos_check_interface_flaps ---


class TestCheckInterfaceFlaps:
    def test_success_with_flaps(self, mock_conn_mgr, mock_node):
        # First call: JSON commands (show interfaces status)
        # Second call: text command (show logging last N)
        mock_node.run_commands.side_effect = [
            [MOCK_INTERFACES_STATUS],  # JSON: show interfaces status
            [MOCK_LOG_WITH_FLAPS],  # text: show logging last 100
        ]
        result = eos_check_interface_flaps("test-switch")
        assert result["status"] == "success"
        assert result["data"]["flap_count"] == 2
        assert len(result["data"]["flap_events"]) == 2

    def test_success_no_flaps(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = [
            [MOCK_INTERFACES_STATUS],
            [MOCK_LOG_NO_FLAPS],
        ]
        result = eos_check_interface_flaps("test-switch")
        assert result["status"] == "success"
        assert result["data"]["flap_count"] == 0
        assert result["data"]["flap_events"] == []

    def test_with_specific_interface(self, mock_conn_mgr, mock_node):
        # 3 separate run_show_command calls: interfaces status, interface detail, logging
        mock_node.run_commands.side_effect = [
            [MOCK_INTERFACES_STATUS],
            [{"interfaces": {"Ethernet1": {"name": "Ethernet1"}}}],
            [MOCK_LOG_WITH_FLAPS],
        ]
        result = eos_check_interface_flaps("test-switch", interface="Ethernet1")
        assert result["status"] == "success"
        # Should find flaps for Ethernet1
        assert result["data"]["flap_count"] == 2

    def test_with_interface_filter_no_match(self, mock_conn_mgr, mock_node):
        # 3 separate run_show_command calls: interfaces status, interface detail, logging
        mock_node.run_commands.side_effect = [
            [MOCK_INTERFACES_STATUS],
            [{"interfaces": {}}],
            [MOCK_LOG_WITH_FLAPS],
        ]
        # Filter for Ethernet99 which doesn't appear in the flap log
        result = eos_check_interface_flaps("test-switch", interface="Ethernet99")
        assert result["status"] == "success"
        assert result["data"]["flap_count"] == 0

    def test_mixed_encoding_calls(self, mock_conn_mgr, mock_node):
        """Verify eos_check_interface_flaps makes separate JSON and text calls."""
        mock_node.run_commands.side_effect = [
            [MOCK_INTERFACES_STATUS],
            [MOCK_LOG_NO_FLAPS],
        ]
        eos_check_interface_flaps("test-switch")
        assert mock_node.run_commands.call_count == 2
        # First call should be JSON
        first_call = mock_node.run_commands.call_args_list[0]
        assert first_call.kwargs.get("encoding", "json") == "json"
        # Second call should be text
        second_call = mock_node.run_commands.call_args_list[1]
        assert second_call.kwargs.get("encoding") == "text"

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_check_interface_flaps("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = eos_check_interface_flaps("test-switch")
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        result = eos_check_interface_flaps("")
        assert result["status"] == "error"

    def test_custom_log_lines(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = [
            [MOCK_INTERFACES_STATUS],
            [MOCK_LOG_NO_FLAPS],
        ]
        eos_check_interface_flaps("test-switch", log_lines=200)
        # Verify the log command uses the custom line count
        second_call = mock_node.run_commands.call_args_list[1]
        assert "show logging last 200" in second_call.args[0]
