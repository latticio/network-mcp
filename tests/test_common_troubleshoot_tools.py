"""Tests for vendor-agnostic troubleshooting tools.

Tests net_ping, net_traceroute, and net_check_interface_flaps. Validates
parameter validation, CLI injection prevention, vendor response parsing,
NotSupportedError handling, and standard response structure.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr

# --- Mock data ---

MOCK_PING_SUCCESS = {
    "success": True,
    "packets_sent": 5,
    "packets_received": 5,
    "packet_loss_pct": 0.0,
    "rtt_min": 0.5,
    "rtt_avg": 1.2,
    "rtt_max": 2.1,
    "output": "5 packets transmitted, 5 received, 0% packet loss\nrtt min/avg/max = 0.5/1.2/2.1 ms",
}

MOCK_PING_PARTIAL_LOSS = {
    "success": True,
    "packets_sent": 5,
    "packets_received": 3,
    "packet_loss_pct": 40.0,
    "rtt_min": 1.0,
    "rtt_avg": 2.0,
    "rtt_max": 3.0,
    "output": "5 packets transmitted, 3 received, 40% packet loss\nrtt min/avg/max = 1.0/2.0/3.0 ms",
}

MOCK_PING_FAILURE = {
    "success": False,
    "packets_sent": 5,
    "packets_received": 0,
    "packet_loss_pct": 100.0,
    "rtt_min": 0,
    "rtt_avg": 0,
    "rtt_max": 0,
    "output": "5 packets transmitted, 0 received, 100% packet loss",
}

MOCK_TRACEROUTE_SUCCESS = {
    "hops": [
        {"hop": 1, "address": "10.0.0.1", "rtt_ms": 1.2},
        {"hop": 2, "address": "10.0.1.1", "rtt_ms": 2.5},
        {"hop": 3, "address": "10.0.2.1", "rtt_ms": 5.3},
    ],
    "output": "traceroute to 10.0.2.1\n 1  10.0.0.1  1.2 ms\n 2  10.0.1.1  2.5 ms\n 3  10.0.2.1  5.3 ms",
}

MOCK_TRACEROUTE_WITH_TIMEOUT = {
    "hops": [
        {"hop": 1, "address": "10.0.0.1", "rtt_ms": 1.2},
        {"hop": 2, "address": "*", "rtt_ms": 0.0},
        {"hop": 3, "address": "10.0.2.1", "rtt_ms": 5.3},
    ],
    "output": "traceroute to 10.0.2.1\n 1  10.0.0.1  1.2 ms\n 2  * * *\n 3  10.0.2.1  5.3 ms",
}

MOCK_FLAP_EVENTS = [
    {"interface": "Ethernet1", "event": "link down", "timestamp": "Jan  5 10:15:30"},
    {"interface": "Ethernet1", "event": "link up", "timestamp": "Jan  5 10:15:45"},
    {"interface": "Ethernet3", "event": "link down", "timestamp": "Jan  5 12:00:00"},
]


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with troubleshooting methods."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.ping.return_value = MOCK_PING_SUCCESS
    driver.traceroute.return_value = MOCK_TRACEROUTE_SUCCESS
    driver.get_interface_flaps.return_value = MOCK_FLAP_EVENTS
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# ===========================================================================
# net_ping
# ===========================================================================


class TestNetPing:
    """Test the net_ping common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_ping_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1")
        data = result["data"]
        assert data["success"] is True
        assert data["packets_sent"] == 5
        assert data["packets_received"] == 5
        assert data["packet_loss_pct"] == 0.0
        assert data["rtt_min"] == 0.5
        assert data["rtt_avg"] == 1.2
        assert data["rtt_max"] == 2.1

    def test_calls_driver_ping(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        net_ping("spine-01", "10.0.0.1")
        mock_driver.ping.assert_called_once_with(destination="10.0.0.1", count=5, source="", vrf="default")

    def test_custom_count(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        net_ping("spine-01", "10.0.0.1", count=10)
        mock_driver.ping.assert_called_once_with(destination="10.0.0.1", count=10, source="", vrf="default")

    def test_with_source(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        net_ping("spine-01", "10.0.0.1", source="Loopback0")
        mock_driver.ping.assert_called_once_with(destination="10.0.0.1", count=5, source="Loopback0", vrf="default")

    def test_with_vrf(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        net_ping("spine-01", "10.0.0.1", vrf="MGMT")
        mock_driver.ping.assert_called_once_with(destination="10.0.0.1", count=5, source="", vrf="MGMT")

    def test_partial_loss(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        mock_driver.ping.return_value = MOCK_PING_PARTIAL_LOSS
        result = net_ping("spine-01", "10.0.0.1")
        assert result["data"]["success"] is True
        assert result["data"]["packet_loss_pct"] == 40.0

    def test_total_failure(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        mock_driver.ping.return_value = MOCK_PING_FAILURE
        result = net_ping("spine-01", "10.0.0.1")
        assert result["data"]["success"] is False
        assert result["data"]["packet_loss_pct"] == 100.0

    def test_count_too_low(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1", count=0)
        assert result["status"] == "error"
        assert "count must be between 1 and 20" in result["error"]
        mock_driver.ping.assert_not_called()

    def test_count_too_high(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1", count=21)
        assert result["status"] == "error"
        assert "count must be between 1 and 20" in result["error"]
        mock_driver.ping.assert_not_called()

    def test_count_boundary_min(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1", count=1)
        assert result["status"] == "success"
        mock_driver.ping.assert_called_once()

    def test_count_boundary_max(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1", count=20)
        assert result["status"] == "success"
        mock_driver.ping.assert_called_once()

    def test_invalid_destination_injection_semicolon(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1; reload")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.ping.assert_not_called()

    def test_invalid_destination_injection_pipe(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1 | bash")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.ping.assert_not_called()

    def test_invalid_destination_injection_backtick(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "`rm -rf /`")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.ping.assert_not_called()

    def test_invalid_destination_injection_dollar(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "$(cat /etc/passwd)")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.ping.assert_not_called()

    def test_invalid_destination_empty(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "")
        assert result["status"] == "error"
        mock_driver.ping.assert_not_called()

    def test_invalid_source_injection(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1", source="Lo0; reload")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.ping.assert_not_called()

    def test_invalid_vrf_injection(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "10.0.0.1", vrf="MGMT; reload")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.ping.assert_not_called()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        mock_driver.ping.side_effect = NotSupportedError("not available")
        result = net_ping("spine-01", "10.0.0.1")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.troubleshoot import net_ping

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_ping("bad-host", "10.0.0.1")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.troubleshoot import net_ping

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_ping("slow-host", "10.0.0.1")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.troubleshoot import net_ping

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_ping("crash-host", "10.0.0.1")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_ping("iosxe-router", "10.0.0.1")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_ping("nxos-switch", "10.0.0.1")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_ping("junos-router", "10.0.0.1")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"

    def test_hostname_destination(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_ping

        result = net_ping("spine-01", "server-01.example.com")
        assert result["status"] == "success"
        mock_driver.ping.assert_called_once_with(destination="server-01.example.com", count=5, source="", vrf="default")


# ===========================================================================
# net_traceroute
# ===========================================================================


class TestNetTraceroute:
    """Test the net_traceroute common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        result = net_traceroute("spine-01", "10.0.2.1")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        result = net_traceroute("spine-01", "10.0.2.1")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_traceroute_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        result = net_traceroute("spine-01", "10.0.2.1")
        data = result["data"]
        assert "hops" in data
        assert "output" in data
        assert len(data["hops"]) == 3
        assert data["hops"][0]["hop"] == 1
        assert data["hops"][0]["address"] == "10.0.0.1"
        assert data["hops"][0]["rtt_ms"] == 1.2

    def test_calls_driver_traceroute(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        net_traceroute("spine-01", "10.0.2.1")
        mock_driver.traceroute.assert_called_once_with(destination="10.0.2.1", source="", vrf="default")

    def test_with_source(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        net_traceroute("spine-01", "10.0.2.1", source="Loopback0")
        mock_driver.traceroute.assert_called_once_with(destination="10.0.2.1", source="Loopback0", vrf="default")

    def test_with_vrf(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        net_traceroute("spine-01", "10.0.2.1", vrf="MGMT")
        mock_driver.traceroute.assert_called_once_with(destination="10.0.2.1", source="", vrf="MGMT")

    def test_traceroute_with_timeout_hops(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        mock_driver.traceroute.return_value = MOCK_TRACEROUTE_WITH_TIMEOUT
        result = net_traceroute("spine-01", "10.0.2.1")
        data = result["data"]
        assert data["hops"][1]["address"] == "*"
        assert data["hops"][1]["rtt_ms"] == 0.0

    def test_empty_hops(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        mock_driver.traceroute.return_value = {"hops": [], "output": "traceroute failed"}
        result = net_traceroute("spine-01", "10.0.2.1")
        assert result["status"] == "success"
        assert result["data"]["hops"] == []

    def test_invalid_destination_injection(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        result = net_traceroute("spine-01", "10.0.0.1; reload")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.traceroute.assert_not_called()

    def test_invalid_destination_empty(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        result = net_traceroute("spine-01", "")
        assert result["status"] == "error"
        mock_driver.traceroute.assert_not_called()

    def test_invalid_source_injection(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        result = net_traceroute("spine-01", "10.0.0.1", source="Lo0|bash")
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]
        mock_driver.traceroute.assert_not_called()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        mock_driver.traceroute.side_effect = NotSupportedError("not available")
        result = net_traceroute("spine-01", "10.0.0.1")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_traceroute("bad-host", "10.0.0.1")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_traceroute("slow-host", "10.0.0.1")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_traceroute

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_traceroute("iosxe-router", "10.0.0.1")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"


# ===========================================================================
# net_check_interface_flaps
# ===========================================================================


class TestNetCheckInterfaceFlaps:
    """Test the net_check_interface_flaps common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_flap_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01")
        data = result["data"]
        assert data["flap_count"] == 3
        assert data["lookback_hours"] == 24
        assert len(data["flap_events"]) == 3
        event = data["flap_events"][0]
        assert event["interface"] == "Ethernet1"
        assert event["event"] == "link down"
        assert event["timestamp"] == "Jan  5 10:15:30"

    def test_calls_driver_with_hours(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        net_check_interface_flaps("spine-01", hours=48)
        mock_driver.get_interface_flaps.assert_called_once_with(hours=48)

    def test_default_hours(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        net_check_interface_flaps("spine-01")
        mock_driver.get_interface_flaps.assert_called_once_with(hours=24)

    def test_no_flaps(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        mock_driver.get_interface_flaps.return_value = []
        result = net_check_interface_flaps("spine-01")
        assert result["status"] == "success"
        assert result["data"]["flap_count"] == 0
        assert result["data"]["flap_events"] == []

    def test_hours_too_low(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01", hours=0)
        assert result["status"] == "error"
        assert "hours must be between 1 and 168" in result["error"]
        mock_driver.get_interface_flaps.assert_not_called()

    def test_hours_too_high(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01", hours=200)
        assert result["status"] == "error"
        assert "hours must be between 1 and 168" in result["error"]
        mock_driver.get_interface_flaps.assert_not_called()

    def test_hours_boundary_min(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01", hours=1)
        assert result["status"] == "success"

    def test_hours_boundary_max(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01", hours=168)
        assert result["status"] == "success"

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        mock_driver.get_interface_flaps.side_effect = NotSupportedError("not available")
        result = net_check_interface_flaps("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_check_interface_flaps("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_check_interface_flaps("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_check_interface_flaps("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_check_interface_flaps("iosxe-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_check_interface_flaps("junos-switch")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"

    def test_link_up_event(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01")
        events = result["data"]["flap_events"]
        assert events[1]["event"] == "link up"

    def test_lookback_hours_in_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.troubleshoot import net_check_interface_flaps

        result = net_check_interface_flaps("spine-01", hours=48)
        assert result["data"]["lookback_hours"] == 48
