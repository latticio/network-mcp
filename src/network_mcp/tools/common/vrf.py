"""Vendor-agnostic VRF (Virtual Routing and Forwarding) tools."""

from __future__ import annotations

import logging

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import READ_ONLY, handle_tool_errors, validate_vrf_name
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_vrf_list(host: str) -> dict:
    """Get list of VRFs configured on any vendor device.

    Returns VRF name, route distinguisher, interfaces, and route counts.
    On JunOS, returns routing instances (the JunOS equivalent of VRFs).
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        vrfs = driver.get_vrfs()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_vrfs is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": vrfs,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_vrf_detail(host: str, vrf: str) -> dict:
    """Get detailed VRF information from any vendor device.

    Returns VRF name, route distinguisher, assigned interfaces, route counts,
    and route targets. On JunOS, accepts routing instance names.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        vrf: VRF name (or routing instance name on JunOS).
    """
    vrf_err = validate_vrf_name(vrf)
    if vrf_err:
        return {"status": "error", "device": host, "error": vrf_err}

    driver = conn_mgr.get_driver(host)
    try:
        detail = driver.get_vrf_detail(vrf=vrf)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_vrf_detail is not supported on {driver.platform}",
        }
    if not detail:
        return {
            "status": "error",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"VRF '{vrf}' not found on {host}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": detail,
    }
