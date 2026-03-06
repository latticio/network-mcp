"""Tests for AI/ML fabric health tools: RoCEv2/RDMA readiness, ECN, PFC."""

import pyeapi.eapilib
import pytest

from network_mcp.tools.ai_fabric import (
    _check_ecn_configured,
    _check_lossless_queues,
    _check_pfc_enabled,
    net_ai_fabric_ecn_status,
    net_ai_fabric_health,
    net_ai_fabric_pfc_status,
)

# --- Mock responses for AI fabric tools ---

MOCK_PFC_ENABLED = {
    "pfcEnabled": True,
    "interfaces": {
        "Ethernet1": {
            "pfcEnabled": True,
            "priorities": {3: {"txPause": 100, "rxPause": 50}},
        },
        "Ethernet2": {
            "pfcEnabled": True,
            "priorities": {3: {"txPause": 200, "rxPause": 80}},
        },
    },
    "priorities": {3: {"enabled": True}},
}

MOCK_PFC_DISABLED = {
    "pfcEnabled": False,
    "interfaces": {},
}

MOCK_PFC_PARTIAL = {
    "interfaces": {
        "Ethernet1": {
            "pfcEnabled": True,
            "priorities": {3: {"txPause": 10, "rxPause": 5}},
        },
        "Ethernet2": {
            "pfcEnabled": False,
        },
    },
}

MOCK_PFC_COUNTERS = {
    "interfaces": {
        "Ethernet1": {
            "priorities": {
                3: {"txPauseFrames": 1500, "rxPauseFrames": 800},
                4: {"txPauseFrames": 0, "rxPauseFrames": 0},
            },
        },
    },
    "watchdog": {"enabled": True, "action": "errdisable", "triggers": 0},
}

MOCK_POLICY_MAP_WITH_ECN = {
    "policyMaps": {
        "RDMA-QOS": {
            "classMaps": {
                "RDMA-TRAFFIC": {
                    "ecn": {"markingEnabled": True, "threshold": 1000000},
                    "bandwidth": "50%",
                },
                "DEFAULT": {
                    "bandwidth": "50%",
                },
            },
        },
    },
}

MOCK_POLICY_MAP_NO_ECN = {
    "policyMaps": {
        "DEFAULT-QOS": {
            "classMaps": {
                "DEFAULT": {
                    "bandwidth": "100%",
                },
            },
        },
    },
}

MOCK_POLICY_MAP_EMPTY = {}

MOCK_POLICY_MAP_IFACE_ECN = {
    "interfaces": {
        "Ethernet1": {
            "input": {
                "ecn": {"enabled": True},
            },
        },
    },
    "policyMaps": {},
}

MOCK_QUEUE_COUNTERS = {
    "interfaces": {
        "Ethernet1": {
            "queues": {
                "0": {"txPackets": 1000000, "txBytes": 64000000, "drops": 0},
                "3": {"txPackets": 5000000, "txBytes": 320000000, "drops": 50},
            },
            "watermarks": {"queue3": 90},
        },
    },
}


# --- net_ai_fabric_health ---


class TestNetAiFabricHealth:
    def test_success_rocev2_ready(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PFC_ENABLED, MOCK_POLICY_MAP_WITH_ECN, MOCK_QUEUE_COUNTERS]
        result = net_ai_fabric_health("test-switch")
        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["data"]["rocev2_ready"] is True
        assert len(result["data"]["checks"]) == 3
        assert all(c["passed"] for c in result["data"]["checks"])

    def test_not_ready_pfc_disabled(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PFC_DISABLED, MOCK_POLICY_MAP_WITH_ECN, MOCK_QUEUE_COUNTERS]
        result = net_ai_fabric_health("test-switch")
        assert result["status"] == "success"
        assert result["data"]["rocev2_ready"] is False
        pfc_check = next(c for c in result["data"]["checks"] if c["check"] == "pfc_enabled")
        assert pfc_check["passed"] is False

    def test_not_ready_no_ecn(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PFC_ENABLED, MOCK_POLICY_MAP_NO_ECN, MOCK_QUEUE_COUNTERS]
        result = net_ai_fabric_health("test-switch")
        assert result["status"] == "success"
        assert result["data"]["rocev2_ready"] is False
        ecn_check = next(c for c in result["data"]["checks"] if c["check"] == "ecn_configured")
        assert ecn_check["passed"] is False

    def test_not_ready_no_lossless_queues(self, mock_conn_mgr, mock_node):
        pfc_no_priorities = {"pfcEnabled": True, "interfaces": {}}
        mock_node.run_commands.return_value = [pfc_no_priorities, MOCK_POLICY_MAP_WITH_ECN, MOCK_QUEUE_COUNTERS]
        result = net_ai_fabric_health("test-switch")
        assert result["status"] == "success"
        assert result["data"]["rocev2_ready"] is False
        lq_check = next(c for c in result["data"]["checks"] if c["check"] == "lossless_queues")
        assert lq_check["passed"] is False

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = net_ai_fabric_health("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = net_ai_fabric_health("test-switch")
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            net_ai_fabric_health("")

    def test_returns_raw_data(self, mock_conn_mgr, mock_node):
        """Verify raw PFC, policy-map, and queue data is included in response."""
        mock_node.run_commands.return_value = [MOCK_PFC_ENABLED, MOCK_POLICY_MAP_WITH_ECN, MOCK_QUEUE_COUNTERS]
        result = net_ai_fabric_health("test-switch")
        assert result["data"]["pfc"] == MOCK_PFC_ENABLED
        assert result["data"]["policy_map"] == MOCK_POLICY_MAP_WITH_ECN
        assert result["data"]["queue_counters"] == MOCK_QUEUE_COUNTERS

    def test_show_commands(self, mock_conn_mgr, mock_node):
        """Verify the correct show commands are sent."""
        mock_node.run_commands.return_value = [MOCK_PFC_ENABLED, MOCK_POLICY_MAP_WITH_ECN, MOCK_QUEUE_COUNTERS]
        net_ai_fabric_health("test-switch")
        mock_node.run_commands.assert_called_once_with(
            ["show priority-flow-control", "show policy-map interface", "show queue counters"],
            encoding="json",
        )


# --- net_ai_fabric_ecn_status ---


class TestNetAiFabricEcnStatus:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_POLICY_MAP_WITH_ECN, MOCK_QUEUE_COUNTERS]
        result = net_ai_fabric_ecn_status("test-switch")
        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["data"]["policy_map"] == MOCK_POLICY_MAP_WITH_ECN
        assert result["data"]["queue_counters"] == MOCK_QUEUE_COUNTERS

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = net_ai_fabric_ecn_status("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_show_commands(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_POLICY_MAP_WITH_ECN, MOCK_QUEUE_COUNTERS]
        net_ai_fabric_ecn_status("test-switch")
        mock_node.run_commands.assert_called_once_with(
            ["show policy-map interface", "show queue counters"],
            encoding="json",
        )

    def test_empty_response(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{}, {}]
        result = net_ai_fabric_ecn_status("test-switch")
        assert result["status"] == "success"
        assert result["data"]["policy_map"] == {}
        assert result["data"]["queue_counters"] == {}

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            net_ai_fabric_ecn_status("")


# --- net_ai_fabric_pfc_status ---


class TestNetAiFabricPfcStatus:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PFC_ENABLED, MOCK_PFC_COUNTERS]
        result = net_ai_fabric_pfc_status("test-switch")
        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["data"]["pfc_status"] == MOCK_PFC_ENABLED
        assert result["data"]["pfc_counters"] == MOCK_PFC_COUNTERS

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = net_ai_fabric_pfc_status("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = net_ai_fabric_pfc_status("test-switch")
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_show_commands(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PFC_ENABLED, MOCK_PFC_COUNTERS]
        net_ai_fabric_pfc_status("test-switch")
        mock_node.run_commands.assert_called_once_with(
            ["show priority-flow-control", "show priority-flow-control counters"],
            encoding="json",
        )

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            net_ai_fabric_pfc_status("")

    def test_timeout(self, mock_conn_mgr, mock_node_timeout):
        result = net_ai_fabric_pfc_status("test-switch")
        assert result["status"] == "error"


# --- Helper function tests ---


class TestCheckPfcEnabled:
    def test_global_pfc_enabled(self):
        assert _check_pfc_enabled({"pfcEnabled": True}) is True

    def test_global_pfc_disabled(self):
        assert _check_pfc_enabled({"pfcEnabled": False}) is False

    def test_interface_pfc_enabled(self):
        data = {"interfaces": {"Ethernet1": {"pfcEnabled": True}}}
        assert _check_pfc_enabled(data) is True

    def test_no_interfaces(self):
        assert _check_pfc_enabled({}) is False

    def test_all_interfaces_disabled(self):
        data = {"interfaces": {"Ethernet1": {"pfcEnabled": False}}}
        assert _check_pfc_enabled(data) is False


class TestCheckEcnConfigured:
    def test_ecn_in_policy_map(self):
        assert _check_ecn_configured(MOCK_POLICY_MAP_WITH_ECN) is True

    def test_no_ecn_in_policy_map(self):
        assert _check_ecn_configured(MOCK_POLICY_MAP_NO_ECN) is False

    def test_empty_policy_map(self):
        assert _check_ecn_configured(MOCK_POLICY_MAP_EMPTY) is False

    def test_ecn_on_interface(self):
        assert _check_ecn_configured(MOCK_POLICY_MAP_IFACE_ECN) is True


class TestCheckLosslessQueues:
    def test_global_priorities(self):
        data = {"priorities": {3: {"enabled": True}}}
        assert _check_lossless_queues(data) is True

    def test_interface_priorities(self):
        data = {"interfaces": {"Ethernet1": {"priorities": {3: {}}}}}
        assert _check_lossless_queues(data) is True

    def test_no_priorities(self):
        assert _check_lossless_queues({}) is False

    def test_empty_interfaces(self):
        data = {"interfaces": {}}
        assert _check_lossless_queues(data) is False
