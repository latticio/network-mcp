"""Tests for AI/ML fabric health tools: RoCEv2/RDMA readiness, ECN, PFC, GPU path validation."""

import pyeapi.eapilib
import pytest

from network_mcp.tools.ai_fabric import (
    _check_ecn_configured,
    _check_lossless_queues,
    _check_pfc_enabled,
    _parse_ecmp_paths,
    _parse_lldp_text,
    _parse_traceroute_hops,
    net_ai_fabric_ecn_status,
    net_ai_fabric_gpu_path,
    net_ai_fabric_health,
    net_ai_fabric_pfc_status,
    net_ai_fabric_rail_check,
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


# --- Mock responses for GPU fabric path tools ---

MOCK_LLDP_RAIL = {
    "lldpNeighbors": [
        {"neighborDevice": "gpu-server-01", "port": "Ethernet1", "neighborPort": "eth0"},
        {"neighborDevice": "gpu-server-02", "port": "Ethernet2", "neighborPort": "eth0"},
        {"neighborDevice": "spine-rail-01", "port": "Ethernet49", "neighborPort": "Ethernet1"},
        {"neighborDevice": "spine-rail-02", "port": "Ethernet50", "neighborPort": "Ethernet1"},
    ],
}

MOCK_LLDP_NO_SPINES = {
    "lldpNeighbors": [
        {"neighborDevice": "gpu-server-01", "port": "Ethernet1", "neighborPort": "eth0"},
        {"neighborDevice": "gpu-server-02", "port": "Ethernet2", "neighborPort": "eth0"},
    ],
}

MOCK_LLDP_NO_GPU = {
    "lldpNeighbors": [
        {"neighborDevice": "spine-rail-01", "port": "Ethernet49", "neighborPort": "Ethernet1"},
    ],
}

MOCK_ROUTES_DEFAULT = {
    "vrfs": {
        "default": {
            "routes": {
                "10.0.1.0/24": {"routeType": "connected"},
                "10.0.2.0/24": {"routeType": "bgp"},
                "10.0.3.0/24": {"routeType": "bgp"},
            },
        },
    },
}

MOCK_INTF_STATUS_RAIL = {
    "interfaceStatuses": {
        "Ethernet1": {"linkStatus": "connected", "bandwidth": 100000000000},
        "Ethernet2": {"linkStatus": "connected", "bandwidth": 100000000000},
        "Ethernet49": {"linkStatus": "connected", "bandwidth": 400000000000},
        "Ethernet50": {"linkStatus": "connected", "bandwidth": 400000000000},
    },
}

MOCK_INTF_STATUS_PORT_DOWN = {
    "interfaceStatuses": {
        "Ethernet1": {"linkStatus": "connected", "bandwidth": 100000000000},
        "Ethernet2": {"linkStatus": "notconnect", "bandwidth": 100000000000},
        "Ethernet49": {"linkStatus": "connected", "bandwidth": 400000000000},
    },
}

MOCK_TRACEROUTE_OUTPUT = {
    "output": (
        "traceroute to 10.0.2.100 (10.0.2.100), 30 hops max, 60 byte packets\n"
        " 1  10.0.1.1 (10.0.1.1)  0.512 ms  0.411 ms  0.389 ms\n"
        " 2  10.0.0.1 (10.0.0.1)  1.234 ms  1.111 ms  1.222 ms\n"
        " 3  10.0.2.100 (10.0.2.100)  2.345 ms  2.222 ms  2.111 ms\n"
    ),
}

MOCK_TRACEROUTE_WITH_STARS = {
    "output": (
        "traceroute to 10.0.2.100 (10.0.2.100), 30 hops max, 60 byte packets\n"
        " 1  10.0.1.1 (10.0.1.1)  0.512 ms  0.411 ms  0.389 ms\n"
        " 2  * * *\n"
        " 3  10.0.2.100 (10.0.2.100)  5.345 ms  5.222 ms  5.111 ms\n"
    ),
}

MOCK_LLDP_TEXT_OUTPUT = {
    "output": (
        "Port          Neighbor Device ID       Neighbor Port ID     TTL\n"
        "---------- ------------------------ ---------------------- -----\n"
        "Ethernet1     gpu-server-01            eth0                 120\n"
        "Ethernet49    spine-rail-01            Ethernet1            120\n"
    ),
}

MOCK_ROUTE_TEXT_ECMP = {
    "output": (
        "Routing entry for 10.0.2.0/24\n"
        "  Known via \"bgp\", distance 200, metric 0\n"
        "  Last update 1d2h ago\n"
        "  * via 10.0.0.1, Ethernet49\n"
        "  * via 10.0.0.2, Ethernet50\n"
    ),
}

MOCK_ROUTE_TEXT_SINGLE = {
    "output": (
        "Routing entry for 10.0.2.0/24\n"
        "  Known via \"connected\", distance 0, metric 0\n"
        "  * via 10.0.0.1, Ethernet49\n"
    ),
}


# --- net_ai_fabric_rail_check ---


class TestNetAiFabricRailCheck:
    def test_success_valid_rail_topology(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LLDP_RAIL, MOCK_ROUTES_DEFAULT, MOCK_INTF_STATUS_RAIL]
        result = net_ai_fabric_rail_check("leaf-01")
        assert result["status"] == "success"
        assert result["device"] == "leaf-01"
        assert result["data"]["rail_topology_valid"] is True
        assert result["data"]["gpu_host_count"] == 2
        assert result["data"]["spine_count"] == 2
        assert len(result["data"]["host_checks"]) == 2
        assert all(c["passed"] for c in result["data"]["host_checks"])

    def test_no_spine_uplinks(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LLDP_NO_SPINES, MOCK_ROUTES_DEFAULT, MOCK_INTF_STATUS_RAIL]
        result = net_ai_fabric_rail_check("leaf-01")
        assert result["status"] == "success"
        assert result["data"]["rail_topology_valid"] is False
        assert result["data"]["spine_count"] == 0
        for check in result["data"]["host_checks"]:
            assert check["has_spine_path"] is False
            assert check["passed"] is False

    def test_no_gpu_hosts(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LLDP_NO_GPU, MOCK_ROUTES_DEFAULT, MOCK_INTF_STATUS_RAIL]
        result = net_ai_fabric_rail_check("leaf-01")
        assert result["status"] == "success"
        assert result["data"]["rail_topology_valid"] is False
        assert result["data"]["gpu_host_count"] == 0
        assert len(result["data"]["host_checks"]) == 0

    def test_gpu_port_down(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LLDP_RAIL, MOCK_ROUTES_DEFAULT, MOCK_INTF_STATUS_PORT_DOWN]
        result = net_ai_fabric_rail_check("leaf-01")
        assert result["status"] == "success"
        assert result["data"]["rail_topology_valid"] is False
        checks = result["data"]["host_checks"]
        # gpu-server-01 on Ethernet1 is up, gpu-server-02 on Ethernet2 is down
        gpu1 = next(c for c in checks if c["gpu_host"] == "gpu-server-01")
        gpu2 = next(c for c in checks if c["gpu_host"] == "gpu-server-02")
        assert gpu1["passed"] is True
        assert gpu2["passed"] is False
        assert gpu2["port_up"] is False

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = net_ai_fabric_rail_check("leaf-01")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = net_ai_fabric_rail_check("leaf-01")
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            net_ai_fabric_rail_check("")

    def test_show_commands(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LLDP_RAIL, MOCK_ROUTES_DEFAULT, MOCK_INTF_STATUS_RAIL]
        net_ai_fabric_rail_check("leaf-01")
        mock_node.run_commands.assert_called_once_with(
            ["show lldp neighbors", "show ip route", "show interfaces status"],
            encoding="json",
        )

    def test_spine_uplinks_listed_in_checks(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LLDP_RAIL, MOCK_ROUTES_DEFAULT, MOCK_INTF_STATUS_RAIL]
        result = net_ai_fabric_rail_check("leaf-01")
        for check in result["data"]["host_checks"]:
            assert "Ethernet49" in check["spine_uplinks"]
            assert "Ethernet50" in check["spine_uplinks"]

    def test_compute_node_detected(self, mock_conn_mgr, mock_node):
        """Nodes with 'compute' in hostname are classified as GPU hosts."""
        lldp = {
            "lldpNeighbors": [
                {"neighborDevice": "compute-node-01", "port": "Ethernet1", "neighborPort": "eth0"},
                {"neighborDevice": "spine-rail-01", "port": "Ethernet49", "neighborPort": "Ethernet1"},
            ],
        }
        mock_node.run_commands.return_value = [lldp, MOCK_ROUTES_DEFAULT, MOCK_INTF_STATUS_RAIL]
        result = net_ai_fabric_rail_check("leaf-01")
        assert result["data"]["gpu_host_count"] == 1
        assert result["data"]["host_checks"][0]["gpu_host"] == "compute-node-01"


# --- net_ai_fabric_gpu_path ---


class TestNetAiFabricGpuPath:
    def test_success_full_trace(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_OUTPUT, MOCK_LLDP_TEXT_OUTPUT, MOCK_ROUTE_TEXT_ECMP]
        result = net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "10.0.2.100")
        assert result["status"] == "success"
        assert result["device"] == "leaf-01"
        assert result["data"]["source"] == "10.0.1.100"
        assert result["data"]["destination"] == "10.0.2.100"
        assert result["data"]["hop_count"] == 3
        assert len(result["data"]["hops"]) == 3
        assert result["data"]["hops"][0]["address"] == "10.0.1.1"
        assert result["data"]["hops"][2]["address"] == "10.0.2.100"

    def test_ecmp_paths_detected(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_OUTPUT, MOCK_LLDP_TEXT_OUTPUT, MOCK_ROUTE_TEXT_ECMP]
        result = net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "10.0.2.100")
        ecmp = result["data"]["ecmp_paths"]
        assert len(ecmp) == 2
        assert ecmp[0]["next_hop"] == "10.0.0.1"
        assert ecmp[0]["interface"] == "Ethernet49"
        assert ecmp[1]["next_hop"] == "10.0.0.2"
        assert ecmp[1]["interface"] == "Ethernet50"

    def test_single_path_no_ecmp(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_OUTPUT, MOCK_LLDP_TEXT_OUTPUT, MOCK_ROUTE_TEXT_SINGLE]
        result = net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "10.0.2.100")
        assert len(result["data"]["ecmp_paths"]) == 1

    def test_traceroute_with_timeouts(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_WITH_STARS, MOCK_LLDP_TEXT_OUTPUT, MOCK_ROUTE_TEXT_ECMP]
        result = net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "10.0.2.100")
        assert result["status"] == "success"
        hops = result["data"]["hops"]
        assert hops[1]["address"] == "*"
        assert hops[1]["rtt_ms"] == []

    def test_invalid_source(self, mock_conn_mgr, mock_node):
        result = net_ai_fabric_gpu_path("leaf-01", "bad;host", "10.0.2.100")
        assert result["status"] == "error"
        assert "source_gpu" in result["error"]

    def test_invalid_destination(self, mock_conn_mgr, mock_node):
        result = net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "bad|host")
        assert result["status"] == "error"
        assert "destination_gpu" in result["error"]

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "10.0.2.100")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "10.0.2.100")
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            net_ai_fabric_gpu_path("", "10.0.1.100", "10.0.2.100")

    def test_show_commands(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_OUTPUT, MOCK_LLDP_TEXT_OUTPUT, MOCK_ROUTE_TEXT_ECMP]
        net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "10.0.2.100")
        mock_node.run_commands.assert_called_once_with(
            ["traceroute 10.0.2.100 source 10.0.1.100", "show lldp neighbors", "show ip route 10.0.2.100"],
            encoding="text",
        )

    def test_raw_traceroute_included(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_TRACEROUTE_OUTPUT, MOCK_LLDP_TEXT_OUTPUT, MOCK_ROUTE_TEXT_ECMP]
        result = net_ai_fabric_gpu_path("leaf-01", "10.0.1.100", "10.0.2.100")
        assert "traceroute to 10.0.2.100" in result["data"]["traceroute_raw"]


# --- Parser helper tests ---


class TestParseTracerouteHops:
    def test_standard_traceroute(self):
        output = (
            "traceroute to 10.0.2.100, 30 hops max\n"
            " 1  10.0.1.1 (10.0.1.1)  0.5 ms  0.4 ms  0.3 ms\n"
            " 2  10.0.0.1 (10.0.0.1)  1.2 ms  1.1 ms  1.0 ms\n"
        )
        hops = _parse_traceroute_hops(output)
        assert len(hops) == 2
        assert hops[0]["hop"] == 1
        assert hops[0]["address"] == "10.0.1.1"
        assert len(hops[0]["rtt_ms"]) == 3

    def test_timeout_hops(self):
        output = " 1  * * *\n"
        hops = _parse_traceroute_hops(output)
        assert len(hops) == 1
        assert hops[0]["address"] == "*"
        assert hops[0]["rtt_ms"] == []

    def test_empty_output(self):
        assert _parse_traceroute_hops("") == []


class TestParseEcmpPaths:
    def test_multiple_paths(self):
        text = "  * via 10.0.0.1, Ethernet49\n  * via 10.0.0.2, Ethernet50\n"
        paths = _parse_ecmp_paths(text)
        assert len(paths) == 2
        assert paths[0]["next_hop"] == "10.0.0.1"
        assert paths[1]["next_hop"] == "10.0.0.2"

    def test_single_path(self):
        text = "  * via 10.0.0.1, Ethernet49\n"
        paths = _parse_ecmp_paths(text)
        assert len(paths) == 1

    def test_no_paths(self):
        text = "No route found\n"
        paths = _parse_ecmp_paths(text)
        assert len(paths) == 0


class TestParseLldpText:
    def test_parse_neighbors(self):
        text = (
            "Port          Neighbor Device ID       Neighbor Port ID     TTL\n"
            "---------- ------------------------ ---------------------- -----\n"
            "Ethernet1     gpu-server-01            eth0                 120\n"
            "Ethernet49    spine-rail-01            Ethernet1            120\n"
        )
        result = _parse_lldp_text(text)
        assert "gpu-server-01" in result
        assert result["gpu-server-01"]["port"] == "eth0"
        assert result["gpu-server-01"]["local_port"] == "Ethernet1"

    def test_empty_text(self):
        assert _parse_lldp_text("") == {}

    def test_header_only(self):
        text = "Port          Neighbor Device ID       Neighbor Port ID     TTL\n"
        result = _parse_lldp_text(text)
        assert result == {}
