"""Tests for vendor-agnostic configuration management tools.

Tests net_get_running_config, net_get_running_config_section, net_get_startup_config,
net_compare_configs, net_save_config, and net_push_config. Validates response structure,
sanitization, CLI injection prevention, read-only enforcement, deny-list blocking,
multi-vendor support, and error handling.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr

# --- Mock data ---

MOCK_RUNNING_CONFIG = """\
! Command: show running-config
hostname spine-01
!
username admin privilege 15 role network-admin secret sha512 $6$abc123
!
interface Ethernet1
   description link-to-leaf-01
   no shutdown
!
router bgp 65001
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65002
   neighbor 10.0.0.2 password 7 070C31174F5B
!
management api http-commands
   protocol https
   no shutdown
!
end
"""

MOCK_STARTUP_CONFIG = """\
! Command: show running-config
hostname spine-01
!
username admin privilege 15 role network-admin secret sha512 $6$abc123
!
interface Ethernet1
   description link-to-leaf-01
   no shutdown
!
router bgp 65001
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65002
   neighbor 10.0.0.2 password 7 070C31174F5B
!
management api http-commands
   protocol https
   no shutdown
!
end
"""

MOCK_RUNNING_CONFIG_CHANGED = """\
! Command: show running-config
hostname spine-01
!
username admin privilege 15 role network-admin secret sha512 $6$abc123
!
interface Ethernet1
   description link-to-leaf-01-UPDATED
   no shutdown
!
interface Ethernet2
   description new-link
   no shutdown
!
router bgp 65001
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65002
   neighbor 10.0.0.2 password 7 070C31174F5B
!
management api http-commands
   protocol https
   no shutdown
!
end
"""

MOCK_SECTION_BGP = """\
router bgp 65001
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65002
   neighbor 10.0.0.2 password 7 070C31174F5B
"""


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with config getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_config.return_value = {
        "running": MOCK_RUNNING_CONFIG,
        "startup": MOCK_STARTUP_CONFIG,
    }
    driver.get_running_config_section.return_value = MOCK_SECTION_BGP
    driver.get_startup_config.return_value = MOCK_STARTUP_CONFIG
    driver.run_show.return_value = [{}]
    driver.run_config.return_value = []
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# ===========================================================================
# net_get_running_config
# ===========================================================================


class TestNetGetRunningConfig:
    """Test the net_get_running_config common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_data_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config("spine-01")
        assert set(result["data"].keys()) == {"config", "sanitized"}

    def test_calls_get_config(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config

        net_get_running_config("spine-01")
        mock_driver.get_config.assert_called_once_with(sanitized=False)

    def test_config_contains_hostname(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config("spine-01")
        assert "hostname spine-01" in result["data"]["config"]

    def test_sanitized_by_default(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config("spine-01")
        assert result["data"]["sanitized"] is True
        # Passwords should be redacted
        assert "$6$abc123" not in result["data"]["config"]

    def test_unsanitized_when_requested(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config

        result = net_get_running_config("spine-01", sanitize=False)
        assert result["data"]["sanitized"] is False
        # Raw config should contain passwords
        assert "$6$abc123" in result["data"]["config"]

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config

        mock_driver.get_config.side_effect = NotSupportedError("not available")
        result = net_get_running_config("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.config import net_get_running_config

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_running_config("bad-host")
        assert result["status"] == "error"

    def test_timeout_error(self):
        from network_mcp.tools.common.config import net_get_running_config

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_running_config("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.config import net_get_running_config

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_running_config("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_running_config("iosxe-router")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_running_config("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_running_config("junos-router")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"

    def test_empty_config(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config

        mock_driver.get_config.return_value = {"running": "", "startup": ""}
        result = net_get_running_config("spine-01")
        assert result["status"] == "success"
        assert result["data"]["config"] == ""


# ===========================================================================
# net_get_running_config_section
# ===========================================================================


class TestNetGetRunningConfigSection:
    """Test the net_get_running_config_section common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router bgp")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router bgp")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_data_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router bgp")
        assert set(result["data"].keys()) == {"section", "config", "sanitized"}
        assert result["data"]["section"] == "router bgp"

    def test_calls_driver_method(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        net_get_running_config_section("spine-01", "router bgp")
        mock_driver.get_running_config_section.assert_called_once_with("router bgp")

    def test_bgp_section_content(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router bgp")
        assert "router bgp 65001" in result["data"]["config"]
        assert "router-id 10.0.0.1" in result["data"]["config"]

    def test_sanitized_by_default(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router bgp")
        assert result["data"]["sanitized"] is True

    def test_unsanitized_when_requested(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router bgp", sanitize=False)
        assert result["data"]["sanitized"] is False
        assert "070C31174F5B" in result["data"]["config"]

    def test_empty_section_rejected(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "")
        assert result["status"] == "error"
        assert "cannot be empty" in result["error"]
        mock_driver.get_running_config_section.assert_not_called()

    def test_injection_semicolon(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router bgp; reload")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.get_running_config_section.assert_not_called()

    def test_injection_pipe(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router bgp | bash")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.get_running_config_section.assert_not_called()

    def test_injection_backtick(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "`rm -rf /`")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.get_running_config_section.assert_not_called()

    def test_injection_dollar_paren(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router $(whoami)")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.get_running_config_section.assert_not_called()

    def test_injection_newline(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        result = net_get_running_config_section("spine-01", "router\nreload")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.get_running_config_section.assert_not_called()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        mock_driver.get_running_config_section.side_effect = NotSupportedError("not available")
        result = net_get_running_config_section("spine-01", "router bgp")
        assert result["status"] == "not_supported"

    def test_connection_error(self):
        from network_mcp.tools.common.config import net_get_running_config_section

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_running_config_section("bad-host", "router bgp")
        assert result["status"] == "error"

    def test_interface_section(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        mock_driver.get_running_config_section.return_value = "interface Ethernet1\n   no shutdown\n"
        result = net_get_running_config_section("spine-01", "interface Ethernet1")
        assert "interface Ethernet1" in result["data"]["config"]
        assert result["data"]["section"] == "interface Ethernet1"

    def test_empty_section_result(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_running_config_section

        mock_driver.get_running_config_section.return_value = ""
        result = net_get_running_config_section("spine-01", "nonexistent")
        assert result["status"] == "success"
        assert result["data"]["config"] == ""


# ===========================================================================
# net_get_startup_config
# ===========================================================================


class TestNetGetStartupConfig:
    """Test the net_get_startup_config common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_startup_config

        result = net_get_startup_config("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_startup_config

        result = net_get_startup_config("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_data_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_startup_config

        result = net_get_startup_config("spine-01")
        assert set(result["data"].keys()) == {"config", "sanitized"}

    def test_calls_get_startup_config(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_startup_config

        net_get_startup_config("spine-01")
        mock_driver.get_startup_config.assert_called_once()

    def test_config_content(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_startup_config

        result = net_get_startup_config("spine-01")
        assert "hostname spine-01" in result["data"]["config"]

    def test_sanitized_by_default(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_startup_config

        result = net_get_startup_config("spine-01")
        assert result["data"]["sanitized"] is True
        assert "$6$abc123" not in result["data"]["config"]

    def test_unsanitized_when_requested(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_get_startup_config

        result = net_get_startup_config("spine-01", sanitize=False)
        assert result["data"]["sanitized"] is False
        assert "$6$abc123" in result["data"]["config"]

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_startup_config

        mock_driver.get_startup_config.side_effect = NotSupportedError("not available")
        result = net_get_startup_config("spine-01")
        assert result["status"] == "not_supported"

    def test_junos_startup_same_as_running(self, mock_driver):
        from network_mcp.tools.common.config import net_get_startup_config

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        junos_config = "system { host-name junos-router; }"
        mock_driver.get_startup_config.return_value = junos_config
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_startup_config("junos-router")
        assert result["status"] == "success"
        assert result["platform"] == "junos"

    def test_empty_startup_config(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_get_startup_config

        mock_driver.get_startup_config.return_value = ""
        result = net_get_startup_config("spine-01")
        assert result["status"] == "success"
        assert result["data"]["config"] == ""

    def test_connection_error(self):
        from network_mcp.tools.common.config import net_get_startup_config

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_startup_config("bad-host")
        assert result["status"] == "error"

    def test_timeout_error(self):
        from network_mcp.tools.common.config import net_get_startup_config

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_startup_config("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]


# ===========================================================================
# net_compare_configs
# ===========================================================================


class TestNetCompareConfigs:
    """Test the net_compare_configs common tool."""

    def test_success_response_no_diff(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_compare_configs

        result = net_compare_configs("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_compare_configs

        result = net_compare_configs("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_data_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_compare_configs

        result = net_compare_configs("spine-01")
        assert set(result["data"].keys()) == {"has_unsaved_changes", "diff"}

    def test_no_diff_when_same(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_compare_configs

        result = net_compare_configs("spine-01")
        assert result["data"]["has_unsaved_changes"] is False
        assert result["data"]["diff"] == ""

    def test_diff_when_changed(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_compare_configs

        mock_driver.get_config.return_value = {
            "running": MOCK_RUNNING_CONFIG_CHANGED,
            "startup": MOCK_STARTUP_CONFIG,
        }
        result = net_compare_configs("spine-01")
        assert result["data"]["has_unsaved_changes"] is True
        assert result["data"]["diff"] != ""
        assert "---" in result["data"]["diff"]
        assert "+++" in result["data"]["diff"]

    def test_diff_shows_changes(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_compare_configs

        mock_driver.get_config.return_value = {
            "running": MOCK_RUNNING_CONFIG_CHANGED,
            "startup": MOCK_STARTUP_CONFIG,
        }
        result = net_compare_configs("spine-01")
        diff = result["data"]["diff"]
        # Should contain the changed description
        assert "UPDATED" in diff or "link-to-leaf-01" in diff

    def test_diff_is_sanitized(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_compare_configs

        running_with_password = "username admin secret sha512 $6$NEWSECRET\n"
        startup_with_password = "username admin secret sha512 $6$OLDSECRET\n"
        mock_driver.get_config.return_value = {
            "running": running_with_password,
            "startup": startup_with_password,
        }
        result = net_compare_configs("spine-01")
        # Neither password should appear in the diff
        assert "$6$NEWSECRET" not in result["data"]["diff"]
        assert "$6$OLDSECRET" not in result["data"]["diff"]

    def test_diff_labels(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_compare_configs

        mock_driver.get_config.return_value = {
            "running": "line A\nline B\n",
            "startup": "line A\nline C\n",
        }
        result = net_compare_configs("spine-01")
        assert "startup-config" in result["data"]["diff"]
        assert "running-config" in result["data"]["diff"]

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_compare_configs

        mock_driver.get_config.side_effect = NotSupportedError("not available")
        result = net_compare_configs("spine-01")
        assert result["status"] == "not_supported"

    def test_connection_error(self):
        from network_mcp.tools.common.config import net_compare_configs

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_compare_configs("bad-host")
        assert result["status"] == "error"

    def test_junos_no_diff(self, mock_driver):
        from network_mcp.tools.common.config import net_compare_configs

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        junos_config = "system { host-name junos-router; }"
        mock_driver.get_config.return_value = {"running": junos_config, "startup": junos_config}
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_compare_configs("junos-router")
        assert result["data"]["has_unsaved_changes"] is False


# ===========================================================================
# net_save_config
# ===========================================================================


class TestNetSaveConfig:
    """Test the net_save_config common tool."""

    @pytest.fixture(autouse=True)
    def _allow_writes(self):
        """Ensure write mode is enabled for these tests."""
        with patch("network_mcp.tools.common.config.check_read_only", return_value=None):
            yield

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_save_config

        result = net_save_config("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["action"] == "save_config"

    def test_response_message(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_save_config

        result = net_save_config("spine-01")
        assert "saved" in result["message"].lower() or "startup" in result["message"].lower()

    def test_calls_run_show_for_save(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_save_config

        net_save_config("spine-01")
        mock_driver.run_show.assert_called_once_with(["copy running-config startup-config"])

    def test_junos_no_save_needed(self, mock_driver):
        from network_mcp.tools.common.config import net_save_config

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_save_config("junos-router")
        assert result["status"] == "success"
        assert "persists" in result["message"].lower() or "no save" in result["message"].lower()
        mock_driver.run_show.assert_not_called()

    def test_read_only_blocks_save(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_save_config

        with patch("network_mcp.tools.common.config.check_read_only", return_value="Write operations are disabled."):
            result = net_save_config("spine-01")
        assert result["status"] == "error"
        assert "disabled" in result["error"].lower() or "Write" in result["error"]

    def test_cisco_iosxe(self, mock_driver):
        from network_mcp.tools.common.config import net_save_config

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_save_config("iosxe-router")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        mock_driver.run_show.assert_called_once()

    def test_cisco_nxos(self, mock_driver):
        from network_mcp.tools.common.config import net_save_config

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_save_config("nxos-switch")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"

    def test_connection_error(self):
        from network_mcp.tools.common.config import net_save_config

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_save_config("bad-host")
        assert result["status"] == "error"

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_save_config

        mock_driver.run_show.side_effect = NotSupportedError("not available")
        result = net_save_config("spine-01")
        assert result["status"] == "not_supported"


# ===========================================================================
# net_push_config
# ===========================================================================


class TestNetPushConfig:
    """Test the net_push_config common tool."""

    @pytest.fixture(autouse=True)
    def _allow_writes(self):
        """Ensure write mode is enabled for these tests."""
        with patch("network_mcp.tools.common.config.check_read_only", return_value=None):
            yield

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["interface Ethernet1", "description test"])
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["action"] == "push_config"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["interface Ethernet1"])
        expected_keys = {"status", "device", "vendor", "platform", "action", "data", "message"}
        assert set(result.keys()) == expected_keys

    def test_data_contains_commands(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_push_config

        cmds = ["interface Ethernet1", "description test-link"]
        result = net_push_config("spine-01", cmds)
        assert result["data"]["commands"] == cmds
        assert result["data"]["commands_count"] == 2

    def test_calls_run_config(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        cmds = ["interface Ethernet1", "no shutdown"]
        net_push_config("spine-01", cmds)
        mock_driver.run_config.assert_called_once_with(cmds)

    def test_read_only_blocks_push(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_push_config

        with patch("network_mcp.tools.common.config.check_read_only", return_value="Write operations are disabled."):
            result = net_push_config("spine-01", ["interface Ethernet1"])
        assert result["status"] == "error"
        assert "disabled" in result["error"].lower() or "Write" in result["error"]

    def test_empty_commands_rejected(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", [])
        assert result["status"] == "error"
        assert "No commands" in result["error"]
        mock_driver.run_config.assert_not_called()

    def test_deny_reload(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["reload"])
        assert result["status"] == "error"
        assert "blocked" in result["error"].lower() or "deny" in result["error"].lower()
        mock_driver.run_config.assert_not_called()

    def test_deny_write_erase(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["write erase"])
        assert result["status"] == "error"
        assert "blocked" in result["error"].lower() or "deny" in result["error"].lower()
        mock_driver.run_config.assert_not_called()

    def test_deny_bash(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["bash echo hacked"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_deny_zerotouch(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["zerotouch cancel"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_deny_format(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["format flash:"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_deny_delete(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["delete flash:startup-config"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_deny_mixed_valid_and_denied(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["interface Ethernet1", "reload"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_deny_configure_terminal(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["configure terminal"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_injection_semicolon(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["interface Ethernet1; reload"])
        assert result["status"] == "error"
        assert "invalid" in result["error"].lower() or "Invalid" in result["error"]
        mock_driver.run_config.assert_not_called()

    def test_injection_pipe(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["show version | bash"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_injection_backtick(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["`rm -rf /`"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_injection_dollar_paren(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["hostname $(whoami)"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_injection_newline(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["interface Ethernet1\nreload"])
        assert result["status"] == "error"
        mock_driver.run_config.assert_not_called()

    def test_valid_vlan_commands(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        cmds = ["vlan 100", "name SERVERS"]
        result = net_push_config("spine-01", cmds)
        assert result["status"] == "success"
        mock_driver.run_config.assert_called_once_with(cmds)

    def test_valid_interface_commands(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        cmds = ["interface Ethernet1", "description uplink-to-spine", "no shutdown"]
        result = net_push_config("spine-01", cmds)
        assert result["status"] == "success"
        assert result["data"]["commands_count"] == 3

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        mock_driver.run_config.side_effect = NotSupportedError("not available")
        result = net_push_config("spine-01", ["vlan 100"])
        assert result["status"] == "not_supported"

    def test_connection_error(self):
        from network_mcp.tools.common.config import net_push_config

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_push_config("bad-host", ["vlan 100"])
        assert result["status"] == "error"

    def test_timeout_error(self):
        from network_mcp.tools.common.config import net_push_config

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_push_config("slow-host", ["vlan 100"])
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_cisco_iosxe_push(self, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_push_config("iosxe-router", ["interface GigabitEthernet1", "description uplink"])
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"

    def test_cisco_nxos_push(self, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_push_config("nxos-switch", ["vlan 200", "name PROD"])
        assert result["status"] == "success"
        assert result["platform"] == "nxos"

    def test_juniper_push(self, mock_driver):
        from network_mcp.tools.common.config import net_push_config

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_push_config("junos-router", ["set interfaces ge-0/0/0 description uplink"])
        assert result["status"] == "success"
        assert result["vendor"] == "juniper"

    def test_message_includes_command_count(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["vlan 100", "name SERVERS", "vlan 200", "name CLIENTS"])
        assert "4 command(s)" in result["message"]

    def test_single_command(self, mock_common_conn_mgr):
        from network_mcp.tools.common.config import net_push_config

        result = net_push_config("spine-01", ["hostname new-name"])
        assert result["data"]["commands_count"] == 1
        assert "1 command(s)" in result["message"]
