"""Event Monitor tools for Arista EOS.

Arista's Event Monitor records system events (MAC moves, route changes, ARP updates) to a local
SQLite database. These tools provide access to that data for troubleshooting.
Note: Event Monitor must be enabled on the device (`event-monitor` in config).
"""

from typing import Any

from network_mcp.helpers import READ_ONLY, run_show_command
from network_mcp.server import conn_mgr, mcp


def _truncate_events(data: dict[str, Any], limit: int) -> dict[str, Any]:
    """Truncate list values in event-monitor output to the given limit."""
    truncated = {}
    for key, value in data.items():
        if isinstance(value, list):
            truncated[key] = value[:limit]
        elif isinstance(value, dict):
            # Event data may be nested as {"events": [...]} or {"<key>": [...]}
            truncated[key] = _truncate_events(value, limit)  # type: ignore[assignment]
        else:
            truncated[key] = value
    return truncated


@mcp.tool(annotations=READ_ONLY)
def eos_get_event_monitor(host: str, event_type: str | None = None, limit: int = 100) -> dict:
    """Get recent system events from the Arista EOS Event Monitor.

    Event Monitor records MAC moves, route changes, ARP updates, and other system events.
    Returns recent events of the specified type, or all events if no type is given.
    Note: Event Monitor must be enabled on the device.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        event_type: Optional event type filter (e.g., 'mac', 'route', 'arp'). Returns all events if not set.
        limit: Maximum number of events to return (default: 100).
    """
    VALID_EVENT_TYPES = ("mac", "route", "arp", "link", "all")
    if event_type is not None:
        if event_type not in VALID_EVENT_TYPES:
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid event_type '{event_type}'. Valid types: {', '.join(VALID_EVENT_TYPES)}",
            }
    if event_type:
        cmd = f"show event-monitor {event_type}"
    else:
        cmd = "show event-monitor"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": _truncate_events(result["data"][0], limit),
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_mac_move_events(host: str, limit: int = 50) -> dict:
    """Get MAC address move events from Arista EOS Event Monitor.

    MAC moves (a MAC address appearing on a different port) are the #1 indicator of L2 loops,
    misconfiguration, or VM migration. This tool retrieves recent MAC move events with
    timestamps, VLAN, and old/new port information.
    Note: Event Monitor must be enabled on the device.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        limit: Maximum number of events to return (default: 50).
    """
    result = run_show_command(conn_mgr, host, ["show event-monitor mac"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": _truncate_events(result["data"][0], limit),
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_route_change_events(host: str, limit: int = 50) -> dict:
    """Get route change events from Arista EOS Event Monitor.

    Returns recent routing table changes including route additions, withdrawals, and next-hop
    changes. Useful for diagnosing route flaps and understanding when routes changed.
    Note: Event Monitor must be enabled on the device.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        limit: Maximum number of events to return (default: 50).
    """
    result = run_show_command(conn_mgr, host, ["show event-monitor route"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": _truncate_events(result["data"][0], limit),
    }
