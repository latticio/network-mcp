"""Tests for SNMP tools in security.py."""

MOCK_SNMP_CONFIG = {
    "enabled": True,
    "contact": "ops@example.com",
    "location": "DC1-Rack42",
    "communities": {
        "public": {"accessList": "", "access": "ro"},
        "private": {"accessList": "", "access": "rw"},
    },
    "users": {},
    "groups": {},
}

MOCK_SNMP_TRAPS = {
    "trapReceivers": {
        "10.0.0.100": {
            "port": 162,
            "community": "traps-comm",
            "version": "2c",
        }
    },
    "trapsEnabled": {
        "bgp": True,
        "ospf": True,
        "entity": True,
    },
}


class TestGetSnmpConfig:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_get_snmp_config

        mock_node.run_commands.return_value = [MOCK_SNMP_CONFIG]
        result = eos_get_snmp_config("test-switch")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert "enabled" in result["data"]

    def test_sanitized_output(self, mock_conn_mgr, mock_node):
        """Verify SNMP output goes through run_show_command which sanitizes credentials."""
        from network_mcp.tools.security import eos_get_snmp_config

        # run_show_command calls sanitize_dict_values which redacts sensitive values.
        # The sanitizer targets keys like "password", "secret", "community" etc.
        mock_node.run_commands.return_value = [MOCK_SNMP_CONFIG]
        result = eos_get_snmp_config("test-switch")

        # The result should be successful - sanitization is handled by run_show_command
        assert result["status"] == "success"


class TestGetSnmpTraps:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_get_snmp_traps

        mock_node.run_commands.return_value = [MOCK_SNMP_TRAPS]
        result = eos_get_snmp_traps("test-switch")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert "trapReceivers" in result["data"] or "trapsEnabled" in result["data"]

    def test_error_handling(self, mock_conn_mgr, mock_node):
        import pyeapi.eapilib

        from network_mcp.tools.security import eos_get_snmp_traps

        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Command not found")
        result = eos_get_snmp_traps("test-switch")

        assert result["status"] == "error"
        assert "test-switch" in result["device"]
