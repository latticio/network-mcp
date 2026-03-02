"""Tests for expanded command deny-list."""

from network_mcp.helpers import validate_config_commands


class TestNewDenyListEntries:
    """Verify newly added commands are blocked."""

    def test_erase_blocked(self):
        result = validate_config_commands(["erase startup-config"])
        assert result is not None
        assert "blocked by the safety deny-list" in result

    def test_copy_erase_blocked(self):
        result = validate_config_commands(["copy erase flash:"])
        assert result is not None
        assert "blocked" in result

    def test_clear_logging_blocked(self):
        result = validate_config_commands(["clear logging"])
        assert result is not None
        assert "blocked" in result

    def test_clear_counters_blocked(self):
        result = validate_config_commands(["clear counters"])
        assert result is not None
        assert "blocked" in result

    def test_no_management_api_http_commands_blocked(self):
        result = validate_config_commands(["no management api http-commands"])
        assert result is not None
        assert "blocked" in result

    def test_no_management_api_gnmi_blocked(self):
        result = validate_config_commands(["no management api gnmi"])
        assert result is not None
        assert "blocked" in result


class TestExistingDenyListStillWorks:
    """Existing deny-list entries still block correctly."""

    def test_reload_blocked(self):
        assert validate_config_commands(["reload"]) is not None

    def test_write_erase_blocked(self):
        assert validate_config_commands(["write erase"]) is not None

    def test_bash_blocked(self):
        assert validate_config_commands(["bash"]) is not None

    def test_delete_blocked(self):
        assert validate_config_commands(["delete flash:startup-config"]) is not None

    def test_configure_terminal_blocked(self):
        assert validate_config_commands(["configure terminal"]) is not None


class TestAllowedCommands:
    """Commands NOT on the deny-list pass validation."""

    def test_vlan_allowed(self):
        assert validate_config_commands(["vlan 100", "name SERVERS"]) is None

    def test_interface_config_allowed(self):
        assert validate_config_commands(["interface Ethernet1", "description uplink"]) is None

    def test_router_bgp_allowed(self):
        assert validate_config_commands(["router bgp 65000"]) is None

    def test_write_memory_allowed(self):
        """'write memory' should not be blocked (only 'write erase' is)."""
        assert validate_config_commands(["write memory"]) is None
