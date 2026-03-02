"""Tests for day-2 operation tools (backup, maintenance window, auto-rollback)."""

from unittest.mock import patch

import pytest

from network_mcp.server import settings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


# --- eos_backup_config ---


class TestBackupConfig:
    def test_scp_destination_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        mock_node.run_commands.return_value = [{}]
        result = eos_backup_config("test-switch", "scp://user@server/path/backup.cfg")

        assert result["status"] == "success"
        assert result["action"] == "backup_config"
        assert result["data"]["destination"] == "scp://user@server/path/backup.cfg"
        mock_node.run_commands.assert_called_once_with(["copy running-config scp://user@server/path/backup.cfg"])

    def test_flash_destination_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        mock_node.run_commands.return_value = [{}]
        result = eos_backup_config("test-switch", "flash:backup-2024.cfg")

        assert result["status"] == "success"
        assert result["action"] == "backup_config"
        mock_node.run_commands.assert_called_once_with(["copy running-config flash:backup-2024.cfg"])

    def test_sftp_destination_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        mock_node.run_commands.return_value = [{}]
        result = eos_backup_config("test-switch", "sftp://user@server/backup.cfg")

        assert result["status"] == "success"
        assert result["action"] == "backup_config"

    def test_ftp_destination_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        mock_node.run_commands.return_value = [{}]
        result = eos_backup_config("test-switch", "ftp://server/backup.cfg")

        assert result["status"] == "success"
        assert result["action"] == "backup_config"

    def test_invalid_destination_no_prefix(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        result = eos_backup_config("test-switch", "http://server/backup.cfg")

        assert result["status"] == "error"
        assert "Invalid destination" in result["error"]
        mock_node.run_commands.assert_not_called()

    def test_empty_destination(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        result = eos_backup_config("test-switch", "")

        assert result["status"] == "error"
        assert "destination" in result["error"].lower() or "Destination" in result["error"]
        mock_node.run_commands.assert_not_called()

    def test_destination_with_injection_characters(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        result = eos_backup_config("test-switch", "scp://user@server/path;rm -rf /")

        assert result["status"] == "error"
        assert "invalid characters" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_destination_with_pipe_injection(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        result = eos_backup_config("test-switch", "scp://user@server/path|cat /etc/passwd")

        assert result["status"] == "error"
        assert "invalid characters" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_destination_with_backtick_injection(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        result = eos_backup_config("test-switch", "scp://user@server/`whoami`")

        assert result["status"] == "error"
        assert "invalid characters" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_backup_config

        with patch.object(settings, "net_read_only", True):
            result = eos_backup_config("test-switch", "scp://user@server/backup.cfg")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.run_commands.assert_not_called()


# --- eos_schedule_maintenance ---


class TestScheduleMaintenance:
    def test_default_rollback_timer(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        mock_node.run_commands.return_value = [{}]
        result = eos_schedule_maintenance("test-switch", ["interface Ethernet1", "description MAINT"])

        assert result["status"] == "committed_with_timer"
        assert result["action"] == "schedule_maintenance"
        assert result["data"]["rollback_minutes"] == 5
        assert result["data"]["commands"] == ["interface Ethernet1", "description MAINT"]
        # Session name starts with maint-
        assert result["data"]["session_name"].startswith("maint-")

        # Verify the session commands sent to device
        call_args = mock_node.run_commands.call_args[0][0]
        assert call_args[0].startswith("configure session maint-")
        assert call_args[1] == "interface Ethernet1"
        assert call_args[2] == "description MAINT"
        assert call_args[3] == "commit timer 5:00"

    def test_custom_rollback_timer(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        mock_node.run_commands.return_value = [{}]
        result = eos_schedule_maintenance("test-switch", ["vlan 100", "name TEST"], rollback_minutes=30)

        assert result["status"] == "committed_with_timer"
        assert result["data"]["rollback_minutes"] == 30

        call_args = mock_node.run_commands.call_args[0][0]
        assert call_args[-1] == "commit timer 30:00"

    def test_minimum_rollback_timer(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        mock_node.run_commands.return_value = [{}]
        result = eos_schedule_maintenance("test-switch", ["vlan 200"], rollback_minutes=1)

        assert result["status"] == "committed_with_timer"
        assert result["data"]["rollback_minutes"] == 1

        call_args = mock_node.run_commands.call_args[0][0]
        assert call_args[-1] == "commit timer 1:00"

    def test_maximum_rollback_timer(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        mock_node.run_commands.return_value = [{}]
        result = eos_schedule_maintenance("test-switch", ["vlan 200"], rollback_minutes=120)

        assert result["status"] == "committed_with_timer"
        assert result["data"]["rollback_minutes"] == 120

    def test_rollback_timer_too_low(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        result = eos_schedule_maintenance("test-switch", ["vlan 100"], rollback_minutes=0)

        assert result["status"] == "error"
        assert "rollback_minutes" in result["error"]
        mock_node.run_commands.assert_not_called()

    def test_rollback_timer_too_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        result = eos_schedule_maintenance("test-switch", ["vlan 100"], rollback_minutes=121)

        assert result["status"] == "error"
        assert "rollback_minutes" in result["error"]
        mock_node.run_commands.assert_not_called()

    def test_empty_commands(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        result = eos_schedule_maintenance("test-switch", [])

        assert result["status"] == "error"
        assert "commands" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_denied_command_blocked(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        result = eos_schedule_maintenance("test-switch", ["reload"])

        assert result["status"] == "error"
        assert "blocked" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_return_includes_session_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        mock_node.run_commands.return_value = [{}]
        result = eos_schedule_maintenance("test-switch", ["vlan 300"])

        assert "session_name" in result["data"]
        assert result["data"]["session_name"].startswith("maint-")

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_schedule_maintenance

        with patch.object(settings, "net_read_only", True):
            result = eos_schedule_maintenance("test-switch", ["vlan 100"])
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.run_commands.assert_not_called()


# --- eos_auto_rollback_on_failure ---


class TestAutoRollbackOnFailure:
    def test_health_check_passes_config_retained(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        # Mock sequence: checkpoint save, config apply, health check show command
        mock_node.run_commands.side_effect = [
            [{}],  # checkpoint save
            [{"output": "Vlan100   100   active   Ethernet1\n"}],  # health check
        ]
        mock_node.config.return_value = [{}]

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["vlan 100", "name TEST"],
            "show vlan brief",
            r"Vlan100.*active",
        )

        assert result["status"] == "success"
        assert result["action"] == "auto_rollback_on_failure"
        assert result["data"]["health_check_passed"] is True
        assert result["data"]["checkpoint_name"].startswith("auto-rb-")

    def test_health_check_fails_rollback_executed(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        mock_node.run_commands.side_effect = [
            [{}],  # checkpoint save
            [{"output": "Vlan100   100   suspended\n"}],  # health check (no match)
            [{}],  # checkpoint restore (rollback)
        ]
        mock_node.config.return_value = [{}]

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["vlan 100", "name TEST"],
            "show vlan brief",
            r"Vlan100.*active",
        )

        assert result["status"] == "error"
        assert result["action"] == "auto_rollback"
        assert "rolled back" in result["error"].lower()
        assert "pattern" in result["error"].lower()
        assert result["data"]["expected_pattern"] == r"Vlan100.*active"

    def test_invalid_health_check_not_show_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["vlan 100"],
            "configure terminal",
            r".*",
        )

        assert result["status"] == "error"
        assert "show command" in result["error"].lower() or "show " in result["error"]
        mock_node.run_commands.assert_not_called()
        mock_node.config.assert_not_called()

    def test_invalid_health_check_empty(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["vlan 100"],
            "",
            r".*",
        )

        assert result["status"] == "error"
        assert "show" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_invalid_regex_pattern(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["vlan 100"],
            "show vlan brief",
            r"[invalid",
        )

        assert result["status"] == "error"
        assert "regex" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_empty_commands(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        result = eos_auto_rollback_on_failure(
            "test-switch",
            [],
            "show vlan brief",
            r".*",
        )

        assert result["status"] == "error"
        assert "commands" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_denied_command_blocked(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["write erase"],
            "show version",
            r".*",
        )

        assert result["status"] == "error"
        assert "blocked" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_health_check_with_injection_blocked(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["vlan 100"],
            "show version; reload",
            r".*",
        )

        assert result["status"] == "error"
        assert "invalid characters" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        with patch.object(settings, "net_read_only", True):
            result = eos_auto_rollback_on_failure(
                "test-switch",
                ["vlan 100"],
                "show vlan brief",
                r".*",
            )
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.run_commands.assert_not_called()

    def test_checkpoint_name_in_response(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        mock_node.run_commands.side_effect = [
            [{}],  # checkpoint save
            [{"output": "Established\n"}],  # health check
        ]
        mock_node.config.return_value = [{}]

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"],
            "show ip bgp summary",
            r"Established",
        )

        assert result["status"] == "success"
        assert "checkpoint_name" in result["data"]
        assert result["data"]["checkpoint_name"].startswith("auto-rb-")

    def test_empty_regex_pattern(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.config_mgmt import eos_auto_rollback_on_failure

        result = eos_auto_rollback_on_failure(
            "test-switch",
            ["vlan 100"],
            "show vlan brief",
            "",
        )

        assert result["status"] == "error"
        assert "pattern" in result["error"].lower()
        mock_node.run_commands.assert_not_called()
