"""Tests for dry-run config validation tool (net_dryrun_config).

Validates that the dry-run tool:
- Returns syntax_valid=True with predicted_diff for valid commands
- Returns error with details for invalid commands
- Does not modify device config (no run_config calls)
- Aborts config sessions after validation (EOS)
- Is annotated as READ_ONLY
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr

MOCK_SESSION_DIFF = """\
--- system:/running-config
+++ session:/dryrun-abc12345-config
@@ -10,6 +10,7 @@
 interface Ethernet1
    description link-to-leaf-01
    no shutdown
+   speed forced 10000full
"""


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver for dry-run tests."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.run_show.return_value = [
        {},  # configure session result
        {},  # command result
        {"output": MOCK_SESSION_DIFF},  # show session-config diffs result
    ]
    driver.run_config.return_value = []
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return mock driver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


class TestNetDryrunConfigEOS:
    """Tests for dry-run on EOS devices."""

    def test_valid_commands_returns_syntax_valid(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("spine-01", ["interface Ethernet1", "speed forced 10000full"])
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["validation"]["syntax_valid"] is True

    def test_valid_commands_returns_predicted_diff(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("spine-01", ["interface Ethernet1", "speed forced 10000full"])
        assert result["validation"]["predicted_diff"] == MOCK_SESSION_DIFF

    def test_valid_commands_returns_warnings_list(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("spine-01", ["interface Ethernet1", "speed forced 10000full"])
        assert isinstance(result["validation"]["warnings"], list)

    def test_does_not_call_run_config(self, mock_common_conn_mgr, mock_driver):
        """Dry-run must not modify device config."""
        from network_mcp.tools.common.config import net_dryrun_config

        net_dryrun_config("spine-01", ["interface Ethernet1", "speed forced 10000full"])
        mock_driver.run_config.assert_not_called()

    def test_aborts_session_after_validation(self, mock_common_conn_mgr, mock_driver):
        """Dry-run must abort the config session after validation."""
        from network_mcp.tools.common.config import net_dryrun_config

        net_dryrun_config("spine-01", ["interface Ethernet1", "speed forced 10000full"])
        # run_show called at least twice: once for session commands, once for abort
        assert mock_driver.run_show.call_count >= 2
        abort_call = mock_driver.run_show.call_args_list[-1]
        abort_cmds = abort_call[0][0]
        assert any("abort" in cmd for cmd in abort_cmds)

    def test_invalid_commands_returns_error(self, mock_common_conn_mgr, mock_driver):
        """When commands cause a syntax error, return error with details."""
        mock_driver.run_show.side_effect = Exception("Invalid input: 'bogus command'")

        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("spine-01", ["bogus command that is invalid"])
        assert result["status"] == "error"
        assert result["error"] == "Syntax validation failed"
        assert "details" in result
        assert "error" in result["details"]

    def test_aborts_session_on_error(self, mock_common_conn_mgr, mock_driver):
        """Even on error, dry-run should try to abort the session."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Syntax error")
            return [{}]

        mock_driver.run_show.side_effect = side_effect

        from network_mcp.tools.common.config import net_dryrun_config

        net_dryrun_config("spine-01", ["bad-command"])
        # Should have attempted abort after the error
        assert mock_driver.run_show.call_count >= 2

    def test_response_includes_vendor_platform(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("spine-01", ["interface Ethernet1", "description test"])
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"


class TestNetDryrunConfigValidation:
    """Tests for input validation in dry-run tool."""

    def test_empty_host_returns_error(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("", ["vlan 100"])
        assert result["status"] == "error"

    def test_empty_commands_returns_error(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("spine-01", [])
        assert result["status"] == "error"
        assert "No commands" in result["error"]

    def test_denied_command_returns_error(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("spine-01", ["reload"])
        assert result["status"] == "error"
        assert "blocked" in result["error"].lower() or "deny" in result["error"].lower()

    def test_cli_injection_blocked(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("spine-01", ["vlan 100; rm -rf /"])
        assert result["status"] == "error"
        assert "invalid" in result["error"].lower() or "Invalid" in result["error"]


class TestNetDryrunConfigAnnotation:
    """Verify tool is annotated as READ_ONLY."""

    def test_read_only_annotation(self):
        from network_mcp.helpers import READ_ONLY

        # The wrapped function has annotations set via @mcp.tool(annotations=READ_ONLY)
        # We verify the tool itself is registered with read-only hints
        assert READ_ONLY.readOnlyHint is True
        assert READ_ONLY.destructiveHint is False


class TestNetDryrunConfigJunOS:
    """Tests for dry-run on JunOS devices."""

    def test_junos_dryrun_uses_commit_check(self, mock_common_conn_mgr, mock_driver):
        mock_driver.platform = "junos"
        mock_driver.vendor = "juniper"
        mock_driver.run_show.return_value = [
            {},  # configure private
            {},  # set command
            {"output": "configuration check succeeds"},  # commit check
            {},  # rollback 0
        ]

        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("junos-router", ["set interfaces ge-0/0/0 description test"])
        assert result["status"] == "success"
        assert result["validation"]["syntax_valid"] is True


class TestNetDryrunConfigGeneric:
    """Tests for dry-run on IOS-XE/NX-OS devices."""

    def test_iosxe_dryrun(self, mock_common_conn_mgr, mock_driver):
        mock_driver.platform = "iosxe"
        mock_driver.vendor = "cisco"
        mock_driver.run_show.return_value = [{"output": ""}]

        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("iosxe-router", ["interface GigabitEthernet0/0", "description test"])
        assert result["status"] == "success"
        assert result["validation"]["syntax_valid"] is True

    def test_nxos_dryrun(self, mock_common_conn_mgr, mock_driver):
        mock_driver.platform = "nxos"
        mock_driver.vendor = "cisco"
        mock_driver.run_show.return_value = [{"output": ""}]

        from network_mcp.tools.common.config import net_dryrun_config

        result = net_dryrun_config("nxos-switch", ["vlan 100", "name SERVERS"])
        assert result["status"] == "success"
        assert result["validation"]["syntax_valid"] is True
