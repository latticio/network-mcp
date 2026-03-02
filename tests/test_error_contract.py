"""Validate the error response contract across tool modules.

Every tool that targets a specific device MUST return error dicts containing:
  - "status": "error"
  - "device": <host>
  - "error": <message>

This test calls a representative set of tools with invalid inputs to trigger
error paths and verifies the contract is satisfied.
"""

from unittest.mock import patch

import pytest

from network_mcp.tools.interfaces import eos_get_interface_detail
from network_mcp.tools.monitoring import eos_get_logging
from network_mcp.tools.routing import eos_get_bgp_summary
from network_mcp.tools.sessions import eos_create_config_session
from network_mcp.tools.switching import eos_get_mac_address_table
from network_mcp.tools.troubleshoot import eos_ping_from_device
from network_mcp.tools.vlans import eos_create_vlan, eos_get_vlan_detail
from network_mcp.tools.workflows import eos_diagnose_bgp_session

REQUIRED_ERROR_KEYS = {"status", "device", "error"}


def _assert_error_contract(result: dict, expected_host: str) -> None:
    """Assert that an error result dict has the required keys and correct device value."""
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["status"] == "error", f"Expected status='error', got {result.get('status')}"
    missing = REQUIRED_ERROR_KEYS - result.keys()
    assert not missing, f"Error response missing keys {missing}: {result}"
    assert result["device"] == expected_host, f"Expected device='{expected_host}', got '{result.get('device')}'"
    assert isinstance(result["error"], str), "Error message must be a string"
    assert len(result["error"]) > 0, "Error message must not be empty"


class TestErrorResponseContract:
    """Verify error response contract for representative tools across modules."""

    def test_interface_validation_error(self, mock_conn_mgr):
        """interfaces.py: invalid interface name includes device key."""
        result = eos_get_interface_detail("spine-01", interface="bad interface")
        _assert_error_contract(result, "spine-01")

    def test_vlan_id_validation_error(self, mock_conn_mgr):
        """vlans.py: out-of-range VLAN ID includes device key."""
        result = eos_get_vlan_detail("spine-01", vlan_id=9999)
        _assert_error_contract(result, "spine-01")

    def test_vlan_create_validation_error(self, mock_conn_mgr):
        """vlans.py: invalid VLAN ID on create includes device key."""
        result = eos_create_vlan("spine-01", vlan_id=0, name="TEST")
        _assert_error_contract(result, "spine-01")

    def test_routing_address_family_error(self, mock_conn_mgr):
        """routing.py: invalid address_family includes device key."""
        result = eos_get_bgp_summary("spine-01", address_family="invalid_af")
        _assert_error_contract(result, "spine-01")

    def test_switching_vlan_filter_error(self, mock_conn_mgr):
        """switching.py: out-of-range VLAN filter includes device key."""
        result = eos_get_mac_address_table("spine-01", vlan=9999)
        _assert_error_contract(result, "spine-01")

    def test_troubleshoot_destination_error(self, mock_conn_mgr):
        """troubleshoot.py: invalid destination includes device key."""
        result = eos_ping_from_device("spine-01", destination="bad;host")
        _assert_error_contract(result, "spine-01")

    def test_monitoring_lines_error(self, mock_conn_mgr):
        """monitoring.py: out-of-range lines includes device key."""
        result = eos_get_logging("spine-01", lines=99999)
        _assert_error_contract(result, "spine-01")

    def test_sessions_read_only_error(self, mock_conn_mgr):
        """sessions.py: read-only mode error includes device key."""
        result = eos_create_config_session("spine-01", session_name="test-session")
        _assert_error_contract(result, "spine-01")

    def test_workflow_peer_ip_error(self, mock_conn_mgr):
        """workflows.py: invalid peer IP includes device key."""
        result = eos_diagnose_bgp_session("spine-01", neighbor_ip="bad;ip")
        _assert_error_contract(result, "spine-01")


class TestErrorContractConnectionErrors:
    """Verify error contract when device is unreachable (connection errors through run_show_command)."""

    HOST = "unreachable-switch"

    def test_device_info_connection_error(self, mock_node, mock_conn_mgr):
        """device.py: connection error returns proper error contract."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError(self.HOST, "connection refused")

        from network_mcp.tools.device import eos_get_device_info

        result = eos_get_device_info(self.HOST)
        _assert_error_contract(result, self.HOST)

    def test_interfaces_connection_error(self, mock_node, mock_conn_mgr):
        """interfaces.py: connection error returns proper error contract."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError(self.HOST, "connection refused")

        from network_mcp.tools.interfaces import eos_get_interfaces_status

        result = eos_get_interfaces_status(self.HOST)
        _assert_error_contract(result, self.HOST)

    def test_monitoring_connection_error(self, mock_node, mock_conn_mgr):
        """monitoring.py: connection error returns proper error contract."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError(self.HOST, "connection refused")

        from network_mcp.tools.monitoring import eos_get_cpu_usage

        result = eos_get_cpu_usage(self.HOST)
        _assert_error_contract(result, self.HOST)

    def test_switching_connection_error(self, mock_node, mock_conn_mgr):
        """switching.py: connection error returns proper error contract."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError(self.HOST, "connection refused")

        from network_mcp.tools.switching import eos_get_mlag_status

        result = eos_get_mlag_status(self.HOST)
        _assert_error_contract(result, self.HOST)

    def test_config_mgmt_connection_error(self, mock_node, mock_conn_mgr):
        """config_mgmt.py: connection error returns proper error contract."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError(self.HOST, "connection refused")

        from network_mcp.tools.config_mgmt import eos_get_running_config

        result = eos_get_running_config(self.HOST)
        _assert_error_contract(result, self.HOST)


class TestErrorContractOptionalModules:
    """Verify error response contract for optional module not-installed errors."""

    VALID_HOST = "spine-01"

    def test_gnmi_not_installed_includes_device(self):
        """gnmi.py: GNMI_NOT_INSTALLED_ERROR includes device key."""
        with patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", False):
            from network_mcp.tools.gnmi import eos_gnmi_get

            result = eos_gnmi_get(self.VALID_HOST, paths=["/interfaces"])
            _assert_error_contract(result, self.VALID_HOST)

    @pytest.mark.anyio
    async def test_anta_not_installed_includes_device(self):
        """validation.py: ANTA_NOT_INSTALLED_ERROR includes device key."""
        with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
            from network_mcp.tools.validation import eos_validate_bgp

            result = await eos_validate_bgp(self.VALID_HOST)
            _assert_error_contract(result, self.VALID_HOST)
