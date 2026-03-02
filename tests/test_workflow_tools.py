"""Tests for workflow tools: BGP diagnosis, interface troubleshooting, config comparison,
fabric health, pre/post change snapshots, interactive troubleshooting."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from network_mcp.tools.workflows import (
    TroubleshootAreaChoice,
    _make_workflow_result,
    _snapshot_store,
    eos_compare_device_configs,
    eos_diagnose_bgp_session,
    eos_interactive_troubleshoot,
    eos_post_change_validation,
    eos_pre_change_snapshot,
    eos_troubleshoot_interface_issues,
    eos_validate_fabric_health,
)
from tests.conftest import (
    MOCK_BGP_NEIGHBORS_DETAIL,
    MOCK_BGP_NEIGHBORS_DETAIL_IDLE,
    MOCK_BGP_SUMMARY,
    MOCK_BGP_SUMMARY_HEALTHY,
    MOCK_BGP_SUMMARY_WITH_DOWN_PEER,
    MOCK_INTERFACE_DETAIL,
    MOCK_INTERFACE_DETAIL_ADMIN_DOWN,
    MOCK_INTERFACE_DETAIL_DOWN,
    MOCK_INTERFACE_ERRORS,
    MOCK_INTERFACE_ERRORS_CLEAN,
    MOCK_INTERFACE_STATUS_SINGLE,
    MOCK_INTERFACES_STATUS,
    MOCK_IP_ROUTE_EMPTY,
    MOCK_IP_ROUTE_TO_PEER,
    MOCK_MLAG_ERROR_STATE,
    MOCK_MLAG_HEALTHY,
    MOCK_ROUTE_SUMMARY,
    MOCK_RUNNING_CONFIG_BGP_HOST1,
    MOCK_RUNNING_CONFIG_BGP_HOST2,
    MOCK_RUNNING_CONFIG_IDENTICAL,
    MOCK_SHOW_VERSION,
    MOCK_TRANSCEIVER_INFO,
    MOCK_TRANSCEIVER_LOW_POWER,
    MOCK_VLANS,
)

# --- WorkflowResult Structure Tests ---


class TestWorkflowResult:
    def test_make_workflow_result_structure(self):
        import time

        start = time.monotonic()
        result = _make_workflow_result(
            status="success",
            device="spine-01",
            workflow="test_workflow",
            start_time=start,
            severity="healthy",
            summary="All good",
            findings=[{"component": "test", "status": "ok"}],
            raw_data={"key": "value"},
            sub_operations=[{"command": "show version", "status": "success", "duration_ms": 50}],
        )
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["workflow"] == "test_workflow"
        assert isinstance(result["duration_ms"], int)
        assert result["duration_ms"] >= 0
        assert result["diagnosis"]["severity"] == "healthy"
        assert result["diagnosis"]["summary"] == "All good"
        assert len(result["diagnosis"]["findings"]) == 1
        assert result["raw_data"] == {"key": "value"}
        assert len(result["sub_operations"]) == 1

    def test_make_workflow_result_defaults(self):
        import time

        start = time.monotonic()
        result = _make_workflow_result(
            status="error",
            device="leaf-01",
            workflow="test",
            start_time=start,
        )
        assert result["diagnosis"]["findings"] == []
        assert result["raw_data"] == {}
        assert result["sub_operations"] == []
        assert result["diagnosis"]["severity"] == "healthy"

    def test_duration_ms_is_populated(self):
        import time

        start = time.monotonic()
        time.sleep(0.01)  # Ensure measurable duration
        result = _make_workflow_result(
            status="success",
            device="test",
            workflow="test",
            start_time=start,
        )
        assert result["duration_ms"] >= 10


# --- BGP Diagnosis Tests ---


def _mock_show_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
    """Build a mock return_value for run_show_command based on command content."""
    cmd = commands[0]
    if "show ip bgp summary" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_BGP_SUMMARY]}
    elif "show ip bgp neighbors 10.0.0.2" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_BGP_NEIGHBORS_DETAIL]}
    elif "show ip bgp neighbors 10.0.0.3" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_BGP_NEIGHBORS_DETAIL_IDLE]}
    elif "show ip route" in cmd:
        if "10.0.0.2" in cmd:
            return {"status": "success", "device": host, "data": [MOCK_IP_ROUTE_TO_PEER]}
        else:
            return {"status": "success", "device": host, "data": [MOCK_IP_ROUTE_EMPTY]}
    elif "show interfaces Ethernet1" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL]}
    elif "show interfaces Ethernet2" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL_DOWN]}
    return {"status": "error", "device": host, "error": "Unknown command"}


class TestDiagnoseBgpSession:
    @patch("network_mcp.tools.workflows.run_show_command")
    def test_peer_established_healthy(self, mock_run_show, mock_conn_mgr):
        mock_run_show.side_effect = _mock_show_return
        result = eos_diagnose_bgp_session("spine-01", "10.0.0.2")

        assert result["status"] == "success"
        assert result["workflow"] == "diagnose_bgp_session"
        assert result["device"] == "spine-01"
        assert result["diagnosis"]["severity"] == "healthy"
        assert "Established" in result["diagnosis"]["summary"]
        assert "150 prefixes" in result["diagnosis"]["summary"]
        assert result["duration_ms"] >= 0
        assert len(result["sub_operations"]) >= 3  # summary + neighbors + route

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_peer_active_state(self, mock_run_show, mock_conn_mgr):
        mock_run_show.side_effect = _mock_show_return
        result = eos_diagnose_bgp_session("spine-01", "10.0.0.3")

        assert result["status"] == "success"
        assert result["diagnosis"]["severity"] == "critical"
        assert "Active" in result["diagnosis"]["summary"]
        findings = result["diagnosis"]["findings"]
        assert len(findings) >= 1
        assert findings[0]["status"] == "Active"
        assert findings[0]["expected"] == "Established"
        assert len(findings[0]["possible_causes"]) > 0
        assert len(findings[0]["recommended_actions"]) > 0

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_peer_not_found(self, mock_run_show, mock_conn_mgr):
        mock_run_show.side_effect = _mock_show_return
        result = eos_diagnose_bgp_session("spine-01", "10.0.0.99")

        assert result["status"] == "success"
        assert result["diagnosis"]["severity"] == "critical"
        assert "not found" in result["diagnosis"]["summary"]
        findings = result["diagnosis"]["findings"]
        assert findings[0]["status"] == "not_configured"

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_peer_idle_state(self, mock_run_show, mock_conn_mgr):
        """Test Idle state with custom BGP summary showing Idle peer."""

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show ip bgp summary" in cmd:
                idle_summary = {
                    "vrfs": {
                        "default": {
                            "routerId": "10.0.0.1",
                            "asn": "65000",
                            "peers": {
                                "10.0.0.5": {
                                    "peerState": "Idle",
                                    "prefixReceived": 0,
                                    "upDownTime": 50,
                                    "asn": "65005",
                                }
                            },
                        }
                    }
                }
                return {"status": "success", "device": host, "data": [idle_summary]}
            elif "show ip bgp neighbors" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_BGP_NEIGHBORS_DETAIL_IDLE]}
            elif "show ip route" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_IP_ROUTE_EMPTY]}
            return {"status": "error", "device": host, "error": "Unknown command"}

        mock_run_show.side_effect = mock_return
        result = eos_diagnose_bgp_session("spine-01", "10.0.0.5")

        assert result["diagnosis"]["severity"] == "critical"
        assert "Idle" in result["diagnosis"]["summary"]
        findings = result["diagnosis"]["findings"]
        assert any("AS" in str(a) for a in findings[0].get("recommended_actions", []))

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_peer_established_zero_prefixes(self, mock_run_show, mock_conn_mgr):
        """Test Established peer with 0 prefixes received."""

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show ip bgp summary" in cmd:
                zero_pfx = {
                    "vrfs": {
                        "default": {
                            "routerId": "10.0.0.1",
                            "asn": "65000",
                            "peers": {
                                "10.0.0.6": {
                                    "peerState": "Established",
                                    "prefixReceived": 0,
                                    "upDownTime": 3600,
                                    "asn": "65006",
                                }
                            },
                        }
                    }
                }
                return {"status": "success", "device": host, "data": [zero_pfx]}
            elif "show ip bgp neighbors" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_BGP_NEIGHBORS_DETAIL]}
            elif "show ip route" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_IP_ROUTE_TO_PEER]}
            elif "show interfaces" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_diagnose_bgp_session("spine-01", "10.0.0.6")

        assert result["diagnosis"]["severity"] == "warning"
        assert "0 prefixes" in result["diagnosis"]["summary"]
        assert "Established" in result["diagnosis"]["summary"]

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_sub_operation_failure_produces_partial(self, mock_run_show, mock_conn_mgr):
        """Test that a failed sub-operation results in partial status."""
        call_count = 0

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            nonlocal call_count
            call_count += 1
            cmd = commands[0]
            if "show ip bgp summary" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_BGP_SUMMARY]}
            elif "show ip bgp neighbors" in cmd:
                return {"status": "error", "device": host, "error": "Command timeout"}
            elif "show ip route" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_IP_ROUTE_TO_PEER]}
            elif "show interfaces" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_diagnose_bgp_session("spine-01", "10.0.0.2")

        assert result["status"] == "partial"
        # Should still have diagnosis based on available data
        assert result["diagnosis"]["severity"] in ("healthy", "warning", "critical")
        # Check sub_operations has the error
        error_ops = [op for op in result["sub_operations"] if op["status"] == "error"]
        assert len(error_ops) >= 1

    def test_invalid_host(self):
        result = eos_diagnose_bgp_session("", "10.0.0.1")
        assert result["status"] == "error"

    def test_invalid_neighbor_ip(self):
        result = eos_diagnose_bgp_session("spine-01", "bad;ip")
        assert result["status"] == "error"


# --- Interface Troubleshooting Tests ---


class TestTroubleshootInterfaceIssues:
    @patch("network_mcp.tools.workflows.run_show_command")
    def test_interface_healthy(self, mock_run_show, mock_conn_mgr):
        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show interfaces Ethernet1 counters errors" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_ERRORS_CLEAN]}
            elif "show interfaces Ethernet1 transceiver" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_TRANSCEIVER_INFO]}
            elif "show interfaces Ethernet1 status" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_STATUS_SINGLE]}
            elif "show interfaces Ethernet1" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_troubleshoot_interface_issues("spine-01", "Ethernet1")

        assert result["status"] == "success"
        assert result["workflow"] == "troubleshoot_interface_issues"
        assert result["diagnosis"]["severity"] == "healthy"
        assert "healthy" in result["diagnosis"]["summary"]
        assert result["duration_ms"] >= 0
        assert len(result["sub_operations"]) == 4

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_interface_admin_down(self, mock_run_show, mock_conn_mgr):
        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show interfaces Ethernet1 counters errors" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_ERRORS_CLEAN]}
            elif "show interfaces Ethernet1 transceiver" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_TRANSCEIVER_INFO]}
            elif "show interfaces Ethernet1 status" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_STATUS_SINGLE]}
            elif "show interfaces Ethernet1" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL_ADMIN_DOWN]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_troubleshoot_interface_issues("spine-01", "Ethernet1")

        assert result["diagnosis"]["severity"] == "critical"
        assert "administratively disabled" in result["diagnosis"]["summary"]
        findings = result["diagnosis"]["findings"]
        assert findings[0]["status"] == "adminDown"

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_interface_link_down(self, mock_run_show, mock_conn_mgr):
        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show interfaces Ethernet1 counters errors" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_ERRORS_CLEAN]}
            elif "show interfaces Ethernet1 transceiver" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_TRANSCEIVER_LOW_POWER]}
            elif "show interfaces Ethernet1 status" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_STATUS_SINGLE]}
            elif "show interfaces Ethernet1" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL_DOWN]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_troubleshoot_interface_issues("spine-01", "Ethernet1")

        assert result["diagnosis"]["severity"] == "critical"
        assert "down" in result["diagnosis"]["summary"].lower()
        findings = result["diagnosis"]["findings"]
        assert any("Rx optical power" in str(f.get("possible_causes", [])) for f in findings)

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_interface_crc_errors(self, mock_run_show, mock_conn_mgr):
        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show interfaces Ethernet1 counters errors" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_ERRORS]}
            elif "show interfaces Ethernet1 transceiver" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_TRANSCEIVER_INFO]}
            elif "show interfaces Ethernet1 status" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_STATUS_SINGLE]}
            elif "show interfaces Ethernet1" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_troubleshoot_interface_issues("spine-01", "Ethernet1")

        assert result["diagnosis"]["severity"] == "warning"
        assert "CRC" in result["diagnosis"]["summary"] or "error" in result["diagnosis"]["summary"].lower()
        # Check that findings mention physical layer issues
        crc_findings = [
            f for f in result["diagnosis"]["findings"] if "CRC" in f.get("status", "") or "FCS" in f.get("status", "")
        ]
        assert len(crc_findings) >= 1

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_sub_operation_failure_partial(self, mock_run_show, mock_conn_mgr):
        """Test that failing sub-ops produce partial status."""

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show interfaces Ethernet1 transceiver" in cmd:
                return {"status": "error", "device": host, "error": "Not supported"}
            elif "show interfaces Ethernet1 counters errors" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_ERRORS_CLEAN]}
            elif "show interfaces Ethernet1 status" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_STATUS_SINGLE]}
            elif "show interfaces Ethernet1" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_troubleshoot_interface_issues("spine-01", "Ethernet1")

        assert result["status"] == "partial"
        assert result["diagnosis"]["severity"] == "healthy"  # Still healthy since errors are clean
        error_ops = [op for op in result["sub_operations"] if op["status"] == "error"]
        assert len(error_ops) == 1

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_all_sub_ops_fail(self, mock_run_show, mock_conn_mgr):
        mock_run_show.return_value = {"status": "error", "device": "spine-01", "error": "Connection failed"}
        result = eos_troubleshoot_interface_issues("spine-01", "Ethernet1")

        assert result["status"] == "error"
        assert result["diagnosis"]["severity"] == "critical"

    def test_invalid_host(self):
        result = eos_troubleshoot_interface_issues("", "Ethernet1")
        assert result["status"] == "error"

    def test_invalid_interface(self):
        result = eos_troubleshoot_interface_issues("spine-01", "bad-intf")
        assert result["status"] == "error"


# --- Config Comparison Tests ---


class TestCompareDeviceConfigs:
    @patch("network_mcp.tools.workflows.run_show_command")
    def test_configs_differ(self, mock_run_show, mock_conn_mgr):
        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            if host == "spine-01":
                return {"status": "success", "device": host, "data": [MOCK_RUNNING_CONFIG_BGP_HOST1]}
            elif host == "spine-02":
                return {"status": "success", "device": host, "data": [MOCK_RUNNING_CONFIG_BGP_HOST2]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_compare_device_configs("spine-01", "spine-02", sections=["router bgp"])

        assert result["status"] == "success"
        assert result["workflow"] == "compare_device_configs"
        assert result["diagnosis"]["severity"] == "warning"
        assert "additions" in result["diagnosis"]["summary"]
        assert "removals" in result["diagnosis"]["summary"]
        assert result["duration_ms"] >= 0
        # Check findings contain diff detail
        assert len(result["diagnosis"]["findings"]) >= 1
        assert result["diagnosis"]["findings"][0]["status"] != "identical"

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_configs_identical(self, mock_run_show, mock_conn_mgr):
        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            return {"status": "success", "device": host, "data": [MOCK_RUNNING_CONFIG_IDENTICAL]}

        mock_run_show.side_effect = mock_return
        result = eos_compare_device_configs("spine-01", "spine-02", sections=["router bgp"])

        assert result["status"] == "success"
        assert result["diagnosis"]["severity"] == "healthy"
        assert "identical" in result["diagnosis"]["summary"]
        assert result["diagnosis"]["findings"][0]["status"] == "identical"

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_full_config_comparison(self, mock_run_show, mock_conn_mgr):
        """Test comparison without sections parameter."""

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            if host == "spine-01":
                return {"status": "success", "device": host, "data": [MOCK_RUNNING_CONFIG_BGP_HOST1]}
            return {"status": "success", "device": host, "data": [MOCK_RUNNING_CONFIG_BGP_HOST2]}

        mock_run_show.side_effect = mock_return
        result = eos_compare_device_configs("spine-01", "spine-02")

        assert result["status"] == "success"
        # Full config with different content should show diffs
        assert result["diagnosis"]["severity"] == "warning"

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_one_host_fails_partial(self, mock_run_show, mock_conn_mgr):
        """Test partial status when one host config fetch fails."""

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            if host == "spine-01":
                return {"status": "success", "device": host, "data": [MOCK_RUNNING_CONFIG_BGP_HOST1]}
            return {"status": "error", "device": host, "error": "Connection refused"}

        mock_run_show.side_effect = mock_return
        result = eos_compare_device_configs("spine-01", "spine-02", sections=["router bgp"])

        assert result["status"] == "partial"
        # Should still produce a diff (one side has data, other doesn't)
        assert result["diagnosis"]["severity"] in ("warning", "critical")

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_both_hosts_fail(self, mock_run_show, mock_conn_mgr):
        mock_run_show.return_value = {"status": "error", "device": "test", "error": "Connection failed"}
        result = eos_compare_device_configs("spine-01", "spine-02")

        assert result["status"] == "error"
        assert result["diagnosis"]["severity"] == "critical"

    def test_invalid_host1(self):
        result = eos_compare_device_configs("", "spine-02")
        assert result["status"] == "error"

    def test_invalid_host2(self):
        result = eos_compare_device_configs("spine-01", "")
        assert result["status"] == "error"

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_sub_operations_tracked(self, mock_run_show, mock_conn_mgr):
        """Verify sub-operations are recorded for both hosts."""

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            return {"status": "success", "device": host, "data": [MOCK_RUNNING_CONFIG_IDENTICAL]}

        mock_run_show.side_effect = mock_return
        result = eos_compare_device_configs("spine-01", "spine-02", sections=["router bgp"])

        # Should have 2 sub-ops: one for each host
        assert len(result["sub_operations"]) == 2
        assert all(op["status"] == "success" for op in result["sub_operations"])
        assert all("duration_ms" in op for op in result["sub_operations"])

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_multiple_sections(self, mock_run_show, mock_conn_mgr):
        """Test comparison with multiple config sections."""

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            return {"status": "success", "device": host, "data": [MOCK_RUNNING_CONFIG_IDENTICAL]}

        mock_run_show.side_effect = mock_return
        result = eos_compare_device_configs("spine-01", "spine-02", sections=["router bgp", "interface"])

        # Should have 4 sub-ops: 2 sections × 2 hosts
        assert len(result["sub_operations"]) == 4


# --- Cross-cutting Workflow Tests ---


class TestWorkflowCrossCutting:
    @patch("network_mcp.tools.workflows.run_show_command")
    def test_bgp_workflow_result_has_all_fields(self, mock_run_show, mock_conn_mgr):
        mock_run_show.side_effect = _mock_show_return
        result = eos_diagnose_bgp_session("spine-01", "10.0.0.2")

        # Verify all WorkflowResult fields are present
        required_keys = {"status", "device", "workflow", "duration_ms", "diagnosis", "raw_data", "sub_operations"}
        assert required_keys.issubset(result.keys())
        diagnosis_keys = {"severity", "summary", "findings"}
        assert diagnosis_keys.issubset(result["diagnosis"].keys())

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_interface_workflow_result_has_all_fields(self, mock_run_show, mock_conn_mgr):
        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "counters errors" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_ERRORS_CLEAN]}
            elif "transceiver" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_TRANSCEIVER_INFO]}
            elif "status" in cmd and "counters" not in cmd and "transceiver" not in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_STATUS_SINGLE]}
            elif "show interfaces Ethernet1" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_INTERFACE_DETAIL]}
            return {"status": "error", "device": host, "error": "Unknown"}

        mock_run_show.side_effect = mock_return
        result = eos_troubleshoot_interface_issues("spine-01", "Ethernet1")

        required_keys = {"status", "device", "workflow", "duration_ms", "diagnosis", "raw_data", "sub_operations"}
        assert required_keys.issubset(result.keys())

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_config_workflow_result_has_all_fields(self, mock_run_show, mock_conn_mgr):
        mock_run_show.return_value = {"status": "success", "device": "test", "data": [MOCK_RUNNING_CONFIG_IDENTICAL]}
        result = eos_compare_device_configs("spine-01", "spine-02")

        required_keys = {"status", "device", "workflow", "duration_ms", "diagnosis", "raw_data", "sub_operations"}
        assert required_keys.issubset(result.keys())

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_cache_ttl_zero_used(self, mock_run_show, mock_conn_mgr):
        """Verify workflow tools pass cache_ttl=0 to bypass cache."""
        mock_run_show.side_effect = _mock_show_return
        eos_diagnose_bgp_session("spine-01", "10.0.0.2")

        # Check all calls used cache_ttl=0
        for call in mock_run_show.call_args_list:
            kwargs = call[1] if call[1] else {}
            # cache_ttl should be 0 in kwargs
            assert kwargs.get("cache_ttl", 0) == 0


# --- Fabric Health Validation Tests ---


def _mock_fabric_device_check(conn_mgr_ref, host, *args, **kwargs):
    """Mock per-device health check for fabric validation tests."""
    if host == "spine-01":
        return {
            "status": "success",
            "data": {
                "version": MOCK_SHOW_VERSION,
                "bgp_summary": MOCK_BGP_SUMMARY_HEALTHY,
                "mlag": MOCK_MLAG_HEALTHY,
            },
            "errors": [],
        }
    elif host == "leaf-01":
        return {
            "status": "success",
            "data": {
                "version": MOCK_SHOW_VERSION,
                "bgp_summary": MOCK_BGP_SUMMARY_HEALTHY,
                "mlag": MOCK_MLAG_HEALTHY,
            },
            "errors": [],
        }
    elif host == "leaf-02":
        return {
            "status": "success",
            "data": {
                "version": MOCK_SHOW_VERSION,
                "bgp_summary": MOCK_BGP_SUMMARY_WITH_DOWN_PEER,
                "mlag": MOCK_MLAG_ERROR_STATE,
            },
            "errors": [],
        }
    return {"status": "error", "error": "Connection timeout"}


class TestValidateFabricHealth:
    @pytest.mark.asyncio
    @patch("network_mcp.tools.workflows.execute_on_devices")
    @patch("network_mcp.tools.workflows.resolve_hosts")
    async def test_all_healthy(self, mock_resolve, mock_execute, mock_conn_mgr):
        """Test fabric health when all devices are healthy."""
        from network_mcp.executor import MultiDeviceResult

        mock_resolve.return_value = ["spine-01", "leaf-01"]

        result_obj = MultiDeviceResult()
        result_obj.add_success(
            "spine-01",
            {
                "status": "success",
                "data": {
                    "version": MOCK_SHOW_VERSION,
                    "bgp_summary": MOCK_BGP_SUMMARY_HEALTHY,
                    "mlag": MOCK_MLAG_HEALTHY,
                },
                "errors": [],
            },
        )
        result_obj.add_success(
            "leaf-01",
            {
                "status": "success",
                "data": {
                    "version": MOCK_SHOW_VERSION,
                    "bgp_summary": MOCK_BGP_SUMMARY_HEALTHY,
                    "mlag": MOCK_MLAG_HEALTHY,
                },
                "errors": [],
            },
        )

        async def fake_execute(*args, **kwargs):
            return result_obj

        mock_execute.side_effect = fake_execute

        result = await eos_validate_fabric_health("all")

        assert result["status"] == "success"
        assert result["workflow"] == "validate_fabric_health"
        assert result["diagnosis"]["severity"] == "healthy"
        assert "100%" in result["diagnosis"]["summary"]

    @pytest.mark.asyncio
    @patch("network_mcp.tools.workflows.execute_on_devices")
    @patch("network_mcp.tools.workflows.resolve_hosts")
    async def test_partial_failure(self, mock_resolve, mock_execute, mock_conn_mgr):
        """Test fabric health when some devices fail."""
        from network_mcp.executor import MultiDeviceResult

        mock_resolve.return_value = ["spine-01", "leaf-02"]

        result_obj = MultiDeviceResult()
        result_obj.add_success(
            "spine-01",
            {
                "status": "success",
                "data": {
                    "version": MOCK_SHOW_VERSION,
                    "bgp_summary": MOCK_BGP_SUMMARY_HEALTHY,
                    "mlag": MOCK_MLAG_HEALTHY,
                },
                "errors": [],
            },
        )
        result_obj.add_error("leaf-02", "Connection timeout after 30s")

        async def fake_execute(*args, **kwargs):
            return result_obj

        mock_execute.side_effect = fake_execute

        result = await eos_validate_fabric_health("all")

        assert result["status"] == "partial"
        assert result["diagnosis"]["severity"] == "critical"
        assert any(f["status"] == "unreachable" for f in result["diagnosis"]["findings"])

    @pytest.mark.asyncio
    @patch("network_mcp.tools.workflows.execute_on_devices")
    @patch("network_mcp.tools.workflows.resolve_hosts")
    async def test_all_failed(self, mock_resolve, mock_execute, mock_conn_mgr):
        """Test fabric health when all devices fail."""
        from network_mcp.executor import MultiDeviceResult

        mock_resolve.return_value = ["spine-01", "leaf-01"]

        result_obj = MultiDeviceResult()
        result_obj.add_error("spine-01", "Connection refused")
        result_obj.add_error("leaf-01", "Connection timeout")

        async def fake_execute(*args, **kwargs):
            return result_obj

        mock_execute.side_effect = fake_execute

        result = await eos_validate_fabric_health("all")

        assert result["status"] == "error"
        assert result["diagnosis"]["severity"] == "critical"

    @pytest.mark.asyncio
    @patch("network_mcp.tools.workflows.execute_on_devices")
    @patch("network_mcp.tools.workflows.resolve_hosts")
    async def test_bgp_peer_down_critical(self, mock_resolve, mock_execute, mock_conn_mgr):
        """Test that a down BGP peer triggers critical severity."""
        from network_mcp.executor import MultiDeviceResult

        mock_resolve.return_value = ["leaf-01"]

        result_obj = MultiDeviceResult()
        result_obj.add_success(
            "leaf-01",
            {
                "status": "success",
                "data": {
                    "version": MOCK_SHOW_VERSION,
                    "bgp_summary": MOCK_BGP_SUMMARY_WITH_DOWN_PEER,
                    "mlag": MOCK_MLAG_HEALTHY,
                },
                "errors": [],
            },
        )

        async def fake_execute(*args, **kwargs):
            return result_obj

        mock_execute.side_effect = fake_execute

        result = await eos_validate_fabric_health("all")

        assert result["diagnosis"]["severity"] == "critical"
        bgp_findings = [f for f in result["diagnosis"]["findings"] if "bgp_peer" in f["component"]]
        assert len(bgp_findings) >= 1

    @pytest.mark.asyncio
    @patch("network_mcp.tools.workflows.execute_on_devices")
    @patch("network_mcp.tools.workflows.resolve_hosts")
    async def test_mlag_error_critical(self, mock_resolve, mock_execute, mock_conn_mgr):
        """Test that MLAG error state triggers appropriate severity."""
        from network_mcp.executor import MultiDeviceResult

        mock_resolve.return_value = ["leaf-01"]

        result_obj = MultiDeviceResult()
        result_obj.add_success(
            "leaf-01",
            {
                "status": "success",
                "data": {
                    "version": MOCK_SHOW_VERSION,
                    "bgp_summary": MOCK_BGP_SUMMARY_HEALTHY,
                    "mlag": MOCK_MLAG_ERROR_STATE,
                },
                "errors": [],
            },
        )

        async def fake_execute(*args, **kwargs):
            return result_obj

        mock_execute.side_effect = fake_execute

        result = await eos_validate_fabric_health("all")

        mlag_findings = [f for f in result["diagnosis"]["findings"] if "mlag" in f["component"]]
        assert len(mlag_findings) >= 1

    @pytest.mark.asyncio
    @patch("network_mcp.tools.workflows.resolve_hosts")
    async def test_no_devices_found(self, mock_resolve, mock_conn_mgr):
        """Test empty inventory returns error."""
        mock_resolve.return_value = []

        result = await eos_validate_fabric_health("all")

        assert result["status"] == "error"
        assert "No devices" in result["diagnosis"]["summary"]

    @pytest.mark.asyncio
    @patch("network_mcp.tools.workflows.execute_on_devices")
    @patch("network_mcp.tools.workflows.resolve_hosts")
    async def test_result_has_all_fields(self, mock_resolve, mock_execute, mock_conn_mgr):
        """Test that fabric health result has all WorkflowResult fields."""
        from network_mcp.executor import MultiDeviceResult

        mock_resolve.return_value = ["spine-01"]

        result_obj = MultiDeviceResult()
        result_obj.add_success(
            "spine-01",
            {
                "status": "success",
                "data": {
                    "version": MOCK_SHOW_VERSION,
                    "bgp_summary": MOCK_BGP_SUMMARY_HEALTHY,
                    "mlag": MOCK_MLAG_HEALTHY,
                },
                "errors": [],
            },
        )

        async def fake_execute(*args, **kwargs):
            return result_obj

        mock_execute.side_effect = fake_execute

        result = await eos_validate_fabric_health("all")

        required_keys = {"status", "device", "workflow", "duration_ms", "diagnosis", "raw_data", "sub_operations"}
        assert required_keys.issubset(result.keys())


# --- Pre-Change Snapshot Tests ---


def _mock_snapshot_show_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
    """Mock show command returns for snapshot commands."""
    cmd = commands[0]
    if "show version" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_SHOW_VERSION]}
    elif "show ip bgp summary" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_BGP_SUMMARY_HEALTHY]}
    elif "show interfaces status" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_INTERFACES_STATUS]}
    elif "show mlag" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_MLAG_HEALTHY]}
    elif "show ip route summary" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_ROUTE_SUMMARY]}
    elif "show vlan" in cmd:
        return {"status": "success", "device": host, "data": [MOCK_VLANS]}
    return {"status": "error", "device": host, "error": "Unknown command"}


class TestPreChangeSnapshot:
    def setup_method(self):
        """Clear snapshot store before each test."""
        _snapshot_store.clear()

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_creates_snapshot(self, mock_run_show, mock_conn_mgr):
        """Test that a snapshot is created and returns valid ID."""
        mock_run_show.side_effect = _mock_snapshot_show_return

        result = eos_pre_change_snapshot("spine-01")

        assert result["status"] == "success"
        assert result["workflow"] == "pre_change_snapshot"
        assert result["diagnosis"]["severity"] == "healthy"
        assert "snapshot_id" in str(result["diagnosis"]["findings"])
        assert result["raw_data"]["snapshot_id"] is not None
        assert len(result["raw_data"]["components"]) == 6
        assert result["duration_ms"] >= 0

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_snapshot_id_retrievable(self, mock_run_show, mock_conn_mgr):
        """Test that snapshot can be retrieved from store."""
        mock_run_show.side_effect = _mock_snapshot_show_return

        result = eos_pre_change_snapshot("spine-01")
        snapshot_id = result["raw_data"]["snapshot_id"]

        stored = _snapshot_store.get(snapshot_id)
        assert stored is not None
        assert stored["host"] == "spine-01"
        assert "version" in stored["data"]
        assert "bgp_summary" in stored["data"]

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_partial_when_some_commands_fail(self, mock_run_show, mock_conn_mgr):
        """Test partial status when some snapshot commands fail."""

        def mock_return(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show mlag" in cmd:
                return {"status": "error", "device": host, "error": "Not supported"}
            return _mock_snapshot_show_return(_conn_mgr, host, commands, encoding, cache_ttl)

        mock_run_show.side_effect = mock_return

        result = eos_pre_change_snapshot("spine-01")

        assert result["status"] == "partial"
        assert len(result["raw_data"]["components"]) == 5  # 6 - 1 failed

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_all_commands_fail(self, mock_run_show, mock_conn_mgr):
        """Test error status when all commands fail."""
        mock_run_show.return_value = {"status": "error", "device": "spine-01", "error": "Connection failed"}

        result = eos_pre_change_snapshot("spine-01")

        assert result["status"] == "error"
        assert result["diagnosis"]["severity"] == "critical"

    def test_invalid_host(self):
        result = eos_pre_change_snapshot("")
        assert result["status"] == "error"

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_snapshot_expiry(self, mock_run_show, mock_conn_mgr):
        """Test that snapshots expire after TTL."""
        mock_run_show.side_effect = _mock_snapshot_show_return

        result = eos_pre_change_snapshot("spine-01")
        snapshot_id = result["raw_data"]["snapshot_id"]

        # Force expire by manipulating _created_at
        snap = _snapshot_store._snapshots[snapshot_id]
        snap["_created_at"] = time.time() - 7200  # 2 hours ago (past default 3600s TTL)

        stored = _snapshot_store.get(snapshot_id)
        assert stored is None

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_lru_eviction(self, mock_run_show, mock_conn_mgr):
        """Test that LRU eviction works when max snapshots exceeded."""
        mock_run_show.side_effect = _mock_snapshot_show_return

        # Store MAX_SNAPSHOTS + 1 snapshots
        original_max = _snapshot_store.MAX_SNAPSHOTS
        _snapshot_store.MAX_SNAPSHOTS = 3
        try:
            ids = []
            for _ in range(4):
                result = eos_pre_change_snapshot("spine-01")
                ids.append(result["raw_data"]["snapshot_id"])

            # First snapshot should have been evicted
            assert _snapshot_store.get(ids[0]) is None
            # Last snapshot should exist
            assert _snapshot_store.get(ids[-1]) is not None
        finally:
            _snapshot_store.MAX_SNAPSHOTS = original_max

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_file_persistence(self, mock_run_show, mock_conn_mgr, tmp_path):
        """Test file-backed persistence when NET_SNAPSHOT_DIR is set."""
        mock_run_show.side_effect = _mock_snapshot_show_return

        with patch.object(_snapshot_store, "_get_settings") as mock_settings:
            settings = MagicMock()
            settings.net_snapshot_ttl = 3600
            settings.net_snapshot_dir = str(tmp_path)
            mock_settings.return_value = settings

            result = eos_pre_change_snapshot("spine-01")
            snapshot_id = result["raw_data"]["snapshot_id"]

            # Check file was written
            filepath = tmp_path / f"{snapshot_id}.json"
            assert filepath.exists()

            # Clear in-memory store
            _snapshot_store._snapshots.clear()

            # Should load from file
            stored = _snapshot_store.get(snapshot_id)
            assert stored is not None
            assert stored["host"] == "spine-01"


# --- Post-Change Validation Tests ---


class TestPostChangeValidation:
    def setup_method(self):
        """Clear snapshot store before each test."""
        _snapshot_store.clear()

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_no_changes_detected(self, mock_run_show, mock_conn_mgr):
        """Test when pre and post state are identical."""
        mock_run_show.side_effect = _mock_snapshot_show_return

        # Create snapshot
        snap_result = eos_pre_change_snapshot("spine-01")
        snapshot_id = snap_result["raw_data"]["snapshot_id"]

        # Run same data for post-change
        result = eos_post_change_validation("spine-01", snapshot_id)

        assert result["status"] == "success"
        assert result["workflow"] == "post_change_validation"
        assert result["diagnosis"]["severity"] == "healthy"
        assert "No changes" in result["diagnosis"]["summary"]

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_detects_bgp_regression(self, mock_run_show, mock_conn_mgr):
        """Test detection of BGP peer regression (was up, now down)."""
        mock_run_show.side_effect = _mock_snapshot_show_return

        # Create snapshot with healthy BGP
        snap_result = eos_pre_change_snapshot("spine-01")
        snapshot_id = snap_result["raw_data"]["snapshot_id"]

        # Post-change: one peer goes down
        def post_mock(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show ip bgp summary" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_BGP_SUMMARY_WITH_DOWN_PEER]}
            return _mock_snapshot_show_return(_conn_mgr, host, commands, encoding, cache_ttl)

        mock_run_show.side_effect = post_mock

        result = eos_post_change_validation("spine-01", snapshot_id)

        assert result["diagnosis"]["severity"] == "critical"
        regression_findings = [f for f in result["diagnosis"]["findings"] if "REGRESSION" in f["status"]]
        assert len(regression_findings) >= 1
        assert result["raw_data"]["regressions"]

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_detects_improvement(self, mock_run_show, mock_conn_mgr):
        """Test detection of improvements (was down, now up)."""

        # Create snapshot with a down peer
        def pre_mock(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show ip bgp summary" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_BGP_SUMMARY_WITH_DOWN_PEER]}
            return _mock_snapshot_show_return(_conn_mgr, host, commands, encoding, cache_ttl)

        mock_run_show.side_effect = pre_mock
        snap_result = eos_pre_change_snapshot("spine-01")
        snapshot_id = snap_result["raw_data"]["snapshot_id"]

        # Post-change: peer comes up
        mock_run_show.side_effect = _mock_snapshot_show_return

        result = eos_post_change_validation("spine-01", snapshot_id)

        improvement_findings = [f for f in result["diagnosis"]["findings"] if "IMPROVED" in f["status"]]
        assert len(improvement_findings) >= 1
        assert result["raw_data"]["improvements"]

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_detects_interface_regression(self, mock_run_show, mock_conn_mgr):
        """Test detection of interface going down."""
        mock_run_show.side_effect = _mock_snapshot_show_return
        snap_result = eos_pre_change_snapshot("spine-01")
        snapshot_id = snap_result["raw_data"]["snapshot_id"]

        # Post-change: interface goes down
        post_intfs = {
            "interfaceStatuses": {
                "Ethernet1": {
                    "linkStatus": "notconnect",
                    "vlanInformation": {"interfaceMode": "routed"},
                    "bandwidth": 1000000000,
                    "interfaceType": "1000BASE-T",
                    "description": "to-spine-01",
                },
                "Ethernet2": {
                    "linkStatus": "notconnect",
                    "vlanInformation": {"interfaceMode": "access", "vlanId": 100},
                    "bandwidth": 1000000000,
                    "interfaceType": "1000BASE-T",
                    "description": "",
                },
            }
        }

        def post_mock(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show interfaces status" in cmd:
                return {"status": "success", "device": host, "data": [post_intfs]}
            return _mock_snapshot_show_return(_conn_mgr, host, commands, encoding, cache_ttl)

        mock_run_show.side_effect = post_mock

        result = eos_post_change_validation("spine-01", snapshot_id)

        # Ethernet1 went from connected to notconnect = regression
        regression_findings = [
            f for f in result["diagnosis"]["findings"] if "REGRESSION" in f["status"] and "interface" in f["component"]
        ]
        assert len(regression_findings) >= 1

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_detects_mlag_regression(self, mock_run_show, mock_conn_mgr):
        """Test detection of MLAG config-sanity regression."""
        mock_run_show.side_effect = _mock_snapshot_show_return
        snap_result = eos_pre_change_snapshot("spine-01")
        snapshot_id = snap_result["raw_data"]["snapshot_id"]

        # Post-change: MLAG config-sanity goes inconsistent
        def post_mock(_conn_mgr, host, commands, encoding="json", cache_ttl=0):
            cmd = commands[0]
            if "show mlag" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_MLAG_ERROR_STATE]}
            return _mock_snapshot_show_return(_conn_mgr, host, commands, encoding, cache_ttl)

        mock_run_show.side_effect = post_mock

        result = eos_post_change_validation("spine-01", snapshot_id)

        mlag_findings = [f for f in result["diagnosis"]["findings"] if "mlag" in f["component"]]
        assert len(mlag_findings) >= 1

    def test_snapshot_not_found(self, mock_conn_mgr):
        """Test clear error when snapshot_id doesn't exist."""
        result = eos_post_change_validation("spine-01", "00000000-0000-0000-0000-000000000000")

        assert result["status"] == "error"
        assert "not found" in result["error"]
        assert "NET_SNAPSHOT_TTL" in result["error"]

    def test_invalid_host(self):
        result = eos_post_change_validation("", "some-id")
        assert result["status"] == "error"

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_host_mismatch(self, mock_run_show, mock_conn_mgr):
        """Test error when snapshot host doesn't match request host."""
        mock_run_show.side_effect = _mock_snapshot_show_return
        snap_result = eos_pre_change_snapshot("spine-01")
        snapshot_id = snap_result["raw_data"]["snapshot_id"]

        result = eos_post_change_validation("leaf-01", snapshot_id)

        assert result["status"] == "error"
        assert "spine-01" in result["error"]

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_result_has_all_fields(self, mock_run_show, mock_conn_mgr):
        """Test WorkflowResult structure."""
        mock_run_show.side_effect = _mock_snapshot_show_return
        snap_result = eos_pre_change_snapshot("spine-01")
        snapshot_id = snap_result["raw_data"]["snapshot_id"]

        result = eos_post_change_validation("spine-01", snapshot_id)

        required_keys = {"status", "device", "workflow", "duration_ms", "diagnosis", "raw_data", "sub_operations"}
        assert required_keys.issubset(result.keys())
        assert "changes" in result["raw_data"]
        assert "regressions" in result["raw_data"]
        assert "improvements" in result["raw_data"]

    @patch("network_mcp.tools.workflows.run_show_command")
    def test_post_change_all_commands_fail(self, mock_run_show, mock_conn_mgr):
        """Test error when post-change data collection fails completely."""
        mock_run_show.side_effect = _mock_snapshot_show_return
        snap_result = eos_pre_change_snapshot("spine-01")
        snapshot_id = snap_result["raw_data"]["snapshot_id"]

        # Must clear side_effect and set return_value, or set side_effect to new function
        mock_run_show.side_effect = None
        mock_run_show.return_value = {"status": "error", "device": "spine-01", "error": "Connection lost"}

        result = eos_post_change_validation("spine-01", snapshot_id)

        assert result["status"] == "error"
        assert result["diagnosis"]["severity"] == "critical"


# --- Interactive Troubleshoot Tests ---


class TestInteractiveTroubleshoot:
    """Tests for eos_interactive_troubleshoot with elicitation."""

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_no_context_falls_back_to_health(self, mock_run_show):
        """Without ctx, tool defaults to health area."""
        mock_run_show.return_value = {"status": "success", "device": "spine-01", "data": [MOCK_SHOW_VERSION]}

        result = await eos_interactive_troubleshoot("spine-01", ctx=None)

        assert result["status"] == "success"
        assert result["workflow"] == "interactive_troubleshoot"
        assert result["raw_data"]["area"] == "health"
        assert result["raw_data"]["elicitation_status"] == "skipped"

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_elicitation_accepted_bgp(self, mock_run_show):
        """User selects BGP via elicitation."""
        mock_run_show.return_value = {"status": "success", "device": "spine-01", "data": [MOCK_SHOW_VERSION]}

        # Mock ctx with elicit
        mock_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "accept"
        mock_result.data = TroubleshootAreaChoice(area="bgp")
        mock_ctx.elicit = AsyncMock(return_value=mock_result)

        result = await eos_interactive_troubleshoot("spine-01", ctx=mock_ctx)

        assert result["status"] == "success"
        assert result["raw_data"]["area"] == "bgp"
        assert result["raw_data"]["elicitation_status"] == "accepted"
        mock_ctx.elicit.assert_called_once()

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_elicitation_accepted_interfaces(self, mock_run_show):
        """User selects interfaces via elicitation."""
        mock_run_show.return_value = {"status": "success", "device": "spine-01", "data": [MOCK_SHOW_VERSION]}

        mock_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "accept"
        mock_result.data = TroubleshootAreaChoice(area="interfaces")
        mock_ctx.elicit = AsyncMock(return_value=mock_result)

        result = await eos_interactive_troubleshoot("spine-01", ctx=mock_ctx)

        assert result["raw_data"]["area"] == "interfaces"

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_elicitation_cancelled(self, mock_run_show):
        """User cancels elicitation — defaults to health."""
        mock_run_show.return_value = {"status": "success", "device": "spine-01", "data": [MOCK_SHOW_VERSION]}

        mock_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "cancel"
        mock_ctx.elicit = AsyncMock(return_value=mock_result)

        result = await eos_interactive_troubleshoot("spine-01", ctx=mock_ctx)

        assert result["raw_data"]["area"] == "health"
        assert result["raw_data"]["elicitation_status"] == "cancelled"

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_elicitation_declined(self, mock_run_show):
        """User declines elicitation — defaults to health."""
        mock_run_show.return_value = {"status": "success", "device": "spine-01", "data": [MOCK_SHOW_VERSION]}

        mock_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "decline"
        mock_ctx.elicit = AsyncMock(return_value=mock_result)

        result = await eos_interactive_troubleshoot("spine-01", ctx=mock_ctx)

        assert result["raw_data"]["area"] == "health"
        assert result["raw_data"]["elicitation_status"] == "declined"

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_elicitation_error_falls_back(self, mock_run_show):
        """Elicitation raises exception — falls back to health."""
        mock_run_show.return_value = {"status": "success", "device": "spine-01", "data": [MOCK_SHOW_VERSION]}

        mock_ctx = MagicMock()
        mock_ctx.elicit = AsyncMock(side_effect=RuntimeError("elicitation not supported"))

        result = await eos_interactive_troubleshoot("spine-01", ctx=mock_ctx)

        assert result["raw_data"]["area"] == "health"
        assert "error" in result["raw_data"]["elicitation_status"]

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_invalid_area_defaults_to_health(self, mock_run_show):
        """User enters invalid area — defaults to health."""
        mock_run_show.return_value = {"status": "success", "device": "spine-01", "data": [MOCK_SHOW_VERSION]}

        mock_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "accept"
        mock_result.data = TroubleshootAreaChoice(area="invalid_area")
        mock_ctx.elicit = AsyncMock(return_value=mock_result)

        result = await eos_interactive_troubleshoot("spine-01", ctx=mock_ctx)

        assert result["raw_data"]["area"] == "health"

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_device_unreachable(self, mock_run_show):
        """Device unreachable — returns error."""
        mock_run_show.return_value = {"status": "error", "device": "spine-01", "error": "Connection refused"}

        result = await eos_interactive_troubleshoot("spine-01", ctx=None)

        assert result["status"] == "error"
        assert result["diagnosis"]["severity"] == "critical"
        assert "unreachable" in result["diagnosis"]["findings"][0]["status"]

    async def test_invalid_host_returns_error(self):
        """Invalid host returns validation error."""
        result = await eos_interactive_troubleshoot("host; rm -rf /", ctx=None)
        assert result["status"] == "error"

    @patch("network_mcp.tools.workflows.run_show_command")
    async def test_bgp_area_finds_down_peer(self, mock_run_show):
        """BGP area analysis detects down peers."""

        def _mock_show(conn_mgr, host, commands, **kwargs):
            cmd = commands[0]
            if "bgp summary" in cmd:
                return {"status": "success", "device": host, "data": [MOCK_BGP_SUMMARY_WITH_DOWN_PEER]}
            return {"status": "success", "device": host, "data": [MOCK_SHOW_VERSION]}

        mock_run_show.side_effect = _mock_show

        mock_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.action = "accept"
        mock_result.data = TroubleshootAreaChoice(area="bgp")
        mock_ctx.elicit = AsyncMock(return_value=mock_result)

        result = await eos_interactive_troubleshoot("spine-01", ctx=mock_ctx)

        assert result["raw_data"]["area"] == "bgp"
        # Should find the down peer
        bgp_findings = [f for f in result["diagnosis"]["findings"] if "bgp_peer" in f.get("component", "")]
        assert len(bgp_findings) > 0


# --- .well-known/mcp.json Tests ---


class TestMCPServerIdentity:
    """Tests for the .well-known/mcp.json endpoint."""

    def test_well_known_endpoint_registered(self):
        """Verify the .well-known/mcp.json route is registered on the mcp instance."""
        from network_mcp.server import mcp

        route_paths = [route.path for route in mcp._custom_starlette_routes]
        assert "/.well-known/mcp.json" in route_paths

    def test_health_endpoint_registered(self):
        """Verify the /health route is still registered."""
        from network_mcp.server import mcp

        route_paths = [route.path for route in mcp._custom_starlette_routes]
        assert "/health" in route_paths

    async def test_well_known_response(self):
        """Test the response content of the .well-known/mcp.json handler."""
        from network_mcp.server import mcp_server_identity

        # Call the handler directly with a mock request
        mock_request = MagicMock()
        response = await mcp_server_identity(mock_request)
        assert response.status_code == 200
        import json

        body = json.loads(response.body)
        assert body["name"] == "network-mcp"
        from network_mcp import __version__

        assert body["version"] == __version__
        assert body["capabilities"]["tools"] is True
        assert body["capabilities"]["resources"] is True
        assert body["capabilities"]["prompts"] is True
