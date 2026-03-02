"""Tests for QoS tools in qos.py.

Covers:
- eos_get_policy_maps with mock data and name filter
- eos_get_class_maps with mock data and name filter
- eos_get_traffic_policy with mock data and direction filter
- No QoS configured (empty responses)
- Device unreachable (connection error, timeout)
- CLI injection prevention in name and direction params
"""

import pyeapi.eapilib

MOCK_POLICY_MAP = {
    "policyMaps": {
        "QOS-POLICY": {
            "classMaps": {
                "VOICE": {"actions": {"policer": {"rate": 1000000}}},
                "VIDEO": {"actions": {"policer": {"rate": 5000000}}},
            }
        }
    }
}

MOCK_CLASS_MAP = {
    "classMaps": {
        "VOICE": {"matchCondition": "matchAll", "matchRules": [{"field": "dscp", "value": "ef"}]},
        "VIDEO": {"matchCondition": "matchAll", "matchRules": [{"field": "dscp", "value": "af41"}]},
    }
}

MOCK_TRAFFIC_POLICY = {
    "trafficPolicies": {
        "TP-IN": {
            "direction": "input",
            "counters": {"matchedPackets": 50000, "matchedBytes": 64000000},
        }
    }
}


class TestGetPolicyMaps:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        mock_node.run_commands.return_value = [MOCK_POLICY_MAP]
        result = eos_get_policy_maps("test-switch")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert "policyMaps" in result["data"]

    def test_with_name_filter(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        mock_node.run_commands.return_value = [MOCK_POLICY_MAP]
        result = eos_get_policy_maps("test-switch", name="QOS-POLICY")

        assert result["status"] == "success"

    def test_invalid_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        result = eos_get_policy_maps("test-switch", name="bad;name")
        assert result["status"] == "error"
        assert "name" in result["error"]


class TestGetClassMaps:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        mock_node.run_commands.return_value = [MOCK_CLASS_MAP]
        result = eos_get_class_maps("test-switch")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert "classMaps" in result["data"]

    def test_with_name_filter(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        mock_node.run_commands.return_value = [MOCK_CLASS_MAP]
        result = eos_get_class_maps("test-switch", name="VOICE")

        assert result["status"] == "success"

    def test_invalid_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        result = eos_get_class_maps("test-switch", name="bad|name")
        assert result["status"] == "error"
        assert "name" in result["error"]


class TestGetTrafficPolicy:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.return_value = [MOCK_TRAFFIC_POLICY]
        result = eos_get_traffic_policy("test-switch")

        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert "trafficPolicies" in result["data"]

    def test_with_direction_input(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.return_value = [MOCK_TRAFFIC_POLICY]
        result = eos_get_traffic_policy("test-switch", direction="input")

        assert result["status"] == "success"

    def test_with_direction_output(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.return_value = [MOCK_TRAFFIC_POLICY]
        result = eos_get_traffic_policy("test-switch", direction="output")

        assert result["status"] == "success"

    def test_invalid_direction(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        result = eos_get_traffic_policy("test-switch", direction="both")
        assert result["status"] == "error"
        assert "direction" in result["error"]
        mock_node.run_commands.assert_not_called()


# ===========================================================================
# No QoS Configured (Empty Responses)
# ===========================================================================

MOCK_EMPTY_POLICY_MAP = {"policyMaps": {}}
MOCK_EMPTY_CLASS_MAP = {"classMaps": {}}
MOCK_EMPTY_TRAFFIC_POLICY = {"trafficPolicies": {}}


class TestQosEmptyResponses:
    """Tests for when no QoS is configured on the device."""

    def test_no_policy_maps_configured(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        mock_node.run_commands.return_value = [MOCK_EMPTY_POLICY_MAP]
        result = eos_get_policy_maps("test-switch")

        assert result["status"] == "success"
        assert result["data"]["policyMaps"] == {}

    def test_no_class_maps_configured(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        mock_node.run_commands.return_value = [MOCK_EMPTY_CLASS_MAP]
        result = eos_get_class_maps("test-switch")

        assert result["status"] == "success"
        assert result["data"]["classMaps"] == {}

    def test_no_traffic_policy_configured(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.return_value = [MOCK_EMPTY_TRAFFIC_POLICY]
        result = eos_get_traffic_policy("test-switch")

        assert result["status"] == "success"
        assert result["data"]["trafficPolicies"] == {}


# ===========================================================================
# Device Unreachable
# ===========================================================================


class TestQosDeviceUnreachable:
    """Tests for device connectivity errors."""

    def test_policy_maps_connection_error(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")
        result = eos_get_policy_maps("unreachable-switch")

        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_class_maps_connection_error(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")
        result = eos_get_class_maps("unreachable-switch")

        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_traffic_policy_connection_error(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")
        result = eos_get_traffic_policy("unreachable-switch")

        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_policy_maps_generic_exception(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        mock_node.run_commands.side_effect = RuntimeError("Something broke")
        result = eos_get_policy_maps("broken-switch")

        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_class_maps_generic_exception(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        mock_node.run_commands.side_effect = RuntimeError("Something broke")
        result = eos_get_class_maps("broken-switch")

        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_traffic_policy_generic_exception(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.side_effect = RuntimeError("Something broke")
        result = eos_get_traffic_policy("broken-switch")

        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_policy_maps_command_error(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")
        result = eos_get_policy_maps("test-switch")

        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_class_maps_command_error(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")
        result = eos_get_class_maps("test-switch")

        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_traffic_policy_command_error(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")
        result = eos_get_traffic_policy("test-switch")

        assert result["status"] == "error"
        assert "Command error" in result["error"]


# ===========================================================================
# CLI Injection Prevention
# ===========================================================================


class TestQosInputValidation:
    """Tests for CLI injection prevention in QoS tool parameters."""

    def test_policy_map_name_semicolon_rejected(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        result = eos_get_policy_maps("test-switch", name="POLICY; show run")
        assert result["status"] == "error"
        assert "invalid" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_policy_map_name_backtick_rejected(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        result = eos_get_policy_maps("test-switch", name="POLICY`reboot`")
        assert result["status"] == "error"
        mock_node.run_commands.assert_not_called()

    def test_policy_map_name_subshell_rejected(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        result = eos_get_policy_maps("test-switch", name="$(rm -rf /)")
        assert result["status"] == "error"
        mock_node.run_commands.assert_not_called()

    def test_class_map_name_pipe_rejected(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        result = eos_get_class_maps("test-switch", name="CLASS|include secret")
        assert result["status"] == "error"
        assert "invalid" in result["error"].lower()
        mock_node.run_commands.assert_not_called()

    def test_class_map_name_newline_rejected(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        result = eos_get_class_maps("test-switch", name="CLASS\nshow run")
        assert result["status"] == "error"
        mock_node.run_commands.assert_not_called()


# ===========================================================================
# Command Construction Verification
# ===========================================================================


class TestQosCommandConstruction:
    """Verify the correct show commands are sent to the device."""

    def test_policy_maps_all_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        mock_node.run_commands.return_value = [MOCK_EMPTY_POLICY_MAP]
        eos_get_policy_maps("test-switch")
        mock_node.run_commands.assert_called_once_with(["show policy-map"], encoding="json")

    def test_policy_maps_named_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_policy_maps

        mock_node.run_commands.return_value = [MOCK_POLICY_MAP]
        eos_get_policy_maps("test-switch", name="QOS-POLICY")
        mock_node.run_commands.assert_called_once_with(["show policy-map QOS-POLICY"], encoding="json")

    def test_class_maps_all_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        mock_node.run_commands.return_value = [MOCK_EMPTY_CLASS_MAP]
        eos_get_class_maps("test-switch")
        mock_node.run_commands.assert_called_once_with(["show class-map"], encoding="json")

    def test_class_maps_named_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_class_maps

        mock_node.run_commands.return_value = [MOCK_CLASS_MAP]
        eos_get_class_maps("test-switch", name="VOICE")
        mock_node.run_commands.assert_called_once_with(["show class-map VOICE"], encoding="json")

    def test_traffic_policy_all_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.return_value = [MOCK_EMPTY_TRAFFIC_POLICY]
        eos_get_traffic_policy("test-switch")
        mock_node.run_commands.assert_called_once_with(["show traffic-policy"], encoding="json")

    def test_traffic_policy_input_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.return_value = [MOCK_TRAFFIC_POLICY]
        eos_get_traffic_policy("test-switch", direction="input")
        mock_node.run_commands.assert_called_once_with(["show traffic-policy input"], encoding="json")

    def test_traffic_policy_output_command(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.qos import eos_get_traffic_policy

        mock_node.run_commands.return_value = [MOCK_TRAFFIC_POLICY]
        eos_get_traffic_policy("test-switch", direction="output")
        mock_node.run_commands.assert_called_once_with(["show traffic-policy output"], encoding="json")
