"""Vendor-agnostic device information tool."""

from __future__ import annotations

import logging

from network_mcp.helpers import READ_ONLY, handle_tool_errors
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_device_info(host: str) -> dict:
    """Get device information from any supported network device.

    Returns normalized device facts including hostname, model, version,
    serial number, and uptime. Works with Arista EOS, Cisco IOS-XE,
    Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    facts = driver.get_facts()
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": facts,
    }
