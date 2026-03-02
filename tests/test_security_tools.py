"""Tests for security visibility tools."""

import pyeapi.eapilib
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.tools.security import (
    eos_get_aaa_status,
    eos_get_copp_policy,
    eos_get_eapi_status,
    eos_get_ip_access_lists,
    eos_get_radius_status,
    eos_get_tacacs_status,
)
from tests.conftest import (
    MOCK_AAA,
    MOCK_COPP_POLICY,
    MOCK_EAPI_STATUS,
    MOCK_IP_ACCESS_LISTS,
    MOCK_RADIUS,
    MOCK_TACACS,
)

# --- get_ip_access_lists ---


def test_get_ip_access_lists_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_IP_ACCESS_LISTS]

    result = eos_get_ip_access_lists("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert len(result["data"]) == 1
    assert result["data"][0]["name"] == "MGMT-ACCESS"
    assert result["data"][0]["type"] == "Standard"
    mock_node.run_commands.assert_called_once_with(["show ip access-lists"], encoding="json")


def test_get_ip_access_lists_with_name(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_IP_ACCESS_LISTS]

    result = eos_get_ip_access_lists("test-switch", name="MGMT-ACCESS")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show ip access-lists MGMT-ACCESS"], encoding="json")


def test_get_ip_access_lists_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_ip_access_lists("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_ip_access_lists_invalid_host(mock_conn_mgr, mock_node):
    with pytest.raises(ToolError, match="Host is required"):
        eos_get_ip_access_lists("")


# --- get_aaa_status ---


def test_get_aaa_status_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_AAA]

    result = eos_get_aaa_status("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "authentication" in result["data"]
    assert "authorization" in result["data"]
    assert "accounting" in result["data"]
    mock_node.run_commands.assert_called_once_with(["show aaa"], encoding="json")


def test_get_aaa_status_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_aaa_status("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_radius_status ---


def test_get_radius_status_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_RADIUS]

    result = eos_get_radius_status("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "servers" in result["data"]
    mock_node.run_commands.assert_called_once_with(["show radius"], encoding="json")


def test_get_radius_status_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_radius_status("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_tacacs_status ---


def test_get_tacacs_status_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_TACACS]

    result = eos_get_tacacs_status("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert "servers" in result["data"]
    mock_node.run_commands.assert_called_once_with(["show tacacs"], encoding="json")


def test_get_tacacs_status_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_tacacs_status("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_copp_policy ---


def test_get_copp_policy_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_COPP_POLICY]

    result = eos_get_copp_policy("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"] == {}
    mock_node.run_commands.assert_called_once_with(["show policy-map interface control-plane"], encoding="json")


def test_get_copp_policy_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_copp_policy("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


# --- get_eapi_status ---


def test_get_eapi_status_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EAPI_STATUS]

    result = eos_get_eapi_status("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"]["enabled"] is True
    assert result["data"]["httpServer"]["running"] is True
    assert result["data"]["httpsServer"]["port"] == 443
    mock_node.run_commands.assert_called_once_with(["show management api http-commands"], encoding="json")


def test_get_eapi_status_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_eapi_status("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]
