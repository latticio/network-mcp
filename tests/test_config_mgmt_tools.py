"""Tests for config management tools: running/startup config, config diff, sessions, checkpoints."""

from unittest.mock import patch

import pyeapi.eapilib
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.tools.checkpoints import (
    eos_create_checkpoint,
    eos_delete_config_checkpoint,
    eos_list_checkpoints,
    eos_rollback_to_checkpoint,
    validate_checkpoint_name,
)
from network_mcp.tools.config_mgmt import (
    eos_diff_running_startup,
    eos_get_running_config,
    eos_get_running_config_section,
    eos_get_startup_config,
)
from network_mcp.tools.sessions import (
    eos_abort_config_session,
    eos_add_to_config_session,
    eos_commit_config_session,
    eos_commit_config_session_timer,
    eos_confirm_commit_timer,
    eos_create_config_session,
    eos_get_config_session_diff,
    eos_list_config_sessions,
    validate_session_name,
    validate_timer_format,
)

# --- Mock responses ---

MOCK_RUNNING_CONFIG_TEXT = {"output": "! Command: show running-config\nhostname test-switch\n!\ninterface Ethernet1\n"}
MOCK_STARTUP_CONFIG_TEXT = {"output": "! Command: show startup-config\nhostname test-switch\n"}
MOCK_CONFIG_SECTION_TEXT = {"output": "router bgp 65000\n   neighbor 10.0.0.2 remote-as 65001\n"}
MOCK_DIFF_OUTPUT = {"output": "--- startup-config\n+++ running-config\n+ ip route 0.0.0.0/0 10.0.0.1\n"}
MOCK_DIFF_EMPTY = {"output": ""}


# --- get_running_config ---


class TestGetRunningConfig:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_RUNNING_CONFIG_TEXT]
        result = eos_get_running_config("test-switch")
        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert "hostname test-switch" in result["data"]["config"]

    def test_uses_text_encoding(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_RUNNING_CONFIG_TEXT]
        eos_get_running_config("test-switch")
        mock_node.run_commands.assert_called_once_with(["show running-config"], encoding="text")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_running_config("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = eos_get_running_config("test-switch")
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        with pytest.raises(ToolError):
            eos_get_running_config("")

    def test_sanitize_default(self, mock_conn_mgr, mock_node):
        """Config output is sanitized by default."""
        mock_node.run_commands.return_value = [
            {"output": "username admin secret sha512 $6$abc123\nsnmp-server community PUBLIC ro\n"}
        ]
        result = eos_get_running_config("test-switch")
        assert result["status"] == "success"
        assert result["data"]["sanitized"] is True
        assert "$6$abc123" not in result["data"]["config"]
        assert "PUBLIC" not in result["data"]["config"]

    def test_sanitize_false(self, mock_conn_mgr, mock_node):
        """Raw config returned when sanitize=False."""
        mock_node.run_commands.return_value = [{"output": "username admin secret sha512 $6$abc123\n"}]
        result = eos_get_running_config("test-switch", sanitize=False)
        assert result["status"] == "success"
        assert result["data"]["sanitized"] is False
        assert "$6$abc123" in result["data"]["config"]


# --- get_running_config_section ---


class TestGetRunningConfigSection:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_CONFIG_SECTION_TEXT]
        result = eos_get_running_config_section("test-switch", "router bgp")
        assert result["status"] == "success"
        assert result["data"]["section"] == "router bgp"
        assert "router bgp 65000" in result["data"]["config"]

    def test_uses_text_encoding(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_CONFIG_SECTION_TEXT]
        eos_get_running_config_section("test-switch", "router bgp")
        mock_node.run_commands.assert_called_once_with(["show running-config section router bgp"], encoding="text")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_running_config_section("test-switch", "router bgp")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        with pytest.raises(ToolError):
            eos_get_running_config_section("", "router bgp")


# --- get_startup_config ---


class TestGetStartupConfig:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_STARTUP_CONFIG_TEXT]
        result = eos_get_startup_config("test-switch")
        assert result["status"] == "success"
        assert "hostname test-switch" in result["data"]["config"]

    def test_uses_text_encoding(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_STARTUP_CONFIG_TEXT]
        eos_get_startup_config("test-switch")
        mock_node.run_commands.assert_called_once_with(["show startup-config"], encoding="text")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_startup_config("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]


# --- diff_running_startup ---


class TestDiffRunningStartup:
    def test_success_with_diff(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_DIFF_OUTPUT]
        result = eos_diff_running_startup("test-switch")
        assert result["status"] == "success"
        assert result["data"]["has_unsaved_changes"] is True
        assert "ip route" in result["data"]["diff"]

    def test_success_no_diff(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_DIFF_EMPTY]
        result = eos_diff_running_startup("test-switch")
        assert result["status"] == "success"
        assert result["data"]["has_unsaved_changes"] is False
        assert result["data"]["diff"] == ""

    def test_uses_text_encoding(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_DIFF_EMPTY]
        eos_diff_running_startup("test-switch")
        mock_node.run_commands.assert_called_once_with(["show running-config diffs"], encoding="text")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_diff_running_startup("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        with pytest.raises(ToolError):
            eos_diff_running_startup("")


# --- Input Validation Helpers ---


class TestValidateSessionName:
    def test_valid_names(self):
        assert validate_session_name("mcp-1234") is None
        assert validate_session_name("my-session") is None
        assert validate_session_name("abc123") is None
        assert validate_session_name("a") is None

    def test_empty(self):
        assert validate_session_name("") is not None
        assert validate_session_name("  ") is not None

    def test_too_long(self):
        assert validate_session_name("a" * 51) is not None
        assert validate_session_name("a" * 50) is None

    def test_invalid_chars(self):
        assert validate_session_name("has spaces") is not None
        assert validate_session_name("has_underscore") is not None
        assert validate_session_name("has.dot") is not None
        assert validate_session_name("has/slash") is not None
        assert validate_session_name("has@at") is not None


class TestValidateCheckpointName:
    def test_valid(self):
        assert validate_checkpoint_name("pre-change-backup") is None

    def test_invalid(self):
        assert validate_checkpoint_name("") is not None
        assert validate_checkpoint_name("bad name") is not None


class TestValidateTimerFormat:
    def test_valid(self):
        assert validate_timer_format("00:05:00") is None
        assert validate_timer_format("01:00:00") is None
        assert validate_timer_format("00:00:30") is None

    def test_invalid(self):
        assert validate_timer_format("5:00") is not None
        assert validate_timer_format("five minutes") is not None
        assert validate_timer_format("00:05") is not None
        assert validate_timer_format("") is not None
        assert validate_timer_format("0:05:00") is not None


# --- Mock responses for config sessions ---

MOCK_SESSION_DIFF = {
    "output": ("--- system:/running-config\n+++ session:/mcp-test-session-config\n+vlan 200\n+   name SERVERS-NEW\n")
}
MOCK_SESSION_DIFF_EMPTY = {"output": ""}
MOCK_SESSIONS_LIST = {
    "sessions": {
        "mcp-test-session": {
            "state": "pending",
            "commitUser": "",
            "description": "",
        },
        "manual-session": {
            "state": "pending",
            "commitUser": "admin",
            "description": "Manual change",
        },
    },
    "maxSavedSessions": 5,
}
MOCK_SESSIONS_EMPTY = {"sessions": {}, "maxSavedSessions": 5}
MOCK_CHECKPOINTS_TEXT = {"output": "Checkpoint  Time\npre-change  Wed Feb 18 10:00:00 2026\n"}


# --- create_config_session ---


class TestCreateConfigSession:
    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_success_with_name(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{}]
        result = eos_create_config_session("spine-01", session_name="mcp-test")
        assert result["status"] == "success"
        assert result["data"]["session_name"] == "mcp-test"
        assert result["device"] == "spine-01"

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_success_auto_name(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{}]
        result = eos_create_config_session("spine-01")
        assert result["status"] == "success"
        assert result["data"]["session_name"].startswith("mcp-")

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_invalid_session_name(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_create_config_session("spine-01", session_name="bad name!")
        assert result["status"] == "error"
        assert "alphanumeric" in result["error"]

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_create_config_session("spine-01", session_name="mcp-test")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_connection_error(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_create_config_session("spine-01", session_name="mcp-test")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]


# --- get_config_session_diff ---


class TestGetConfigSessionDiff:
    def test_success_with_diff(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_SESSION_DIFF]
        result = eos_get_config_session_diff("spine-01", "mcp-test")
        assert result["status"] == "success"
        assert result["data"]["has_changes"] is True
        assert "vlan 200" in result["data"]["diff"]
        assert result["data"]["session_name"] == "mcp-test"

    def test_success_no_changes(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_SESSION_DIFF_EMPTY]
        result = eos_get_config_session_diff("spine-01", "mcp-test")
        assert result["status"] == "success"
        assert result["data"]["has_changes"] is False

    def test_invalid_session_name(self, mock_conn_mgr, mock_node):
        result = eos_get_config_session_diff("spine-01", "bad name!")
        assert result["status"] == "error"
        assert "alphanumeric" in result["error"]

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_config_session_diff("spine-01", "mcp-test")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]


# --- add_to_config_session ---


class TestAddToConfigSession:
    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_success(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.config.return_value = None
        mock_node.run_commands.return_value = [MOCK_SESSION_DIFF]
        result = eos_add_to_config_session("spine-01", "mcp-test", ["vlan 200", "name SERVERS-NEW"])
        assert result["status"] == "staged"
        assert result["data"]["commands_added"] == ["vlan 200", "name SERVERS-NEW"]
        assert "vlan 200" in result["data"]["diff"]
        mock_node.config.assert_called_once_with(["vlan 200", "name SERVERS-NEW"], session="mcp-test")

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_empty_commands(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_add_to_config_session("spine-01", "mcp-test", [])
        assert result["status"] == "error"
        assert "No commands" in result["error"]

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_denied_command(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_add_to_config_session("spine-01", "mcp-test", ["reload"])
        assert result["status"] == "error"
        assert "blocked" in result["error"]

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_invalid_session_name(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_add_to_config_session("spine-01", "bad name!", ["vlan 200"])
        assert result["status"] == "error"
        assert "alphanumeric" in result["error"]

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_add_to_config_session("spine-01", "mcp-test", ["vlan 200"])
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_command_error(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.config.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = eos_add_to_config_session("spine-01", "mcp-test", ["invalid-cmd"])
        assert result["status"] == "error"
        assert "Command error" in result["error"]


# --- commit_config_session ---


class TestCommitConfigSession:
    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_success(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = [
            [MOCK_SESSION_DIFF],  # diff before commit
            None,  # commit
        ]
        result = eos_commit_config_session("spine-01", "mcp-test")
        assert result["status"] == "success"
        assert result["action"] == "commit_config_session"
        assert "vlan 200" in result["data"]["diff"]
        # Verify commit was called
        calls = mock_node.run_commands.call_args_list
        assert calls[1][0][0] == ["configure session mcp-test", "commit"]

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_invalid_session_name(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_commit_config_session("spine-01", "bad name!")
        assert result["status"] == "error"

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_commit_config_session("spine-01", "mcp-test")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_connection_error(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_commit_config_session("spine-01", "mcp-test")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]


# --- commit_config_session_timer ---


class TestCommitConfigSessionTimer:
    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_success_default_timer(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = [
            [MOCK_SESSION_DIFF],  # diff
            None,  # commit timer
        ]
        result = eos_commit_config_session_timer("spine-01", "mcp-test")
        assert result["status"] == "committed_with_timer"
        assert result["data"]["timer"] == "00:05:00"
        calls = mock_node.run_commands.call_args_list
        assert calls[1][0][0] == ["configure session mcp-test", "commit timer 00:05:00"]

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_success_custom_timer(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = [
            [MOCK_SESSION_DIFF],
            None,
        ]
        result = eos_commit_config_session_timer("spine-01", "mcp-test", timer="00:10:00")
        assert result["status"] == "committed_with_timer"
        assert result["data"]["timer"] == "00:10:00"

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_invalid_timer_format(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_commit_config_session_timer("spine-01", "mcp-test", timer="5min")
        assert result["status"] == "error"
        assert "timer format" in result["error"]

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_commit_config_session_timer("spine-01", "mcp-test")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]


# --- confirm_commit_timer ---


class TestConfirmCommitTimer:
    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_success(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = None
        result = eos_confirm_commit_timer("spine-01", "mcp-test")
        assert result["status"] == "success"
        assert result["action"] == "confirm_commit_timer"
        mock_node.run_commands.assert_called_once_with(["configure session mcp-test", "commit"])

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_confirm_commit_timer("spine-01", "mcp-test")
        assert result["status"] == "error"

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_invalid_session_name(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_confirm_commit_timer("spine-01", "bad name!")
        assert result["status"] == "error"


# --- abort_config_session ---


class TestAbortConfigSession:
    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_success(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = None
        result = eos_abort_config_session("spine-01", "mcp-test")
        assert result["status"] == "success"
        assert result["action"] == "abort_config_session"
        mock_node.run_commands.assert_called_once_with(["configure session mcp-test", "abort"])

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_abort_config_session("spine-01", "mcp-test")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_connection_error(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_abort_config_session("spine-01", "mcp-test")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]


# --- list_config_sessions ---


class TestListConfigSessions:
    def test_success_with_sessions(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_SESSIONS_LIST]
        result = eos_list_config_sessions("spine-01")
        assert result["status"] == "success"
        assert result["data"]["count"] == 2
        assert result["data"]["max_sessions"] == 5
        names = [s["name"] for s in result["data"]["sessions"]]
        assert "mcp-test-session" in names
        assert "manual-session" in names

    def test_success_empty(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_SESSIONS_EMPTY]
        result = eos_list_config_sessions("spine-01")
        assert result["status"] == "success"
        assert result["data"]["count"] == 0

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_list_config_sessions("spine-01")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]


# --- create_checkpoint ---


class TestCreateCheckpoint:
    @patch("network_mcp.tools.checkpoints.check_read_only", return_value=None)
    def test_success(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{}]
        result = eos_create_checkpoint("spine-01", "pre-change-backup")
        assert result["status"] == "success"
        assert result["data"]["checkpoint_name"] == "pre-change-backup"
        mock_node.run_commands.assert_called_once_with(["configure checkpoint save pre-change-backup"])

    @patch("network_mcp.tools.checkpoints.check_read_only", return_value=None)
    def test_invalid_checkpoint_name(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_create_checkpoint("spine-01", "bad name!")
        assert result["status"] == "error"
        assert "alphanumeric" in result["error"]

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_create_checkpoint("spine-01", "pre-change")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    @patch("network_mcp.tools.checkpoints.check_read_only", return_value=None)
    def test_connection_error(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_create_checkpoint("spine-01", "pre-change")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]


# --- list_checkpoints ---


class TestListCheckpoints:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_CHECKPOINTS_TEXT]
        result = eos_list_checkpoints("spine-01")
        assert result["status"] == "success"
        assert "pre-change" in result["data"]["checkpoints"]

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_list_checkpoints("spine-01")
        assert result["status"] == "error"


# --- rollback_to_checkpoint ---


class TestRollbackToCheckpoint:
    @patch("network_mcp.tools.checkpoints.check_read_only", return_value=None)
    def test_success(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{}]
        result = eos_rollback_to_checkpoint("spine-01", "pre-change")
        assert result["status"] == "success"
        assert result["action"] == "rollback_to_checkpoint"
        mock_node.run_commands.assert_called_once_with(["configure checkpoint restore pre-change"])

    @patch("network_mcp.tools.checkpoints.check_read_only", return_value=None)
    def test_invalid_checkpoint_name(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_rollback_to_checkpoint("spine-01", "bad name!")
        assert result["status"] == "error"

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_rollback_to_checkpoint("spine-01", "pre-change")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    @patch("network_mcp.tools.checkpoints.check_read_only", return_value=None)
    def test_command_error(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "checkpoint not found")
        result = eos_rollback_to_checkpoint("spine-01", "nonexistent")
        assert result["status"] == "error"
        assert "command error" in result["error"].lower()


# --- delete_config_checkpoint ---


class TestDeleteConfigCheckpoint:
    @patch("network_mcp.tools.checkpoints.check_read_only", return_value=None)
    def test_success(self, _mock_ro, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{}]
        result = eos_delete_config_checkpoint("spine-01", "old-checkpoint")
        assert result["status"] == "success"
        assert result["action"] == "delete_config_checkpoint"
        mock_node.run_commands.assert_called_once_with(["delete checkpoint:old-checkpoint"])

    def test_read_only_blocked(self, mock_conn_mgr, mock_node):
        result = eos_delete_config_checkpoint("spine-01", "old-checkpoint")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    @patch("network_mcp.tools.checkpoints.check_read_only", return_value=None)
    def test_invalid_checkpoint_name(self, _mock_ro, mock_conn_mgr, mock_node):
        result = eos_delete_config_checkpoint("spine-01", "bad name!")
        assert result["status"] == "error"


# --- Multi-tool Workflow Tests ---


class TestConfigSessionWorkflow:
    """Test the complete create -> add -> diff -> commit workflow."""

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_create_add_diff_commit(self, _mock_ro, mock_conn_mgr, mock_node):
        """Full workflow: create session, add commands, review diff, commit."""
        # Step 1: Create session
        mock_node.run_commands.return_value = [{}]
        result = eos_create_config_session("spine-01", session_name="mcp-workflow-test")
        assert result["status"] == "success"

        # Step 2: Add commands
        mock_node.config.return_value = None
        mock_node.run_commands.return_value = [MOCK_SESSION_DIFF]
        result = eos_add_to_config_session("spine-01", "mcp-workflow-test", ["vlan 200", "name SERVERS-NEW"])
        assert result["status"] == "staged"
        assert "vlan 200" in result["data"]["diff"]

        # Step 3: Review diff
        mock_node.run_commands.return_value = [MOCK_SESSION_DIFF]
        result = eos_get_config_session_diff("spine-01", "mcp-workflow-test")
        assert result["status"] == "success"
        assert result["data"]["has_changes"] is True

        # Step 4: Commit
        mock_node.run_commands.side_effect = [
            [MOCK_SESSION_DIFF],  # diff before commit
            None,  # commit
        ]
        result = eos_commit_config_session("spine-01", "mcp-workflow-test")
        assert result["status"] == "success"

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_create_add_diff_abort(self, _mock_ro, mock_conn_mgr, mock_node):
        """Abort workflow: create session, add commands, review diff, abort."""
        # Step 1: Create session
        mock_node.run_commands.return_value = [{}]
        result = eos_create_config_session("spine-01", session_name="mcp-abort-test")
        assert result["status"] == "success"

        # Step 2: Add commands
        mock_node.config.return_value = None
        mock_node.run_commands.return_value = [MOCK_SESSION_DIFF]
        result = eos_add_to_config_session("spine-01", "mcp-abort-test", ["vlan 200", "name SERVERS-NEW"])
        assert result["status"] == "staged"

        # Step 3: Abort
        mock_node.run_commands.return_value = None
        result = eos_abort_config_session("spine-01", "mcp-abort-test")
        assert result["status"] == "success"
        assert result["action"] == "abort_config_session"

    @patch("network_mcp.tools.sessions.check_read_only", return_value=None)
    def test_timer_commit_then_confirm(self, _mock_ro, mock_conn_mgr, mock_node):
        """Timer workflow: commit with timer, then confirm."""
        # Step 1: Commit with timer
        mock_node.run_commands.side_effect = [
            [MOCK_SESSION_DIFF],  # diff
            None,  # commit timer
        ]
        result = eos_commit_config_session_timer("spine-01", "mcp-timer-test", timer="00:05:00")
        assert result["status"] == "committed_with_timer"
        assert result["data"]["timer"] == "00:05:00"

        # Step 2: Confirm
        mock_node.run_commands.side_effect = None
        mock_node.run_commands.return_value = None
        result = eos_confirm_commit_timer("spine-01", "mcp-timer-test")
        assert result["status"] == "success"
        assert result["action"] == "confirm_commit_timer"
