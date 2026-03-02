"""Tests for Event Monitor tools.

Covers:
- eos_get_event_monitor with mock data (all types and filtered)
- eos_get_mac_move_events with mock data
- eos_get_route_change_events with mock data
- Event Monitor not enabled (command error)
- Empty events response
- Invalid event_type rejected
- Event truncation (limit parameter)
- Device unreachable (connection error, timeout)
"""

import pyeapi.eapilib

from network_mcp.tools.event_monitor import (
    _truncate_events,
    eos_get_event_monitor,
    eos_get_mac_move_events,
    eos_get_route_change_events,
)

# --- Mock Event Monitor responses ---

MOCK_EVENT_MONITOR_ALL = {
    "events": [
        {
            "timestamp": 1234567890.0,
            "type": "mac",
            "action": "add",
            "details": {"mac": "00:1c:73:aa:bb:cc", "vlan": 100, "port": "Ethernet1"},
        },
        {
            "timestamp": 1234567891.0,
            "type": "route",
            "action": "add",
            "details": {"prefix": "10.1.0.0/24", "nextHop": "10.0.0.2", "protocol": "bgp"},
        },
    ]
}

MOCK_EVENT_MONITOR_MAC = {
    "events": [
        {
            "timestamp": 1234567890.0,
            "type": "mac",
            "action": "move",
            "details": {
                "mac": "00:1c:73:aa:bb:cc",
                "vlan": 100,
                "oldPort": "Ethernet1",
                "newPort": "Ethernet2",
            },
        },
        {
            "timestamp": 1234567880.0,
            "type": "mac",
            "action": "add",
            "details": {"mac": "00:1c:73:dd:ee:ff", "vlan": 200, "port": "Ethernet3"},
        },
    ]
}

MOCK_EVENT_MONITOR_ROUTE = {
    "events": [
        {
            "timestamp": 1234567891.0,
            "type": "route",
            "action": "withdraw",
            "details": {"prefix": "10.1.0.0/24", "nextHop": "10.0.0.2", "protocol": "bgp"},
        },
        {
            "timestamp": 1234567892.0,
            "type": "route",
            "action": "add",
            "details": {"prefix": "10.2.0.0/24", "nextHop": "10.0.0.3", "protocol": "ospf"},
        },
    ]
}

MOCK_EVENT_MONITOR_EMPTY = {"events": []}


# --- Tests for get_event_monitor ---


def test_get_event_monitor_all(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_ALL]

    result = eos_get_event_monitor("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert len(result["data"]["events"]) == 2
    mock_node.run_commands.assert_called_once_with(["show event-monitor"], encoding="json")


def test_get_event_monitor_with_type(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_MAC]

    result = eos_get_event_monitor("test-switch", event_type="mac")

    assert result["status"] == "success"
    mock_node.run_commands.assert_called_once_with(["show event-monitor mac"], encoding="json")


def test_get_event_monitor_empty(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_EMPTY]

    result = eos_get_event_monitor("test-switch")

    assert result["status"] == "success"
    assert result["data"]["events"] == []


def test_get_event_monitor_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_event_monitor("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_event_monitor_command_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")

    result = eos_get_event_monitor("test-switch")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


# --- Tests for get_mac_move_events ---


def test_eos_get_mac_move_events(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_MAC]

    result = eos_get_mac_move_events("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert len(result["data"]["events"]) == 2
    assert result["data"]["events"][0]["details"]["oldPort"] == "Ethernet1"
    assert result["data"]["events"][0]["details"]["newPort"] == "Ethernet2"
    mock_node.run_commands.assert_called_once_with(["show event-monitor mac"], encoding="json")


def test_get_mac_move_events_empty(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_EMPTY]

    result = eos_get_mac_move_events("test-switch")

    assert result["status"] == "success"
    assert result["data"]["events"] == []


def test_get_mac_move_events_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_mac_move_events("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_mac_move_events_command_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")

    result = eos_get_mac_move_events("test-switch")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


# --- Tests for get_route_change_events ---


def test_eos_get_route_change_events(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_ROUTE]

    result = eos_get_route_change_events("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert len(result["data"]["events"]) == 2
    assert result["data"]["events"][0]["details"]["prefix"] == "10.1.0.0/24"
    mock_node.run_commands.assert_called_once_with(["show event-monitor route"], encoding="json")


def test_get_route_change_events_empty(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_EMPTY]

    result = eos_get_route_change_events("test-switch")

    assert result["status"] == "success"
    assert result["data"]["events"] == []


def test_get_route_change_events_connection_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "connection refused")

    result = eos_get_route_change_events("bad-host")

    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_get_route_change_events_command_error(mock_conn_mgr, mock_node):
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid command")

    result = eos_get_route_change_events("test-switch")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


# ===========================================================================
# Event Monitor Not Enabled
# ===========================================================================


def test_event_monitor_not_enabled(mock_conn_mgr, mock_node):
    """When Event Monitor is not enabled, device returns a command error."""
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(
        1000, "Event Monitor is not enabled. Enable with 'event-monitor' in config."
    )

    result = eos_get_event_monitor("test-switch")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


def test_mac_move_events_not_enabled(mock_conn_mgr, mock_node):
    """When Event Monitor is not enabled, mac move query returns command error."""
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Event Monitor is not enabled")

    result = eos_get_mac_move_events("test-switch")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


def test_route_change_events_not_enabled(mock_conn_mgr, mock_node):
    """When Event Monitor is not enabled, route change query returns command error."""
    mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Event Monitor is not enabled")

    result = eos_get_route_change_events("test-switch")

    assert result["status"] == "error"
    assert "Command error" in result["error"]


# ===========================================================================
# Invalid event_type Input Validation
# ===========================================================================


def test_get_event_monitor_invalid_type(mock_conn_mgr, mock_node):
    """Should reject invalid event types."""
    result = eos_get_event_monitor("test-switch", event_type="invalid_type")

    assert result["status"] == "error"
    assert "Invalid event_type" in result["error"]
    assert "invalid_type" in result["error"]
    mock_node.run_commands.assert_not_called()


def test_get_event_monitor_valid_types(mock_conn_mgr, mock_node):
    """Should accept all valid event types."""
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_EMPTY]

    for event_type in ("mac", "route", "arp", "link", "all"):
        result = eos_get_event_monitor("test-switch", event_type=event_type)
        assert result["status"] == "success", f"event_type '{event_type}' should be valid"


# ===========================================================================
# Event Truncation (limit parameter)
# ===========================================================================


MOCK_MANY_EVENTS = {
    "events": [
        {"timestamp": 1234567890.0 + i, "type": "mac", "action": "add", "details": {"mac": f"00:00:00:00:00:{i:02x}"}}
        for i in range(200)
    ]
}


def test_event_monitor_truncation_default_limit(mock_conn_mgr, mock_node):
    """Default limit should truncate events to 100."""
    mock_node.run_commands.return_value = [MOCK_MANY_EVENTS]

    result = eos_get_event_monitor("test-switch")

    assert result["status"] == "success"
    assert len(result["data"]["events"]) == 100


def test_event_monitor_truncation_custom_limit(mock_conn_mgr, mock_node):
    """Custom limit should truncate to the specified count."""
    mock_node.run_commands.return_value = [MOCK_MANY_EVENTS]

    result = eos_get_event_monitor("test-switch", limit=25)

    assert result["status"] == "success"
    assert len(result["data"]["events"]) == 25


def test_mac_move_events_truncation(mock_conn_mgr, mock_node):
    """Mac move events should respect the limit parameter."""
    mock_node.run_commands.return_value = [MOCK_MANY_EVENTS]

    result = eos_get_mac_move_events("test-switch", limit=10)

    assert result["status"] == "success"
    assert len(result["data"]["events"]) == 10


def test_route_change_events_truncation(mock_conn_mgr, mock_node):
    """Route change events should respect the limit parameter."""
    mock_node.run_commands.return_value = [MOCK_MANY_EVENTS]

    result = eos_get_route_change_events("test-switch", limit=5)

    assert result["status"] == "success"
    assert len(result["data"]["events"]) == 5


# ===========================================================================
# _truncate_events Helper
# ===========================================================================


def test_truncate_events_flat_list():
    """Should truncate flat list values."""
    data = {"events": list(range(50))}
    result = _truncate_events(data, 10)
    assert len(result["events"]) == 10
    assert result["events"] == list(range(10))


def test_truncate_events_nested_dict():
    """Should truncate lists in nested dicts."""
    data = {"category": {"events": list(range(50))}}
    result = _truncate_events(data, 5)
    assert len(result["category"]["events"]) == 5


def test_truncate_events_preserves_non_list_values():
    """Should preserve non-list values unchanged."""
    data = {"count": 42, "name": "test", "events": list(range(20))}
    result = _truncate_events(data, 5)
    assert result["count"] == 42
    assert result["name"] == "test"
    assert len(result["events"]) == 5


def test_truncate_events_limit_larger_than_list():
    """Should not add extra elements when limit exceeds list size."""
    data = {"events": [1, 2, 3]}
    result = _truncate_events(data, 100)
    assert result["events"] == [1, 2, 3]


# ===========================================================================
# Device Unreachable — Timeouts
# ===========================================================================


def test_event_monitor_generic_exception(mock_conn_mgr, mock_node):
    """Should return error on unexpected exceptions."""
    mock_node.run_commands.side_effect = RuntimeError("Something broke")

    result = eos_get_event_monitor("broken-switch")

    assert result["status"] == "error"
    assert "Unexpected error" in result["error"]


def test_mac_move_events_generic_exception(mock_conn_mgr, mock_node):
    """Should return error on unexpected exceptions for mac moves."""
    mock_node.run_commands.side_effect = RuntimeError("Something broke")

    result = eos_get_mac_move_events("broken-switch")

    assert result["status"] == "error"
    assert "Unexpected error" in result["error"]


def test_route_change_events_generic_exception(mock_conn_mgr, mock_node):
    """Should return error on unexpected exceptions for route changes."""
    mock_node.run_commands.side_effect = RuntimeError("Something broke")

    result = eos_get_route_change_events("broken-switch")

    assert result["status"] == "error"
    assert "Unexpected error" in result["error"]


# ===========================================================================
# Command Construction Verification
# ===========================================================================


def test_event_monitor_route_command(mock_conn_mgr, mock_node):
    """Should send 'show event-monitor route' for route type."""
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_EMPTY]

    eos_get_event_monitor("test-switch", event_type="route")

    mock_node.run_commands.assert_called_once_with(["show event-monitor route"], encoding="json")


def test_event_monitor_arp_command(mock_conn_mgr, mock_node):
    """Should send 'show event-monitor arp' for arp type."""
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_EMPTY]

    eos_get_event_monitor("test-switch", event_type="arp")

    mock_node.run_commands.assert_called_once_with(["show event-monitor arp"], encoding="json")


def test_event_monitor_link_command(mock_conn_mgr, mock_node):
    """Should send 'show event-monitor link' for link type."""
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_EMPTY]

    eos_get_event_monitor("test-switch", event_type="link")

    mock_node.run_commands.assert_called_once_with(["show event-monitor link"], encoding="json")


def test_event_monitor_none_type_command(mock_conn_mgr, mock_node):
    """Should send 'show event-monitor' when no type is specified."""
    mock_node.run_commands.return_value = [MOCK_EVENT_MONITOR_EMPTY]

    eos_get_event_monitor("test-switch", event_type=None)

    mock_node.run_commands.assert_called_once_with(["show event-monitor"], encoding="json")
