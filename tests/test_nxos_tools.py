"""Tests for expanded NX-OS vendor-specific tools (vPC detail, FEX detail, features, system resources).

Tests the nxos_get_vpc_detail, nxos_get_fex_detail, nxos_get_feature_list, and
nxos_get_system_resources tools with mock NX-OS data, verifying normalization,
error handling, and platform validation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from network_mcp.server import conn_mgr
from network_mcp.tools.cisco.fex import nxos_get_fex_detail
from network_mcp.tools.cisco.nxos import (
    _normalize_feature_list,
    _normalize_system_resources,
    nxos_get_feature_list,
    nxos_get_system_resources,
)
from network_mcp.tools.cisco.vpc import nxos_get_vpc_detail
from tests.mock_data.eos.mock_driver import MockEosDriver
from tests.mock_data.iosxe.mock_driver import MockIosXeDriver
from tests.mock_data.nxos.mock_driver import MockNxosDriver


@pytest.fixture
def nxos_driver():
    """Create a connected MockNxosDriver."""
    driver = MockNxosDriver()
    driver.connect()
    return driver


@pytest.fixture
def eos_driver():
    """Create a connected MockEosDriver (for platform validation tests)."""
    driver = MockEosDriver()
    driver.connect()
    return driver


@pytest.fixture
def iosxe_driver():
    """Create a connected MockIosXeDriver (for platform validation tests)."""
    driver = MockIosXeDriver()
    driver.connect()
    return driver


# --- nxos_get_vpc_detail tests ---


class TestNxosGetVpcDetail:
    """Test nxos_get_vpc_detail tool."""

    def test_success(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        assert result["status"] == "success"
        assert result["device"] == "n9k-01"

    def test_domain_id(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        data = result["data"]
        assert data["domain_id"] == 1

    def test_peer_keepalive(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        keepalive = result["data"]["peer_keepalive"]
        assert keepalive["destination"] == "10.0.0.2"
        assert keepalive["source"] == "10.0.0.1"
        assert keepalive["vrf"] == "management"
        assert keepalive["interval_ms"] == 1000
        assert keepalive["timeout_ms"] == 5000

    def test_consistency_params(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        params = result["data"]["consistency_params"]
        assert len(params) == 3

    def test_consistency_param_fields(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        param = result["data"]["consistency_params"][0]
        assert param["name"] == "STP Mode"
        assert param["local_value"] == "Rapid-PVST"
        assert param["peer_value"] == "Rapid-PVST"
        assert param["result"] == "success"

    def test_consistency_param_failed(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        failed_param = result["data"]["consistency_params"][2]
        assert failed_param["name"] == "LACP Mode"
        assert failed_param["result"] == "failed"

    def test_vpc_detail_active_vlans(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        vpcs = result["data"]["vpcs"]
        assert len(vpcs) == 2
        assert vpcs[0]["active_vlans"] == "100,200,300"

    def test_error_on_non_nxos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = nxos_get_vpc_detail("eos-spine-01")
        assert result["status"] == "error"
        assert "NX-OS" in result["error"]

    def test_error_on_empty_host(self):
        result = nxos_get_vpc_detail("")
        assert result["status"] == "error"

    def test_empty_response(self, nxos_driver):
        nxos_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        assert result["status"] == "error"

    def test_error_in_response(self, nxos_driver):
        nxos_driver.run_show = lambda cmds, **kw: [{"error": "Command not supported"}]
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        assert result["status"] == "error"

    def test_data_structure_types(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_detail("n9k-01")
        data = result["data"]
        assert isinstance(data["peer_keepalive"], dict)
        assert isinstance(data["consistency_params"], list)
        assert isinstance(data["vpcs"], list)


# --- nxos_get_fex_detail tests ---


class TestNxosGetFexDetail:
    """Test nxos_get_fex_detail tool."""

    def test_success(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 100)
        assert result["status"] == "success"
        assert result["device"] == "n9k-01"

    def test_fex_basic_fields(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 100)
        data = result["data"]
        assert data["fex_id"] == 100
        assert data["description"] == "FEX100 Fabric Extender"
        assert data["state"] == "Online"
        assert data["model"] == "N2K-C2248TP-1GE"
        assert data["serial"] == "FOC1234ABCD"

    def test_fex_firmware(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 100)
        data = result["data"]
        assert data["firmware"] == "7.0(3)I7(10)"

    def test_fex_uplinks(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 100)
        uplinks = result["data"]["uplinks"]
        assert len(uplinks) == 2
        assert uplinks[0]["interface"] == "Ethernet1/1"
        assert uplinks[0]["status"] == "up"
        assert uplinks[0]["speed"] == "10G"

    def test_fex_pinning(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 100)
        pinning = result["data"]["pinning"]
        assert len(pinning) == 2
        assert pinning[0]["host_interface"] == "Ethernet100/1/1"
        assert pinning[0]["fabric_interface"] == "Ethernet1/1"

    def test_invalid_fex_id_too_low(self):
        result = nxos_get_fex_detail("n9k-01", 50)
        assert result["status"] == "error"
        assert "Invalid FEX ID" in result["error"]

    def test_invalid_fex_id_too_high(self):
        result = nxos_get_fex_detail("n9k-01", 200)
        assert result["status"] == "error"
        assert "Invalid FEX ID" in result["error"]

    def test_invalid_fex_id_boundary_low(self, nxos_driver):
        """FEX ID 100 is valid (boundary)."""
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 100)
        assert result["status"] == "success"

    def test_invalid_fex_id_boundary_high(self, nxos_driver):
        """FEX ID 199 is valid (boundary)."""
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 199)
        assert result["status"] == "success"

    def test_error_on_non_nxos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = nxos_get_fex_detail("eos-spine-01", 100)
        assert result["status"] == "error"
        assert "NX-OS" in result["error"]

    def test_error_on_iosxe_device(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = nxos_get_fex_detail("csr1000v-01", 100)
        assert result["status"] == "error"
        assert "NX-OS" in result["error"]

    def test_error_on_empty_host(self):
        result = nxos_get_fex_detail("", 100)
        assert result["status"] == "error"

    def test_empty_response(self, nxos_driver):
        nxos_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 100)
        assert result["status"] == "error"

    def test_data_structure_types(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_detail("n9k-01", 100)
        data = result["data"]
        assert isinstance(data["fex_id"], int)
        assert isinstance(data["uplinks"], list)
        assert isinstance(data["pinning"], list)
        assert isinstance(data["firmware"], str)


# --- nxos_get_feature_list tests ---


class TestNxosGetFeatureList:
    """Test nxos_get_feature_list tool."""

    def test_success(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_feature_list("n9k-01")
        assert result["status"] == "success"
        assert result["device"] == "n9k-01"

    def test_feature_list(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_feature_list("n9k-01")
        data = result["data"]
        assert data["total_count"] == 7

    def test_enabled_count(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_feature_list("n9k-01")
        data = result["data"]
        assert data["enabled_count"] == 4  # bgp, vpc, lacp, nxapi

    def test_disabled_count(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_feature_list("n9k-01")
        data = result["data"]
        assert data["disabled_count"] == 3  # ospf, pim, scp-server

    def test_feature_fields(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_feature_list("n9k-01")
        feature = result["data"]["features"][0]
        assert feature["name"] == "bgp"
        assert feature["status"] == "enabled"
        assert feature["instance"] == 1
        assert feature["enabled"] is True

    def test_disabled_feature(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_feature_list("n9k-01")
        ospf = result["data"]["features"][3]
        assert ospf["name"] == "ospf"
        assert ospf["enabled"] is False

    def test_error_on_non_nxos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = nxos_get_feature_list("eos-spine-01")
        assert result["status"] == "error"
        assert "NX-OS" in result["error"]

    def test_error_on_empty_host(self):
        result = nxos_get_feature_list("")
        assert result["status"] == "error"

    def test_empty_response(self, nxos_driver):
        nxos_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_feature_list("n9k-01")
        assert result["status"] == "error"

    def test_host_injection_rejected(self):
        result = nxos_get_feature_list("host;rm -rf /")
        assert result["status"] == "error"

    def test_data_structure_types(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_feature_list("n9k-01")
        data = result["data"]
        assert isinstance(data["features"], list)
        assert isinstance(data["total_count"], int)
        assert isinstance(data["enabled_count"], int)
        assert isinstance(data["disabled_count"], int)


# --- nxos_get_system_resources tests ---


class TestNxosGetSystemResources:
    """Test nxos_get_system_resources tool."""

    def test_success(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_system_resources("n9k-01")
        assert result["status"] == "success"
        assert result["device"] == "n9k-01"

    def test_cpu_load(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_system_resources("n9k-01")
        cpu = result["data"]["cpu"]
        assert cpu["load_1min"] == 0.25
        assert cpu["load_5min"] == 0.18
        assert cpu["load_15min"] == 0.12

    def test_memory_info(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_system_resources("n9k-01")
        memory = result["data"]["memory"]
        assert memory["total_kb"] == 16384000
        assert memory["used_kb"] == 8192000
        assert memory["free_kb"] == 8192000

    def test_memory_utilization_pct(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_system_resources("n9k-01")
        memory = result["data"]["memory"]
        assert memory["utilization_pct"] == 50.0

    def test_process_count(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_system_resources("n9k-01")
        assert result["data"]["process_count"] == 512

    def test_error_on_non_nxos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = nxos_get_system_resources("eos-spine-01")
        assert result["status"] == "error"
        assert "NX-OS" in result["error"]

    def test_error_on_iosxe_device(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = nxos_get_system_resources("csr1000v-01")
        assert result["status"] == "error"
        assert "NX-OS" in result["error"]

    def test_error_on_empty_host(self):
        result = nxos_get_system_resources("")
        assert result["status"] == "error"

    def test_empty_response(self, nxos_driver):
        nxos_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_system_resources("n9k-01")
        assert result["status"] == "error"

    def test_exception_during_execution(self):
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = nxos_get_system_resources("n9k-01")
        assert result["status"] == "error"

    def test_data_structure_types(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_system_resources("n9k-01")
        data = result["data"]
        assert isinstance(data["cpu"], dict)
        assert isinstance(data["memory"], dict)
        assert isinstance(data["process_count"], int)
        assert isinstance(data["cpu"]["load_1min"], float)
        assert isinstance(data["memory"]["total_kb"], int)


# --- Normalization function tests ---


class TestFeatureListNormalization:
    """Test _normalize_feature_list with various input formats."""

    def test_standard_format(self):
        data = {
            "TABLE_cfcFeatureCtrlTable": {
                "ROW_cfcFeatureCtrlTable": [
                    {
                        "cfcFeatureCtrlName2": "bgp",
                        "cfcFeatureCtrlOpStatus2": "enabled",
                        "cfcFeatureCtrlOpStatusInstance": 1,
                    },
                    {
                        "cfcFeatureCtrlName2": "ospf",
                        "cfcFeatureCtrlOpStatus2": "disabled",
                        "cfcFeatureCtrlOpStatusInstance": 1,
                    },
                ]
            }
        }
        result = _normalize_feature_list(data)
        assert result["total_count"] == 2
        assert result["enabled_count"] == 1
        assert result["disabled_count"] == 1

    def test_single_feature_as_dict(self):
        data = {
            "TABLE_cfcFeatureCtrlTable": {
                "ROW_cfcFeatureCtrlTable": {
                    "cfcFeatureCtrlName2": "bgp",
                    "cfcFeatureCtrlOpStatus2": "enabled",
                    "cfcFeatureCtrlOpStatusInstance": 1,
                }
            }
        }
        result = _normalize_feature_list(data)
        assert result["total_count"] == 1
        assert result["enabled_count"] == 1

    def test_empty_table(self):
        result = _normalize_feature_list({})
        assert result["total_count"] == 0
        assert result["features"] == []


class TestSystemResourcesNormalization:
    """Test _normalize_system_resources with various input formats."""

    def test_standard_format(self):
        data = {
            "load_avg_1min": "0.50",
            "load_avg_5min": "0.30",
            "load_avg_15min": "0.20",
            "memory_usage_total": 8192000,
            "memory_usage_used": 4096000,
            "memory_usage_free": 4096000,
            "processes_total": 256,
        }
        result = _normalize_system_resources(data)
        assert result["cpu"]["load_1min"] == 0.50
        assert result["memory"]["total_kb"] == 8192000
        assert result["memory"]["utilization_pct"] == 50.0
        assert result["process_count"] == 256

    def test_zero_memory(self):
        data = {"memory_usage_total": 0, "memory_usage_used": 0, "memory_usage_free": 0}
        result = _normalize_system_resources(data)
        assert result["memory"]["utilization_pct"] == 0.0

    def test_missing_fields(self):
        result = _normalize_system_resources({})
        assert result["cpu"]["load_1min"] == 0.0
        assert result["memory"]["total_kb"] == 0
        assert result["process_count"] == 0


# --- handle_tool_errors decorator tests ---


class TestHandleToolErrorsOnNxosTools:
    """Verify @handle_tool_errors wraps NX-OS tools with standardized error dicts."""

    def test_vpc_detail_timeout_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = nxos_get_vpc_detail("n9k-01")
        assert result["status"] == "error"
        assert result["device"] == "n9k-01"
        assert "Timeout" in result["error"]

    def test_fex_detail_timeout_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("read timeout")):
            result = nxos_get_fex_detail("n9k-01", 100)
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_feature_list_unexpected_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = nxos_get_feature_list("n9k-01")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]
        assert "n9k-01" in result["error"]

    def test_system_resources_connection_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("refused")):
            result = nxos_get_system_resources("n9k-01")
        assert result["status"] == "error"
        assert "refused" in result["error"]
