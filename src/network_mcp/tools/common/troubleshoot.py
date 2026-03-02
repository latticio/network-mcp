"""Vendor-agnostic troubleshooting tools (ping, traceroute, interface flaps)."""

from __future__ import annotations

import logging

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import READ_ONLY, handle_tool_errors, validate_cli_param, validate_ip_or_hostname
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_ping(host: str, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
    """Execute a ping from any vendor device to a destination.

    Returns packet statistics including loss percentage and RTT values.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        destination: IP address or hostname to ping.
        count: Number of ping packets (default 5, max 20).
        source: Source interface or IP (optional).
        vrf: VRF context (default: "default").
    """
    dest_err = validate_ip_or_hostname(destination, "destination")
    if dest_err:
        return {"status": "error", "device": host, "error": dest_err}
    if source:
        src_err = validate_cli_param(source, "source")
        if src_err:
            return {"status": "error", "device": host, "error": src_err}
    if count < 1 or count > 20:
        return {"status": "error", "device": host, "error": "count must be between 1 and 20"}
    if vrf:
        vrf_err = validate_cli_param(vrf, "vrf")
        if vrf_err:
            return {"status": "error", "device": host, "error": vrf_err}

    driver = conn_mgr.get_driver(host)
    try:
        ping_result = driver.ping(destination=destination, count=count, source=source, vrf=vrf)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"ping is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": ping_result,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_traceroute(host: str, destination: str, source: str = "", vrf: str = "default") -> dict:
    """Execute a traceroute from any vendor device to a destination.

    Returns hop-by-hop path with IP addresses and RTT values.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        destination: IP address or hostname to trace.
        source: Source interface or IP (optional).
        vrf: VRF context (default: "default").
    """
    dest_err = validate_ip_or_hostname(destination, "destination")
    if dest_err:
        return {"status": "error", "device": host, "error": dest_err}
    if source:
        src_err = validate_cli_param(source, "source")
        if src_err:
            return {"status": "error", "device": host, "error": src_err}
    if vrf:
        vrf_err = validate_cli_param(vrf, "vrf")
        if vrf_err:
            return {"status": "error", "device": host, "error": vrf_err}

    driver = conn_mgr.get_driver(host)
    try:
        trace_result = driver.traceroute(destination=destination, source=source, vrf=vrf)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"traceroute is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": trace_result,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_check_interface_flaps(host: str, hours: int = 24) -> dict:
    """Check for interface flap events in the last N hours on any vendor device.

    Returns a list of interface state-change events extracted from device logs.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        hours: Look-back period in hours (default 24, max 168).
    """
    if hours < 1 or hours > 168:
        return {"status": "error", "device": host, "error": "hours must be between 1 and 168"}

    driver = conn_mgr.get_driver(host)
    try:
        events = driver.get_interface_flaps(hours=hours)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_interface_flaps is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {
            "flap_events": events,
            "flap_count": len(events),
            "lookback_hours": hours,
        },
    }
