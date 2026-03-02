"""Tests for Cisco IOS-XE vendor-specific tools (RESTCONF capabilities, platform info, SD-WAN).

Tests the iosxe_get_restconf_capabilities, iosxe_get_platform_info, and
iosxe_get_sdwan_status tools with mock IOS-XE data, verifying normalization,
error handling, and platform validation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from network_mcp.server import conn_mgr
from network_mcp.tools.cisco.iosxe import (
    _normalize_platform_info,
    _normalize_restconf_capabilities,
    _normalize_sdwan_status,
    iosxe_get_platform_info,
    iosxe_get_restconf_capabilities,
    iosxe_get_sdwan_status,
)
from tests.mock_data.eos.mock_driver import MockEosDriver
from tests.mock_data.iosxe.mock_driver import MockIosXeDriver
from tests.mock_data.nxos.mock_driver import MockNxosDriver


@pytest.fixture
def iosxe_driver():
    """Create a connected MockIosXeDriver."""
    driver = MockIosXeDriver()
    driver.connect()
    return driver


@pytest.fixture
def nxos_driver():
    """Create a connected MockNxosDriver (for platform validation tests)."""
    driver = MockNxosDriver()
    driver.connect()
    return driver


@pytest.fixture
def eos_driver():
    """Create a connected MockEosDriver (for platform validation tests)."""
    driver = MockEosDriver()
    driver.connect()
    return driver


# --- iosxe_get_restconf_capabilities tests ---


class TestIosxeGetRestconfCapabilities:
    """Test iosxe_get_restconf_capabilities tool."""

    def test_success(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        assert result["status"] == "success"
        assert result["device"] == "csr1000v-01"

    def test_capabilities_list(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        data = result["data"]
        assert isinstance(data["capabilities"], list)
        assert len(data["capabilities"]) > 0

    def test_capability_count(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        data = result["data"]
        assert data["capability_count"] == len(data["capabilities"])

    def test_capabilities_contain_urns(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        data = result["data"]
        for cap in data["capabilities"]:
            assert cap.startswith("urn:ietf:params:restconf:")

    def test_error_on_non_iosxe_device(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = iosxe_get_restconf_capabilities("n9k-01")
        assert result["status"] == "error"
        assert "IOS-XE" in result["error"]

    def test_error_on_eos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = iosxe_get_restconf_capabilities("eos-spine-01")
        assert result["status"] == "error"
        assert "IOS-XE" in result["error"]

    def test_error_on_empty_host(self):
        result = iosxe_get_restconf_capabilities("")
        assert result["status"] == "error"
        assert "Host is required" in result["error"]

    def test_host_injection_rejected(self):
        result = iosxe_get_restconf_capabilities("host;rm -rf /")
        assert result["status"] == "error"

    def test_data_structure_types(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        data = result["data"]
        assert isinstance(data["capabilities"], list)
        assert isinstance(data["yang_modules"], list)
        assert isinstance(data["capability_count"], int)
        assert isinstance(data["module_count"], int)


# --- iosxe_get_platform_info tests ---


class TestIosxeGetPlatformInfo:
    """Test iosxe_get_platform_info tool."""

    def test_success(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        assert result["status"] == "success"
        assert result["device"] == "csr1000v-01"

    def test_chassis_pid(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        data = result["data"]
        assert data["chassis_pid"] == "CSR1000V"

    def test_chassis_serial(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        data = result["data"]
        assert data["chassis_sn"] == "9ZL30UN51R9"

    def test_boot_variable(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        data = result["data"]
        assert "bootflash" in data["boot_variable"]

    def test_license_status(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        data = result["data"]
        assert "REGISTERED" in data["license_status"]

    def test_components_list(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        data = result["data"]
        assert isinstance(data["components"], list)
        assert data["component_count"] == 3

    def test_component_fields(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        comp = result["data"]["components"][0]
        assert comp["name"] == "Chassis"
        assert "CSR1000V" in comp["description"]
        assert comp["part_no"] == "CSR1000V"
        assert comp["serial_no"] == "9ZL30UN51R9"

    def test_error_on_non_iosxe_device(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = iosxe_get_platform_info("n9k-01")
        assert result["status"] == "error"
        assert "IOS-XE" in result["error"]

    def test_error_on_empty_host(self):
        result = iosxe_get_platform_info("")
        assert result["status"] == "error"

    def test_data_structure_types(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        data = result["data"]
        assert isinstance(data["chassis_pid"], str)
        assert isinstance(data["chassis_sn"], str)
        assert isinstance(data["components"], list)
        assert isinstance(data["component_count"], int)


# --- iosxe_get_sdwan_status tests ---


class TestIosxeGetSdwanStatus:
    """Test iosxe_get_sdwan_status tool."""

    def test_success(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_sdwan_status("csr1000v-01")
        assert result["status"] == "success"
        assert result["device"] == "csr1000v-01"

    def test_sdwan_enabled(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_sdwan_status("csr1000v-01")
        data = result["data"]
        assert data["sdwan_enabled"] is True

    def test_control_connections(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_sdwan_status("csr1000v-01")
        data = result["data"]
        assert len(data["control_connections"]) == 3
        assert data["connection_count"] == 3

    def test_connection_fields(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_sdwan_status("csr1000v-01")
        conn = result["data"]["control_connections"][0]
        assert conn["peer_type"] == "vsmart"
        assert conn["peer_ip"] == "172.16.0.1"
        assert conn["site_id"] == 100
        assert conn["state"] == "up"

    def test_vbond_connection(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_sdwan_status("csr1000v-01")
        conn = result["data"]["control_connections"][2]
        assert conn["peer_type"] == "vbond"
        assert conn["site_id"] == 0

    def test_not_supported_on_non_sdwan_device(self, iosxe_driver):
        """Test graceful handling when SD-WAN is not enabled."""
        iosxe_driver.run_show = lambda cmds, **kw: [{"error": "Command not supported"}]
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_sdwan_status("csr1000v-01")
        assert result["status"] == "not_supported"
        assert result["data"]["sdwan_enabled"] is False

    def test_not_supported_on_invalid_command(self, iosxe_driver):
        """Test graceful handling with 'invalid' error message."""
        iosxe_driver.run_show = lambda cmds, **kw: [{"error": "Invalid command"}]
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_sdwan_status("csr1000v-01")
        assert result["status"] == "not_supported"

    def test_error_on_non_iosxe_device(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = iosxe_get_sdwan_status("n9k-01")
        assert result["status"] == "error"
        assert "IOS-XE" in result["error"]

    def test_error_on_empty_host(self):
        result = iosxe_get_sdwan_status("")
        assert result["status"] == "error"

    def test_host_injection_rejected(self):
        result = iosxe_get_sdwan_status("host|cat /etc/passwd")
        assert result["status"] == "error"

    def test_data_structure_types(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_sdwan_status("csr1000v-01")
        data = result["data"]
        assert isinstance(data["sdwan_enabled"], bool)
        assert isinstance(data["control_connections"], list)
        assert isinstance(data["connection_count"], int)
        for conn in data["control_connections"]:
            assert isinstance(conn["peer_type"], str)
            assert isinstance(conn["site_id"], int)


# --- Error path tests ---


class TestIosxeErrorPaths:
    """Test IOS-XE tool error handling and edge cases."""

    def test_empty_response_capabilities(self, iosxe_driver):
        iosxe_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        assert result["status"] == "error"
        assert "Failed to get RESTCONF capabilities" in result["error"]

    def test_error_in_response_capabilities(self, iosxe_driver):
        iosxe_driver.run_show = lambda cmds, **kw: [{"error": "Connection refused"}]
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        assert result["status"] == "error"
        assert "Connection refused" in result["error"]

    def test_empty_response_platform_info(self, iosxe_driver):
        iosxe_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = iosxe_get_platform_info("csr1000v-01")
        assert result["status"] == "error"

    def test_exception_during_capabilities(self):
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("Connection lost")):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        assert result["status"] == "error"
        assert "Connection lost" in result["error"]

    def test_exception_during_platform_info(self):
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("Timeout")):
            result = iosxe_get_platform_info("csr1000v-01")
        assert result["status"] == "error"

    def test_exception_during_sdwan_status(self):
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = iosxe_get_sdwan_status("csr1000v-01")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]


# --- Normalization function tests ---


class TestRestconfCapabilitiesNormalization:
    """Test _normalize_restconf_capabilities with various input formats."""

    def test_restconf_monitoring_format(self):
        data = {
            "ietf-restconf-monitoring:restconf-state": {
                "capabilities": {
                    "capability": [
                        "urn:ietf:params:restconf:capability:defaults:1.0",
                        "urn:ietf:params:restconf:capability:depth:1.0",
                    ]
                }
            }
        }
        result = _normalize_restconf_capabilities(data)
        assert len(result["capabilities"]) == 2
        assert result["capability_count"] == 2

    def test_yang_library_format(self):
        data = {
            "ietf-yang-library:modules-state": {
                "module": [
                    {
                        "name": "ietf-interfaces",
                        "revision": "2018-02-20",
                        "namespace": "urn:ietf:params:xml:ns:yang:ietf-interfaces",
                        "conformance-type": "implement",
                    },
                ]
            }
        }
        result = _normalize_restconf_capabilities(data)
        assert len(result["yang_modules"]) == 1
        assert result["module_count"] == 1
        assert result["yang_modules"][0]["name"] == "ietf-interfaces"

    def test_empty_data(self):
        result = _normalize_restconf_capabilities({})
        assert result["capabilities"] == []
        assert result["yang_modules"] == []
        assert result["capability_count"] == 0
        assert result["module_count"] == 0

    def test_combined_format(self):
        data = {
            "ietf-restconf-monitoring:restconf-state": {"capabilities": {"capability": ["cap1", "cap2"]}},
            "ietf-yang-library:modules-state": {
                "module": [
                    {"name": "mod1", "revision": "2023-01-01", "namespace": "ns1", "conformance-type": "implement"},
                ]
            },
        }
        result = _normalize_restconf_capabilities(data)
        assert result["capability_count"] == 2
        assert result["module_count"] == 1


class TestPlatformInfoNormalization:
    """Test _normalize_platform_info with various input formats."""

    def test_components_format(self):
        data = {
            "Cisco-IOS-XE-platform-oper:components": {
                "component": [
                    {
                        "cname": "Chassis",
                        "type": "chassis",
                        "state": {"description": "Cisco CSR1000V", "part-no": "CSR1000V", "serial-no": "SN123"},
                    },
                ]
            },
            "pid": "CSR1000V",
            "sn": "SN123",
        }
        result = _normalize_platform_info(data)
        assert result["chassis_pid"] == "CSR1000V"
        assert len(result["components"]) == 1

    def test_empty_components(self):
        data = {"Cisco-IOS-XE-platform-oper:components": {"component": []}}
        result = _normalize_platform_info(data)
        assert result["component_count"] == 0

    def test_missing_components(self):
        data = {"pid": "C9300-48T"}
        result = _normalize_platform_info(data)
        assert result["chassis_pid"] == "C9300-48T"
        assert result["component_count"] == 0


class TestSdwanStatusNormalization:
    """Test _normalize_sdwan_status with various input formats."""

    def test_table_row_format(self):
        data = {
            "TABLE_connections": {
                "ROW_connections": [
                    {
                        "peer-type": "vsmart",
                        "system-ip": "10.0.0.1",
                        "site-id": 100,
                        "state": "up",
                        "uptime": "1:00:00:00",
                    },
                ]
            }
        }
        result = _normalize_sdwan_status(data)
        assert result["sdwan_enabled"] is True
        assert result["connection_count"] == 1

    def test_single_connection_as_dict(self):
        data = {
            "TABLE_connections": {
                "ROW_connections": {
                    "peer-type": "vmanage",
                    "system-ip": "10.0.0.2",
                    "site-id": 200,
                    "state": "up",
                    "uptime": "2:00:00:00",
                }
            }
        }
        result = _normalize_sdwan_status(data)
        assert result["connection_count"] == 1
        assert result["control_connections"][0]["peer_type"] == "vmanage"

    def test_empty_connections(self):
        data = {"TABLE_connections": {"ROW_connections": []}}
        result = _normalize_sdwan_status(data)
        assert result["sdwan_enabled"] is True
        assert result["connection_count"] == 0


# --- Conditional loading tests ---


class TestIosxeConditionalLoading:
    """Test that IOS-XE tools load conditionally based on httpx availability."""

    def test_iosxe_tools_are_importable(self):
        from network_mcp.tools.cisco import iosxe  # noqa: F401

    def test_restconf_capabilities_tool_registered(self):
        assert callable(iosxe_get_restconf_capabilities)

    def test_platform_info_tool_registered(self):
        assert callable(iosxe_get_platform_info)

    def test_sdwan_status_tool_registered(self):
        assert callable(iosxe_get_sdwan_status)


# --- handle_tool_errors decorator tests ---


class TestHandleToolErrorsOnIosxeTools:
    """Verify @handle_tool_errors wraps IOS-XE tools with standardized error dicts."""

    def test_capabilities_timeout_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = iosxe_get_restconf_capabilities("csr1000v-01")
        assert result["status"] == "error"
        assert result["device"] == "csr1000v-01"
        assert "Timeout" in result["error"]

    def test_platform_info_connection_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("refused")):
            result = iosxe_get_platform_info("csr1000v-01")
        assert result["status"] == "error"
        assert "refused" in result["error"]

    def test_sdwan_unexpected_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = iosxe_get_sdwan_status("csr1000v-01")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]
        assert "csr1000v-01" in result["error"]
