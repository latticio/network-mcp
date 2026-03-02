"""Tests for Cisco-specific tools (vPC, FEX).

Tests the nxos_get_vpc_status and nxos_get_fex_status tools with mock
NX-OS data, verifying normalization, error handling, and platform validation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from network_mcp.server import conn_mgr
from network_mcp.tools.cisco.fex import nxos_get_fex_status
from network_mcp.tools.cisco.vpc import nxos_get_vpc_status
from tests.mock_data.eos.mock_driver import MockEosDriver
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


# --- nxos_get_vpc_status tests ---


class TestNxosGetVpcStatus:
    """Test nxos_get_vpc_status tool."""

    def test_success(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        assert result["status"] == "success"
        assert result["device"] == "n9k-01"

    def test_vpc_domain_id(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        data = result["data"]
        assert data["domain_id"] == 1

    def test_vpc_role(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        data = result["data"]
        assert data["role"] == "primary"

    def test_vpc_peer_status(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        data = result["data"]
        assert data["peer_status"] == "peer adjacency formed ok"
        assert data["peer_keepalive_status"] == "peer is alive"

    def test_vpc_peer_link(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        data = result["data"]
        assert data["peer_link"]["status"] == "up"
        assert data["peer_link"]["port_channel"] == "Po1"

    def test_vpc_members(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        data = result["data"]
        assert len(data["vpcs"]) == 3

    def test_vpc_member_fields(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        vpc = result["data"]["vpcs"][0]
        assert vpc["id"] == 10
        assert vpc["port_channel"] == "Po10"
        assert vpc["status"] == "up"
        assert vpc["consistency"] == "consistent"

    def test_vpc_down_member(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        # Third vPC member is down/inconsistent
        vpc_down = result["data"]["vpcs"][2]
        assert vpc_down["status"] == "down"
        assert vpc_down["consistency"] == "inconsistent"

    def test_error_on_non_nxos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = nxos_get_vpc_status("eos-spine-01")
        assert result["status"] == "error"
        assert "NX-OS" in result["error"]

    def test_error_on_empty_host(self):
        result = nxos_get_vpc_status("")
        assert result["status"] == "error"
        assert "Host is required" in result["error"]

    def test_data_structure_types(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        data = result["data"]
        assert isinstance(data["domain_id"], int)
        assert isinstance(data["role"], str)
        assert isinstance(data["peer_status"], str)
        assert isinstance(data["peer_keepalive_status"], str)
        assert isinstance(data["peer_link"], dict)
        assert isinstance(data["vpcs"], list)


# --- nxos_get_fex_status tests ---


class TestNxosGetFexStatus:
    """Test nxos_get_fex_status tool."""

    def test_success(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        assert result["status"] == "success"
        assert result["device"] == "n9k-01"

    def test_fex_count(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        data = result["data"]
        assert data["fex_count"] == 2

    def test_fex_units_list(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        data = result["data"]
        assert len(data["fex_units"]) == 2

    def test_fex_unit_fields(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        fex = result["data"]["fex_units"][0]
        assert fex["id"] == 100
        assert fex["description"] == "FEX100 Fabric Extender"
        assert fex["state"] == "Online"
        assert fex["model"] == "N2K-C2248TP-1GE"
        assert fex["serial"] == "FOC1234ABCD"
        assert fex["uplink_port_channel"] == "Po100"

    def test_second_fex_unit(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        fex = result["data"]["fex_units"][1]
        assert fex["id"] == 101
        assert fex["serial"] == "FOC1234EFGH"

    def test_error_on_non_nxos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = nxos_get_fex_status("eos-spine-01")
        assert result["status"] == "error"
        assert "NX-OS" in result["error"]

    def test_error_on_empty_host(self):
        result = nxos_get_fex_status("")
        assert result["status"] == "error"
        assert "Host is required" in result["error"]

    def test_data_structure_types(self, nxos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        data = result["data"]
        assert isinstance(data["fex_count"], int)
        assert isinstance(data["fex_units"], list)
        for fex in data["fex_units"]:
            assert isinstance(fex["id"], int)
            assert isinstance(fex["description"], str)
            assert isinstance(fex["state"], str)
            assert isinstance(fex["model"], str)
            assert isinstance(fex["serial"], str)
            assert isinstance(fex["uplink_port_channel"], str)


# --- Conditional loading tests ---


class TestConditionalLoading:
    """Test that Cisco tools load conditionally based on httpx availability."""

    def test_cisco_tools_are_importable(self):
        """Verify Cisco tools can be imported (httpx is available in test env)."""
        from network_mcp.tools.cisco import fex, vpc  # noqa: F401

    def test_vpc_tool_registered(self):
        """Verify nxos_get_vpc_status is a callable tool."""
        assert callable(nxos_get_vpc_status)

    def test_fex_tool_registered(self):
        """Verify nxos_get_fex_status is a callable tool."""
        assert callable(nxos_get_fex_status)


# --- Error and edge case tests ---


class TestVpcErrorPaths:
    """Test vPC error handling and edge cases."""

    def test_empty_response(self, nxos_driver):
        """Test handling of empty response from run_show."""
        nxos_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        assert result["status"] == "error"
        assert "Failed to get vPC status" in result["error"]

    def test_error_in_response(self, nxos_driver):
        """Test handling of error key in response data."""
        nxos_driver.run_show = lambda cmds, **kw: [{"error": "Command not supported"}]
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        assert result["status"] == "error"
        assert "Command not supported" in result["error"]

    def test_exception_during_execution(self):
        """Test handling of unexpected exception."""
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("Connection lost")):
            result = nxos_get_vpc_status("n9k-01")
        assert result["status"] == "error"
        assert "Connection lost" in result["error"]

    def test_single_vpc_member_as_dict(self, nxos_driver):
        """Test normalization when TABLE_vpc.ROW_vpc is a dict (single member)."""
        single_vpc_data = {
            "vpc-domain-id": 1,
            "vpc-role": "primary",
            "vpc-peer-status": "peer adjacency formed ok",
            "vpc-peer-keepalive-status": "peer is alive",
            "vpc-peer-link-status": "up",
            "vpc-peer-link-port-channel": "Po1",
            "TABLE_vpc": {
                "ROW_vpc": {
                    "vpc-id": 10,
                    "vpc-port-channel": "Po10",
                    "vpc-port-status": "up",
                    "vpc-consistency-status": "consistent",
                }
            },
        }
        nxos_driver.run_show = lambda cmds, **kw: [single_vpc_data]
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_vpc_status("n9k-01")
        assert result["status"] == "success"
        assert len(result["data"]["vpcs"]) == 1
        assert result["data"]["vpcs"][0]["id"] == 10

    def test_int_or_default_with_invalid_value(self):
        """Test _int_or_default returns default for non-numeric values."""
        from network_mcp.tools.cisco.vpc import _int_or_default

        assert _int_or_default("not-a-number") == 0
        assert _int_or_default(None) == 0
        assert _int_or_default("abc", 42) == 42
        assert _int_or_default(10) == 10
        assert _int_or_default("5") == 5


class TestFexErrorPaths:
    """Test FEX error handling and edge cases."""

    def test_empty_response(self, nxos_driver):
        """Test handling of empty response from run_show."""
        nxos_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        assert result["status"] == "error"
        assert "Failed to get FEX status" in result["error"]

    def test_error_in_response(self, nxos_driver):
        """Test handling of error key in response data."""
        nxos_driver.run_show = lambda cmds, **kw: [{"error": "Command not supported"}]
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        assert result["status"] == "error"
        assert "Command not supported" in result["error"]

    def test_exception_during_execution(self):
        """Test handling of unexpected exception."""
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("Connection lost")):
            result = nxos_get_fex_status("n9k-01")
        assert result["status"] == "error"
        assert "Connection lost" in result["error"]

    def test_single_fex_as_dict(self, nxos_driver):
        """Test normalization when TABLE_fex.ROW_fex is a dict (single FEX)."""
        single_fex_data = {
            "TABLE_fex": {
                "ROW_fex": {
                    "fex_id": 100,
                    "fex_descr": "FEX100",
                    "fex_state": "Online",
                    "fex_model": "N2K-C2248TP-1GE",
                    "fex_serial": "FOC9999XXXX",
                    "fex_fabric_if": "Po100",
                }
            }
        }
        nxos_driver.run_show = lambda cmds, **kw: [single_fex_data]
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        assert result["status"] == "success"
        assert result["data"]["fex_count"] == 1
        assert result["data"]["fex_units"][0]["id"] == 100

    def test_int_or_default_with_invalid_value(self):
        """Test _int_or_default returns default for non-numeric values."""
        from network_mcp.tools.cisco.fex import _int_or_default

        assert _int_or_default("not-a-number") == 0
        assert _int_or_default(None) == 0
        assert _int_or_default("abc", 42) == 42

    def test_empty_fex_table(self, nxos_driver):
        """Test handling of empty TABLE_fex."""
        nxos_driver.run_show = lambda cmds, **kw: [{}]
        with patch.object(conn_mgr, "get_driver", return_value=nxos_driver):
            result = nxos_get_fex_status("n9k-01")
        assert result["status"] == "success"
        assert result["data"]["fex_count"] == 0
        assert result["data"]["fex_units"] == []

    def test_host_injection_rejected(self):
        """Test that CLI injection in host parameter is rejected."""
        result = nxos_get_fex_status("host;rm -rf /")
        assert result["status"] == "error"


class TestVpcHostValidation:
    """Test host validation edge cases for vPC tool."""

    def test_host_injection_rejected(self):
        """Test that CLI injection in host parameter is rejected."""
        result = nxos_get_vpc_status("host;rm -rf /")
        assert result["status"] == "error"

    def test_host_with_pipe_rejected(self):
        """Test that pipe in host is rejected."""
        result = nxos_get_vpc_status("host|cat /etc/passwd")
        assert result["status"] == "error"


# --- handle_tool_errors decorator tests ---


class TestHandleToolErrorsOnCiscoTools:
    """Verify @handle_tool_errors wraps Cisco tools with standardized error dicts."""

    def test_vpc_timeout_error(self):
        """Verify TimeoutError is caught by handle_tool_errors on nxos_get_vpc_status."""
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = nxos_get_vpc_status("n9k-01")
        assert result["status"] == "error"
        assert result["device"] == "n9k-01"
        assert "Timeout" in result["error"]
        assert "timed out" in result["error"]

    def test_fex_timeout_error(self):
        """Verify TimeoutError is caught by handle_tool_errors on nxos_get_fex_status."""
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("read timeout")):
            result = nxos_get_fex_status("n9k-01")
        assert result["status"] == "error"
        assert result["device"] == "n9k-01"
        assert "Timeout" in result["error"]

    def test_vpc_unexpected_error_format(self):
        """Verify unexpected errors include host name in message."""
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = nxos_get_vpc_status("n9k-01")
        assert result["status"] == "error"
        assert result["device"] == "n9k-01"
        assert "Unexpected error" in result["error"]
        assert "n9k-01" in result["error"]
        assert "driver crash" in result["error"]

    def test_fex_unexpected_error_format(self):
        """Verify unexpected errors include host name in message."""
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = nxos_get_fex_status("n9k-01")
        assert result["status"] == "error"
        assert result["device"] == "n9k-01"
        assert "Unexpected error" in result["error"]
        assert "n9k-01" in result["error"]
