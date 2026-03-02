"""Tests for interface tools."""

import pyeapi.eapilib

from network_mcp.tools.interfaces import (
    eos_get_interface_counters,
    eos_get_interface_detail,
    eos_get_interface_errors,
    eos_get_interfaces_status,
    eos_get_ip_interfaces,
    eos_get_transceiver_info,
)
from tests.conftest import MOCK_INTERFACE_DETAIL, MOCK_INTERFACE_ERRORS, MOCK_INTERFACES_STATUS, MOCK_TRANSCEIVER_INFO


def test_eos_get_interfaces_status(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_INTERFACES_STATUS]

    result = eos_get_interfaces_status("test-switch")

    assert result["status"] == "success"
    assert "Ethernet1" in result["data"]
    assert result["data"]["Ethernet1"]["linkStatus"] == "connected"


def test_eos_get_interface_detail(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_INTERFACE_DETAIL]

    result = eos_get_interface_detail("test-switch", "Ethernet1")

    assert result["status"] == "success"
    assert result["data"]["name"] == "Ethernet1"
    assert result["data"]["lineProtocolStatus"] == "up"


def test_eos_get_interface_detail_not_found(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"interfaces": {}}]

    result = eos_get_interface_detail("test-switch", "Ethernet99")

    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_eos_get_interface_counters(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"interfaces": {"Ethernet1": {"inOctets": 100}}}]

    result = eos_get_interface_counters("test-switch")

    assert result["status"] == "success"
    assert "Ethernet1" in result["data"]


def test_eos_get_interface_errors(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"interfaceErrorCounters": {"Ethernet1": {"crcErrors": 0}}}]

    result = eos_get_interface_errors("test-switch")

    assert result["status"] == "success"


def test_eos_get_ip_interfaces(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"interfaces": {"Loopback0": {"interfaceAddress": {}}}}]

    result = eos_get_ip_interfaces("test-switch")

    assert result["status"] == "success"


def test_eos_get_transceiver_info(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"interfaces": {"Ethernet1": {"mediaType": "100GBASE-SR4"}}}]

    result = eos_get_transceiver_info("test-switch")

    assert result["status"] == "success"


# --- L16: Error case tests ---


def test_eos_get_interfaces_status_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("10.0.0.1", "refused")

    result = eos_get_interfaces_status("test-switch")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_eos_get_interfaces_status_command_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")

    result = eos_get_interfaces_status("test-switch")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


def test_eos_get_interface_detail_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("10.0.0.1", "timeout")

    result = eos_get_interface_detail("test-switch", "Ethernet1")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_eos_get_interface_detail_invalid_name():
    """Invalid interface name returns error without hitting the device."""
    result = eos_get_interface_detail("test-switch", "InvalidPort1")

    assert result["status"] == "error"
    assert "Invalid interface" in result["error"]


def test_eos_get_interface_counters_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("10.0.0.1", "refused")

    result = eos_get_interface_counters("test-switch")

    assert result["status"] == "error"


def test_eos_get_interface_errors_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("10.0.0.1", "refused")

    result = eos_get_interface_errors("test-switch")

    assert result["status"] == "error"


# --- Field filtering tests ---


def test_get_interface_counters_fields_filter(mock_conn_mgr, mock_node):
    """Field filtering returns only requested fields per interface."""
    mock_data = {
        "interfaces": {
            "Ethernet1": {"inOctets": 100, "outOctets": 200, "inUcastPkts": 50, "outUcastPkts": 75},
            "Ethernet2": {"inOctets": 300, "outOctets": 400, "inUcastPkts": 150, "outUcastPkts": 175},
        }
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_interface_counters("test-switch", fields=["inOctets", "outOctets"])

    assert result["status"] == "success"
    assert set(result["data"]["Ethernet1"].keys()) == {"inOctets", "outOctets"}
    assert set(result["data"]["Ethernet2"].keys()) == {"inOctets", "outOctets"}


def test_get_interface_counters_fields_none(mock_conn_mgr, mock_node):
    """fields=None returns all fields unchanged."""
    mock_data = {
        "interfaces": {
            "Ethernet1": {"inOctets": 100, "outOctets": 200, "inUcastPkts": 50},
        }
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_interface_counters("test-switch", fields=None)

    assert result["status"] == "success"
    assert len(result["data"]["Ethernet1"].keys()) == 3


def test_get_interface_errors_fields_filter(mock_conn_mgr, mock_node):
    """Field filtering on error counters returns only requested fields."""
    mock_node.run_commands.return_value = [MOCK_INTERFACE_ERRORS]

    result = eos_get_interface_errors("test-switch", fields=["inputErrors", "fcsErrors"])

    assert result["status"] == "success"
    assert set(result["data"]["Ethernet1"].keys()) == {"inputErrors", "fcsErrors"}


def test_get_ip_interfaces_fields_filter(mock_conn_mgr, mock_node):
    """Field filtering on IP interfaces returns only requested fields."""
    mock_data = {
        "interfaces": {
            "Loopback0": {"interfaceAddress": {"ipAddr": {"address": "10.0.0.1"}}, "lineProtocolStatus": "up"},
            "Vlan100": {"interfaceAddress": {"ipAddr": {"address": "10.1.0.1"}}, "lineProtocolStatus": "up"},
        }
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_ip_interfaces("test-switch", fields=["lineProtocolStatus"])

    assert result["status"] == "success"
    assert set(result["data"]["Loopback0"].keys()) == {"lineProtocolStatus"}
    assert set(result["data"]["Vlan100"].keys()) == {"lineProtocolStatus"}


def test_get_transceiver_info_fields_filter(mock_conn_mgr, mock_node):
    """Field filtering on transceiver info returns only requested fields."""
    mock_node.run_commands.return_value = [MOCK_TRANSCEIVER_INFO]

    result = eos_get_transceiver_info("test-switch", fields=["mediaType", "rxPower"])

    assert result["status"] == "success"
    assert set(result["data"]["Ethernet1"].keys()) == {"mediaType", "rxPower"}


def test_get_transceiver_info_fields_nonexistent(mock_conn_mgr, mock_node):
    """Non-existent field names return empty dicts per entry."""
    mock_node.run_commands.return_value = [MOCK_TRANSCEIVER_INFO]

    result = eos_get_transceiver_info("test-switch", fields=["nonexistent"])

    assert result["status"] == "success"
    assert result["data"]["Ethernet1"] == {}


# --- Pagination tests ---


def test_get_interface_counters_pagination(mock_conn_mgr, mock_node):
    """Pagination on interface counters returns correct subset."""
    mock_data = {"interfaces": {f"Ethernet{i}": {"inOctets": i * 100, "outOctets": i * 200} for i in range(1, 11)}}
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_interface_counters("test-switch", limit=3, offset=0)

    assert result["status"] == "success"
    assert len(result["data"]) == 3
    assert "pagination" in result
    assert result["pagination"]["total"] == 10
    assert result["pagination"]["returned"] == 3
    assert result["pagination"]["has_more"] is True


def test_get_interface_errors_pagination(mock_conn_mgr, mock_node):
    """Pagination on interface errors returns correct subset."""
    mock_data = {"interfaceErrorCounters": {f"Ethernet{i}": {"inputErrors": 0, "outputErrors": 0} for i in range(1, 6)}}
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_interface_errors("test-switch", limit=2, offset=0)

    assert result["status"] == "success"
    assert len(result["data"]) == 2
    assert result["pagination"]["total"] == 5
    assert result["pagination"]["has_more"] is True


def test_get_ip_interfaces_pagination(mock_conn_mgr, mock_node):
    """Pagination on IP interfaces returns correct subset."""
    mock_data = {"interfaces": {f"Vlan{i}": {"lineProtocolStatus": "up"} for i in range(1, 9)}}
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_ip_interfaces("test-switch", limit=3, offset=2)

    assert result["status"] == "success"
    assert len(result["data"]) == 3
    assert result["pagination"]["total"] == 8
    assert result["pagination"]["offset"] == 2
    assert result["pagination"]["returned"] == 3


def test_get_interface_counters_fields_and_pagination(mock_conn_mgr, mock_node):
    """Both fields and pagination work together on interface counters."""
    mock_data = {
        "interfaces": {f"Ethernet{i}": {"inOctets": i * 100, "outOctets": i * 200, "extra": "val"} for i in range(1, 8)}
    }
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_interface_counters("test-switch", fields=["inOctets"], limit=2, offset=0)

    assert result["status"] == "success"
    assert len(result["data"]) == 2
    for intf_data in result["data"].values():
        assert set(intf_data.keys()) == {"inOctets"}
    assert result["pagination"]["total"] == 7
    assert result["pagination"]["returned"] == 2


def test_get_interface_counters_no_pagination(mock_conn_mgr, mock_node):
    """Without limit/offset, no pagination metadata is included."""
    mock_data = {"interfaces": {"Ethernet1": {"inOctets": 100}}}
    mock_node.run_commands.return_value = [mock_data]

    result = eos_get_interface_counters("test-switch")

    assert result["status"] == "success"
    assert "pagination" not in result


# --- Timeout and error scenario tests ---


def test_get_interfaces_status_timeout(mock_conn_mgr, mock_node_timeout):
    """Interface status returns error on timeout."""
    result = eos_get_interfaces_status("test-switch")

    assert result["status"] == "error"


def test_get_interface_detail_timeout(mock_conn_mgr, mock_node_timeout):
    """Interface detail returns error on timeout."""
    result = eos_get_interface_detail("test-switch", "Ethernet1")

    assert result["status"] == "error"


def test_get_interface_counters_timeout(mock_conn_mgr, mock_node_timeout):
    """Interface counters returns error on timeout."""
    result = eos_get_interface_counters("test-switch")

    assert result["status"] == "error"


def test_get_transceiver_info_timeout(mock_conn_mgr, mock_node_timeout):
    """Transceiver info returns error on timeout."""
    result = eos_get_transceiver_info("test-switch")

    assert result["status"] == "error"


def test_get_interface_errors_empty_response(mock_conn_mgr, mock_node_empty):
    """Empty response returns success with empty error counters."""
    result = eos_get_interface_errors("test-switch")

    assert result["status"] == "success"
    assert result["data"] == {}
