"""Vendor-agnostic switching tools."""

from __future__ import annotations

import logging

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import READ_ONLY, handle_tool_errors, paginate_list
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_lldp_neighbors(host: str) -> dict:
    """Get LLDP neighbor information from any supported network device.

    Returns discovered neighbors per interface with hostname, port, and
    system description. Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS,
    and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    neighbors = driver.get_lldp_neighbors()
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": neighbors,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_mac_table(host: str, vlan: int = 0, interface: str = "", page: int = 1, page_size: int = 100) -> dict:
    """Get MAC address table from any vendor device. Supports pagination and filtering.

    Returns MAC address, VLAN, interface, and entry type (dynamic/static).
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        vlan: Filter by VLAN ID (0 = all VLANs).
        interface: Filter by interface name (empty = all).
        page: Page number (default 1).
        page_size: Results per page (default 100, range 1-500).
    """
    if page < 1:
        return {"status": "error", "device": host, "error": "page must be >= 1"}
    if page_size < 1 or page_size > 500:
        return {"status": "error", "device": host, "error": "page_size must be between 1 and 500"}

    driver = conn_mgr.get_driver(host)
    try:
        vlan_filter = vlan if vlan > 0 else None
        entries = driver.get_mac_table(vlan=vlan_filter, limit=10000)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_mac_table is not supported on {driver.platform}",
        }

    # Apply interface filter on the tool side (drivers don't support it)
    if interface:
        entries = [e for e in entries if e.get("interface", "") == interface]

    offset = (page - 1) * page_size
    page_data, pagination = paginate_list(entries, limit=page_size, offset=offset)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": page_data,
        "pagination": pagination,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_stp_status(host: str) -> dict:
    """Get spanning tree status from any vendor device.

    Returns STP mode, root bridge, port states, and topology info.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        stp = driver.get_stp_status()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_stp_status is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": stp,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_port_channels(host: str) -> dict:
    """Get LAG/port-channel status from any vendor device.

    Returns port-channel name, protocol (LACP/static), member interfaces, and status.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        port_channels = driver.get_port_channels()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_port_channels is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": port_channels,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_lldp_neighbor_detail(host: str, interface: str = "") -> dict:
    """Get detailed LLDP neighbor TLV data from any vendor device.

    Returns extended LLDP information including chassis ID, management address,
    and system capabilities. Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS,
    and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        interface: Specific interface (empty = all interfaces).
    """
    driver = conn_mgr.get_driver(host)
    try:
        detail = driver.get_lldp_neighbor_detail(interface=interface)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_lldp_neighbor_detail is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": detail,
    }
