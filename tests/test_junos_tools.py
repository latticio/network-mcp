"""Tests for Juniper JunOS vendor-specific tools.

Tests the junos_get_routing_instances, junos_get_firewall_filters,
junos_get_system_alarms, junos_get_commit_history, junos_compare_rollback,
junos_commit_confirmed, and junos_confirm_commit tools with mock JunOS data,
verifying normalization, error handling, platform validation, and read-only mode.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from network_mcp.server import conn_mgr
from network_mcp.tools.juniper.junos import (
    _normalize_commit_history,
    _normalize_firewall_filters,
    _normalize_rollback_compare,
    _normalize_routing_instances,
    _normalize_system_alarms,
    junos_commit_confirmed,
    junos_compare_rollback,
    junos_confirm_commit,
    junos_get_commit_history,
    junos_get_firewall_filters,
    junos_get_routing_instances,
    junos_get_system_alarms,
)
from tests.mock_data.eos.mock_driver import MockEosDriver
from tests.mock_data.iosxe.mock_driver import MockIosXeDriver
from tests.mock_data.junos.mock_driver import MockJunosDriver


@pytest.fixture
def junos_driver():
    """Create a connected MockJunosDriver."""
    driver = MockJunosDriver()
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


# --- junos_get_routing_instances tests ---


class TestJunosGetRoutingInstances:
    """Test junos_get_routing_instances tool."""

    def test_success(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_routing_instances("junos-spine-01")
        assert result["status"] == "success"
        assert result["device"] == "junos-spine-01"

    def test_instance_count(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_routing_instances("junos-spine-01")
        data = result["data"]
        assert data["instance_count"] == 3
        assert len(data["instances"]) == 3

    def test_master_instance(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_routing_instances("junos-spine-01")
        instances = result["data"]["instances"]
        master = next(i for i in instances if i["name"] == "master")
        assert master["type"] == "forwarding"
        assert master["state"] == "Active"
        assert master["route_count"] == 25

    def test_vrf_instance(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_routing_instances("junos-spine-01")
        instances = result["data"]["instances"]
        customer_a = next(i for i in instances if i["name"] == "CUSTOMER-A")
        assert customer_a["type"] == "vrf"
        assert len(customer_a["interfaces"]) == 2
        assert "ge-0/0/1.100" in customer_a["interfaces"]
        assert customer_a["route_count"] == 12

    def test_virtual_router_instance(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_routing_instances("junos-spine-01")
        instances = result["data"]["instances"]
        mgmt = next(i for i in instances if i["name"] == "MGMT")
        assert mgmt["type"] == "virtual-router"
        assert "fxp0.0" in mgmt["interfaces"]
        assert mgmt["route_count"] == 3

    def test_error_on_non_junos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = junos_get_routing_instances("eos-spine-01")
        assert result["status"] == "error"
        assert "JunOS" in result["error"]

    def test_error_on_iosxe_device(self, iosxe_driver):
        with patch.object(conn_mgr, "get_driver", return_value=iosxe_driver):
            result = junos_get_routing_instances("csr1000v-01")
        assert result["status"] == "error"
        assert "JunOS" in result["error"]

    def test_error_on_empty_host(self):
        result = junos_get_routing_instances("")
        assert result["status"] == "error"
        assert "Host is required" in result["error"]

    def test_host_injection_rejected(self):
        result = junos_get_routing_instances("host;rm -rf /")
        assert result["status"] == "error"

    def test_data_structure_types(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_routing_instances("junos-spine-01")
        data = result["data"]
        assert isinstance(data["instances"], list)
        assert isinstance(data["instance_count"], int)
        for inst in data["instances"]:
            assert isinstance(inst["name"], str)
            assert isinstance(inst["type"], str)
            assert isinstance(inst["interfaces"], list)
            assert isinstance(inst["route_count"], int)


# --- junos_get_firewall_filters tests ---


class TestJunosGetFirewallFilters:
    """Test junos_get_firewall_filters tool."""

    def test_success(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_firewall_filters("junos-spine-01")
        assert result["status"] == "success"
        assert result["device"] == "junos-spine-01"

    def test_filter_count(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_firewall_filters("junos-spine-01")
        data = result["data"]
        assert data["filter_count"] == 2
        assert len(data["filters"]) == 2

    def test_protect_re_filter(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_firewall_filters("junos-spine-01")
        filters = result["data"]["filters"]
        protect_re = next(f for f in filters if f["name"] == "PROTECT-RE")
        assert protect_re["term_count"] == 3
        assert len(protect_re["terms"]) == 3

    def test_filter_term_fields(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_firewall_filters("junos-spine-01")
        filters = result["data"]["filters"]
        protect_re = next(f for f in filters if f["name"] == "PROTECT-RE")
        ssh_term = next(t for t in protect_re["terms"] if t["name"] == "allow-ssh")
        assert ssh_term["packet_count"] == 15432
        assert ssh_term["byte_count"] == 1234560

    def test_customer_ingress_filter(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_firewall_filters("junos-spine-01")
        filters = result["data"]["filters"]
        customer = next(f for f in filters if f["name"] == "CUSTOMER-INGRESS")
        assert customer["term_count"] == 2

    def test_error_on_non_junos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = junos_get_firewall_filters("eos-spine-01")
        assert result["status"] == "error"
        assert "JunOS" in result["error"]

    def test_error_on_empty_host(self):
        result = junos_get_firewall_filters("")
        assert result["status"] == "error"

    def test_host_injection_rejected(self):
        result = junos_get_firewall_filters("host|cat /etc/passwd")
        assert result["status"] == "error"

    def test_data_structure_types(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_firewall_filters("junos-spine-01")
        data = result["data"]
        assert isinstance(data["filters"], list)
        assert isinstance(data["filter_count"], int)
        for f in data["filters"]:
            assert isinstance(f["name"], str)
            assert isinstance(f["terms"], list)
            assert isinstance(f["term_count"], int)


# --- junos_get_system_alarms tests ---


class TestJunosGetSystemAlarms:
    """Test junos_get_system_alarms tool."""

    def test_success(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_system_alarms("junos-spine-01")
        assert result["status"] == "success"
        assert result["device"] == "junos-spine-01"

    def test_alarm_count(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_system_alarms("junos-spine-01")
        data = result["data"]
        assert data["alarm_count"] == 2
        assert len(data["alarms"]) == 2

    def test_major_alarm(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_system_alarms("junos-spine-01")
        alarms = result["data"]["alarms"]
        major = next(a for a in alarms if a["class"] == "Major")
        assert "Power Supply" in major["description"]
        assert major["type"] == "PSU failure"
        assert "2025-01-15" in major["time"]

    def test_minor_alarm(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_system_alarms("junos-spine-01")
        alarms = result["data"]["alarms"]
        minor = next(a for a in alarms if a["class"] == "Minor")
        assert "Rescue configuration" in minor["description"]

    def test_empty_alarms(self, junos_driver):
        """Test with no alarms active."""
        empty_xml = (
            "<alarm-information><alarm-summary>"
            "<active-alarm-count>0</active-alarm-count>"
            "</alarm-summary></alarm-information>"
        )
        junos_driver.run_show = lambda cmds, **kw: [{"output": empty_xml}]
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_system_alarms("junos-spine-01")
        assert result["status"] == "success"
        assert result["data"]["alarm_count"] == 0

    def test_error_on_non_junos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = junos_get_system_alarms("eos-spine-01")
        assert result["status"] == "error"
        assert "JunOS" in result["error"]

    def test_error_on_empty_host(self):
        result = junos_get_system_alarms("")
        assert result["status"] == "error"

    def test_data_structure_types(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_system_alarms("junos-spine-01")
        data = result["data"]
        assert isinstance(data["alarms"], list)
        assert isinstance(data["alarm_count"], int)
        for alarm in data["alarms"]:
            assert isinstance(alarm["class"], str)
            assert isinstance(alarm["time"], str)
            assert isinstance(alarm["description"], str)


# --- junos_get_commit_history tests ---


class TestJunosGetCommitHistory:
    """Test junos_get_commit_history tool."""

    def test_success(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_commit_history("junos-spine-01")
        assert result["status"] == "success"
        assert result["device"] == "junos-spine-01"

    def test_commit_count(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_commit_history("junos-spine-01")
        data = result["data"]
        assert data["commit_count"] == 3
        assert len(data["commits"]) == 3

    def test_latest_commit(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_commit_history("junos-spine-01")
        commits = result["data"]["commits"]
        latest = commits[0]
        assert latest["sequence"] == 0
        assert latest["user"] == "admin"
        assert latest["client"] == "cli"
        assert "Updated BGP" in latest["comment"]

    def test_netconf_commit(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_commit_history("junos-spine-01")
        commits = result["data"]["commits"]
        netconf_commit = commits[1]
        assert netconf_commit["client"] == "netconf"
        assert netconf_commit["user"] == "netops"

    def test_commit_without_comment(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_commit_history("junos-spine-01")
        commits = result["data"]["commits"]
        no_comment = commits[2]
        assert no_comment["comment"] == ""

    def test_custom_count_parameter(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_commit_history("junos-spine-01", count=5)
        assert result["status"] == "success"

    def test_count_too_low(self):
        result = junos_get_commit_history("junos-spine-01", count=0)
        assert result["status"] == "error"
        assert "count must be between" in result["error"]

    def test_count_too_high(self):
        result = junos_get_commit_history("junos-spine-01", count=51)
        assert result["status"] == "error"
        assert "count must be between" in result["error"]

    def test_error_on_non_junos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = junos_get_commit_history("eos-spine-01")
        assert result["status"] == "error"
        assert "JunOS" in result["error"]

    def test_error_on_empty_host(self):
        result = junos_get_commit_history("")
        assert result["status"] == "error"

    def test_data_structure_types(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_commit_history("junos-spine-01")
        data = result["data"]
        assert isinstance(data["commits"], list)
        assert isinstance(data["commit_count"], int)
        for commit in data["commits"]:
            assert isinstance(commit["sequence"], int)
            assert isinstance(commit["user"], str)
            assert isinstance(commit["client"], str)
            assert isinstance(commit["date_time"], str)
            assert isinstance(commit["comment"], str)


# --- junos_compare_rollback tests ---


class TestJunosCompareRollback:
    """Test junos_compare_rollback tool."""

    def test_success(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_compare_rollback("junos-spine-01")
        assert result["status"] == "success"
        assert result["device"] == "junos-spine-01"

    def test_diff_has_changes(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_compare_rollback("junos-spine-01")
        data = result["data"]
        assert data["has_changes"] is True
        assert data["additions"] > 0
        assert data["deletions"] > 0

    def test_diff_content(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_compare_rollback("junos-spine-01")
        data = result["data"]
        assert "neighbor 10.0.0.6" in data["diff"]
        assert "protocols bgp" in data["diff"]

    def test_rollback_id_parameter(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_compare_rollback("junos-spine-01", rollback_id=5)
        assert result["status"] == "success"

    def test_rollback_id_zero(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_compare_rollback("junos-spine-01", rollback_id=0)
        assert result["status"] == "success"

    def test_rollback_id_too_low(self):
        result = junos_compare_rollback("junos-spine-01", rollback_id=-1)
        assert result["status"] == "error"
        assert "rollback_id must be between" in result["error"]

    def test_rollback_id_too_high(self):
        result = junos_compare_rollback("junos-spine-01", rollback_id=50)
        assert result["status"] == "error"
        assert "rollback_id must be between" in result["error"]

    def test_no_changes(self, junos_driver):
        """Test when rollback comparison shows no diff."""
        junos_driver.run_show = lambda cmds, **kw: [{"output": ""}]
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_compare_rollback("junos-spine-01")
        assert result["status"] == "success"
        assert result["data"]["has_changes"] is False
        assert result["data"]["additions"] == 0
        assert result["data"]["deletions"] == 0

    def test_error_on_non_junos_device(self, eos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=eos_driver):
            result = junos_compare_rollback("eos-spine-01")
        assert result["status"] == "error"
        assert "JunOS" in result["error"]

    def test_error_on_empty_host(self):
        result = junos_compare_rollback("")
        assert result["status"] == "error"

    def test_data_structure_types(self, junos_driver):
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_compare_rollback("junos-spine-01")
        data = result["data"]
        assert isinstance(data["diff"], str)
        assert isinstance(data["additions"], int)
        assert isinstance(data["deletions"], int)
        assert isinstance(data["has_changes"], bool)


# --- junos_commit_confirmed tests ---


class TestJunosCommitConfirmed:
    """Test junos_commit_confirmed tool."""

    def test_success(self, junos_driver):
        with (
            patch.object(conn_mgr, "get_driver", return_value=junos_driver),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_commit_confirmed("junos-spine-01")
        assert result["status"] == "success"
        assert result["device"] == "junos-spine-01"

    def test_default_timeout(self, junos_driver):
        with (
            patch.object(conn_mgr, "get_driver", return_value=junos_driver),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_commit_confirmed("junos-spine-01")
        data = result["data"]
        assert data["rollback_minutes"] == 10
        assert data["confirmed"] is False

    def test_custom_timeout(self, junos_driver):
        with (
            patch.object(conn_mgr, "get_driver", return_value=junos_driver),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_commit_confirmed("junos-spine-01", minutes=30)
        data = result["data"]
        assert data["rollback_minutes"] == 30

    def test_with_comment(self, junos_driver):
        with (
            patch.object(conn_mgr, "get_driver", return_value=junos_driver),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_commit_confirmed("junos-spine-01", comment="test change")
        data = result["data"]
        assert data["comment"] == "test change"

    def test_minutes_too_low(self):
        with patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None):
            result = junos_commit_confirmed("junos-spine-01", minutes=0)
        assert result["status"] == "error"
        assert "minutes must be between" in result["error"]

    def test_minutes_too_high(self):
        with patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None):
            result = junos_commit_confirmed("junos-spine-01", minutes=61)
        assert result["status"] == "error"
        assert "minutes must be between" in result["error"]

    def test_comment_injection_rejected(self):
        with patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None):
            result = junos_commit_confirmed("junos-spine-01", comment="test;rm -rf /")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]

    def test_comment_pipe_injection_rejected(self):
        with patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None):
            result = junos_commit_confirmed("junos-spine-01", comment="test|cat /etc/passwd")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]

    def test_read_only_mode_blocked(self, junos_driver):
        with patch(
            "network_mcp.tools.juniper.junos.check_read_only",
            return_value="Write operations are disabled. Set NET_READ_ONLY=false",
        ):
            result = junos_commit_confirmed("junos-spine-01")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    def test_error_on_non_junos_device(self, eos_driver):
        with (
            patch.object(conn_mgr, "get_driver", return_value=eos_driver),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_commit_confirmed("eos-spine-01")
        assert result["status"] == "error"
        assert "JunOS" in result["error"]

    def test_error_on_empty_host(self):
        with patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None):
            result = junos_commit_confirmed("")
        assert result["status"] == "error"

    def test_host_injection_rejected(self):
        with patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None):
            result = junos_commit_confirmed("host`whoami`")
        assert result["status"] == "error"


# --- junos_confirm_commit tests ---


class TestJunosConfirmCommit:
    """Test junos_confirm_commit tool."""

    def test_success(self, junos_driver):
        with (
            patch.object(conn_mgr, "get_driver", return_value=junos_driver),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_confirm_commit("junos-spine-01")
        assert result["status"] == "success"
        assert result["device"] == "junos-spine-01"

    def test_confirmed_flag(self, junos_driver):
        with (
            patch.object(conn_mgr, "get_driver", return_value=junos_driver),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_confirm_commit("junos-spine-01")
        data = result["data"]
        assert data["confirmed"] is True
        assert "rollback timer cancelled" in data["message"]

    def test_read_only_mode_blocked(self, junos_driver):
        with patch(
            "network_mcp.tools.juniper.junos.check_read_only",
            return_value="Write operations are disabled. Set NET_READ_ONLY=false",
        ):
            result = junos_confirm_commit("junos-spine-01")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    def test_error_on_non_junos_device(self, eos_driver):
        with (
            patch.object(conn_mgr, "get_driver", return_value=eos_driver),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_confirm_commit("eos-spine-01")
        assert result["status"] == "error"
        assert "JunOS" in result["error"]

    def test_error_on_empty_host(self):
        with patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None):
            result = junos_confirm_commit("")
        assert result["status"] == "error"


# --- Error path tests ---


class TestJunosErrorPaths:
    """Test JunOS tool error handling and edge cases."""

    def test_empty_response_routing_instances(self, junos_driver):
        junos_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_routing_instances("junos-spine-01")
        assert result["status"] == "error"
        assert "Failed to get routing instances" in result["error"]

    def test_error_in_response_firewall(self, junos_driver):
        junos_driver.run_show = lambda cmds, **kw: [{"error": "NETCONF session closed"}]
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_firewall_filters("junos-spine-01")
        assert result["status"] == "error"
        assert "NETCONF session closed" in result["error"]

    def test_empty_response_alarms(self, junos_driver):
        junos_driver.run_show = lambda cmds, **kw: []
        with patch.object(conn_mgr, "get_driver", return_value=junos_driver):
            result = junos_get_system_alarms("junos-spine-01")
        assert result["status"] == "error"

    def test_exception_during_routing_instances(self):
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("Connection lost")):
            result = junos_get_routing_instances("junos-spine-01")
        assert result["status"] == "error"
        assert "Connection lost" in result["error"]

    def test_exception_during_firewall_filters(self):
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = junos_get_firewall_filters("junos-spine-01")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_exception_during_system_alarms(self):
        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("refused")):
            result = junos_get_system_alarms("junos-spine-01")
        assert result["status"] == "error"

    def test_exception_during_commit_history(self):
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = junos_get_commit_history("junos-spine-01")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_exception_during_compare_rollback(self):
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = junos_compare_rollback("junos-spine-01")
        assert result["status"] == "error"

    def test_exception_during_commit_confirmed(self):
        with (
            patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_commit_confirmed("junos-spine-01")
        assert result["status"] == "error"

    def test_exception_during_confirm_commit(self):
        with (
            patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timeout")),
            patch("network_mcp.tools.juniper.junos.check_read_only", return_value=None),
        ):
            result = junos_confirm_commit("junos-spine-01")
        assert result["status"] == "error"


# --- Normalization function tests ---


class TestRoutingInstancesNormalization:
    """Test _normalize_routing_instances with various input formats."""

    def test_empty_xml(self):
        result = _normalize_routing_instances("")
        assert result["instances"] == []
        assert result["instance_count"] == 0

    def test_invalid_xml(self):
        result = _normalize_routing_instances("<not-valid>")
        assert result["instances"] == []
        assert result["instance_count"] == 0

    def test_empty_instances(self):
        xml = "<instance-information></instance-information>"
        result = _normalize_routing_instances(xml)
        assert result["instance_count"] == 0


class TestFirewallFiltersNormalization:
    """Test _normalize_firewall_filters with various input formats."""

    def test_empty_xml(self):
        result = _normalize_firewall_filters("")
        assert result["filters"] == []
        assert result["filter_count"] == 0

    def test_invalid_xml(self):
        result = _normalize_firewall_filters("<broken")
        assert result["filters"] == []

    def test_filter_without_counters(self):
        xml = """<firewall-information>
            <filter-information>
                <filter-name>EMPTY-FILTER</filter-name>
            </filter-information>
        </firewall-information>"""
        result = _normalize_firewall_filters(xml)
        assert result["filter_count"] == 1
        assert result["filters"][0]["term_count"] == 0


class TestSystemAlarmsNormalization:
    """Test _normalize_system_alarms with various input formats."""

    def test_empty_xml(self):
        result = _normalize_system_alarms("")
        assert result["alarms"] == []
        assert result["alarm_count"] == 0

    def test_no_alarms(self):
        xml = (
            "<alarm-information><alarm-summary>"
            "<active-alarm-count>0</active-alarm-count>"
            "</alarm-summary></alarm-information>"
        )
        result = _normalize_system_alarms(xml)
        assert result["alarm_count"] == 0

    def test_invalid_xml(self):
        result = _normalize_system_alarms("not xml at all")
        assert result["alarms"] == []


class TestCommitHistoryNormalization:
    """Test _normalize_commit_history with various input formats."""

    def test_empty_xml(self):
        result = _normalize_commit_history("")
        assert result["commits"] == []
        assert result["commit_count"] == 0

    def test_invalid_xml(self):
        result = _normalize_commit_history("garbage")
        assert result["commits"] == []

    def test_single_commit(self):
        xml = """<commit-information>
            <commit-history>
                <sequence-number>0</sequence-number>
                <user>root</user>
                <client>cli</client>
                <date-time>2025-01-01 00:00:00 UTC</date-time>
                <log>initial config</log>
            </commit-history>
        </commit-information>"""
        result = _normalize_commit_history(xml)
        assert result["commit_count"] == 1
        assert result["commits"][0]["user"] == "root"
        assert result["commits"][0]["comment"] == "initial config"


class TestRollbackCompareNormalization:
    """Test _normalize_rollback_compare with various input formats."""

    def test_empty_diff(self):
        result = _normalize_rollback_compare("")
        assert result["has_changes"] is False
        assert result["additions"] == 0
        assert result["deletions"] == 0
        assert result["diff"] == ""

    def test_whitespace_only(self):
        result = _normalize_rollback_compare("   \n\n  ")
        assert result["has_changes"] is False

    def test_additions_only(self):
        diff = "+   new-line-1\n+   new-line-2\n"
        result = _normalize_rollback_compare(diff)
        assert result["additions"] == 2
        assert result["deletions"] == 0
        assert result["has_changes"] is True

    def test_deletions_only(self):
        diff = "-   old-line-1\n-   old-line-2\n-   old-line-3\n"
        result = _normalize_rollback_compare(diff)
        assert result["additions"] == 0
        assert result["deletions"] == 3
        assert result["has_changes"] is True

    def test_mixed_changes(self):
        diff = "+   added\n-   removed\n context line\n"
        result = _normalize_rollback_compare(diff)
        assert result["additions"] == 1
        assert result["deletions"] == 1

    def test_diff_header_not_counted(self):
        diff = "--- a/config\n+++ b/config\n+   added-line\n-   removed-line\n"
        result = _normalize_rollback_compare(diff)
        assert result["additions"] == 1
        assert result["deletions"] == 1


# --- Conditional loading tests ---


class TestJunosConditionalLoading:
    """Test that JunOS tools load conditionally based on scrapli availability."""

    def test_junos_tools_are_importable(self):
        from network_mcp.tools.juniper import junos  # noqa: F401

    def test_routing_instances_tool_registered(self):
        assert callable(junos_get_routing_instances)

    def test_firewall_filters_tool_registered(self):
        assert callable(junos_get_firewall_filters)

    def test_system_alarms_tool_registered(self):
        assert callable(junos_get_system_alarms)

    def test_commit_history_tool_registered(self):
        assert callable(junos_get_commit_history)

    def test_compare_rollback_tool_registered(self):
        assert callable(junos_compare_rollback)

    def test_commit_confirmed_tool_registered(self):
        assert callable(junos_commit_confirmed)

    def test_confirm_commit_tool_registered(self):
        assert callable(junos_confirm_commit)


# --- handle_tool_errors decorator tests ---


class TestHandleToolErrorsOnJunosTools:
    """Verify @handle_tool_errors wraps JunOS tools with standardized error dicts."""

    def test_routing_instances_timeout_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = junos_get_routing_instances("junos-spine-01")
        assert result["status"] == "error"
        assert result["device"] == "junos-spine-01"
        assert "Timeout" in result["error"]

    def test_firewall_connection_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("refused")):
            result = junos_get_firewall_filters("junos-spine-01")
        assert result["status"] == "error"

    def test_alarms_unexpected_error(self):
        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = junos_get_system_alarms("junos-spine-01")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]
        assert "junos-spine-01" in result["error"]
