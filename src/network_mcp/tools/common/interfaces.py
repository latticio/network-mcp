"""Vendor-agnostic interface tools."""

from __future__ import annotations

import logging

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    handle_tool_errors,
    paginate_list,
    run_config_command,
    validate_cli_param,
    validate_multi_vendor_interface_name,
)
from network_mcp.server import conn_mgr, mcp
from network_mcp.tools.common import _filter_fields

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_interfaces(host: str, fields: list[str] | None = None) -> dict:
    """Get interface status from any supported network device.

    Returns a normalized interface list with status, speed, MTU, and
    MAC address. Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS,
    and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        fields: Optional list of fields to include per interface
                (e.g., ["is_up", "speed", "description"]).
    """
    driver = conn_mgr.get_driver(host)
    interfaces = driver.get_interfaces()
    if fields:
        interfaces = _filter_fields(interfaces, fields)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": interfaces,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_interface_detail(host: str, interface: str) -> dict:
    """Get detailed information for a specific interface on any supported device.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        interface: Interface name in vendor-native format (e.g., Ethernet1, ge-0/0/0).
    """
    driver = conn_mgr.get_driver(host)
    interfaces = driver.get_interfaces()
    if interface not in interfaces:
        return {
            "status": "error",
            "device": host,
            "error": f"Interface '{interface}' not found. Available interfaces: {', '.join(sorted(interfaces.keys()))}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {interface: interfaces[interface]},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_interface_counters(host: str, page: int = 1, page_size: int = 50) -> dict:
    """Get traffic counters for all interfaces on any supported network device.

    Returns per-interface packet and byte counters (in/out octets, unicast,
    broadcast, multicast). Supports pagination. Works with Arista EOS,
    Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        page: Page number (default 1, minimum 1).
        page_size: Results per page (default 50, range 1-500).
    """
    if page < 1:
        return {"status": "error", "device": host, "error": "page must be >= 1"}
    if page_size < 1 or page_size > 500:
        return {"status": "error", "device": host, "error": "page_size must be between 1 and 500"}

    driver = conn_mgr.get_driver(host)
    try:
        counters = driver.get_interface_counters()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_interface_counters is not supported on {driver.platform}",
        }

    offset = (page - 1) * page_size
    page_data, pagination = paginate_list(counters, limit=page_size, offset=offset)
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
def net_get_interface_errors(host: str, page: int = 1, page_size: int = 50) -> dict:
    """Get error counters for all interfaces on any supported network device.

    Returns per-interface error counts including CRC, frame, runt, and giant
    errors. Supports pagination. Works with Arista EOS, Cisco IOS-XE,
    Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        page: Page number (default 1, minimum 1).
        page_size: Results per page (default 50, range 1-500).
    """
    if page < 1:
        return {"status": "error", "device": host, "error": "page must be >= 1"}
    if page_size < 1 or page_size > 500:
        return {"status": "error", "device": host, "error": "page_size must be between 1 and 500"}

    driver = conn_mgr.get_driver(host)
    try:
        errors = driver.get_interface_errors()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_interface_errors is not supported on {driver.platform}",
        }

    offset = (page - 1) * page_size
    page_data, pagination = paginate_list(errors, limit=page_size, offset=offset)
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
def net_get_ip_interfaces(host: str) -> dict:
    """Get Layer 3 interfaces with IP addresses from any supported network device.

    Returns interface name, IP address, subnet, VRF, and protocol status.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        ip_intfs = driver.get_ip_interfaces()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_ip_interfaces is not supported on {driver.platform}",
        }

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": ip_intfs,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_trunk_interfaces(host: str) -> dict:
    """Get trunk port configuration from any supported network device.

    Returns allowed VLANs, native VLAN, and active VLANs per trunk port.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        trunks = driver.get_trunk_interfaces()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_trunk_interfaces is not supported on {driver.platform}",
        }

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": trunks,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_transceiver_info(host: str) -> dict:
    """Get transceiver / optics DOM data from any supported network device.

    Returns per-interface transceiver type, TX/RX power (dBm), and temperature.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        xcvrs = driver.get_transceiver_info()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_transceiver_info is not supported on {driver.platform}",
        }

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": xcvrs,
    }


# --- Write Tools ---


def _build_interface_description_commands(platform: str, interface: str, description: str) -> list[str]:
    """Build vendor-specific commands to set or clear an interface description."""
    if not description:
        # Clear the description
        if platform == "junos":
            return [f"delete interfaces {interface} description"]
        return [f"interface {interface}", "no description"]
    if platform == "junos":
        # JunOS uses set-style configuration
        return [f'set interfaces {interface} description "{description}"']
    # EOS, IOS-XE, NX-OS all use the same syntax
    return [f"interface {interface}", f"description {description}"]


def _build_shutdown_commands(platform: str, interface: str) -> list[str]:
    """Build vendor-specific commands to shutdown an interface."""
    if platform == "junos":
        return [f"set interfaces {interface} disable"]
    return [f"interface {interface}", "shutdown"]


def _build_no_shutdown_commands(platform: str, interface: str) -> list[str]:
    """Build vendor-specific commands to enable an interface."""
    if platform == "junos":
        return [f"delete interfaces {interface} disable"]
    return [f"interface {interface}", "no shutdown"]


@mcp.tool(annotations=WRITE_SAFE)
def net_configure_interface_description(host: str, interface: str, description: str) -> dict:
    """[WRITE] Set interface description on any vendor device. Requires NET_READ_ONLY=false.

    Updates the interface description without affecting traffic. Useful for documenting
    circuit IDs, connected devices, or purpose. Works with Arista EOS, Cisco IOS-XE,
    Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP address, or inventory name.
        interface: Interface name (e.g., Ethernet1, GigabitEthernet0/0, xe-0/0/0).
        description: Interface description text. Pass empty string "" to clear the description.
    """
    intf_err = validate_multi_vendor_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    # Allow empty string to clear description; only validate non-empty values
    if description:
        desc_err = validate_cli_param(description, "description")
        if desc_err:
            return {"status": "error", "device": host, "error": desc_err}
        if len(description) > 254:
            return {"status": "error", "device": host, "error": "Description must be 254 characters or fewer"}

    driver = conn_mgr.get_driver(host)
    commands = _build_interface_description_commands(driver.platform, interface, description)
    return run_config_command(conn_mgr, host, commands, "net_configure_interface_description")


@mcp.tool(annotations=DESTRUCTIVE)
def net_shutdown_interface(host: str, interface: str) -> dict:
    """[WRITE][DESTRUCTIVE] Administratively disable an interface on any vendor device. Requires NET_READ_ONLY=false.

    Shuts down the interface, which will drop all traffic on it. Use with caution —
    this will cause a link-down event. Works with Arista EOS, Cisco IOS-XE,
    Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP address, or inventory name.
        interface: Interface name (e.g., Ethernet1, GigabitEthernet0/0, xe-0/0/0).
    """
    intf_err = validate_multi_vendor_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}

    driver = conn_mgr.get_driver(host)
    commands = _build_shutdown_commands(driver.platform, interface)
    return run_config_command(conn_mgr, host, commands, "net_shutdown_interface")


@mcp.tool(annotations=DESTRUCTIVE)
def net_no_shutdown_interface(host: str, interface: str) -> dict:
    """[WRITE] Administratively enable an interface on any vendor device. Requires NET_READ_ONLY=false.

    Removes the shutdown state, allowing the interface to come up. Works with
    Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP address, or inventory name.
        interface: Interface name (e.g., Ethernet1, GigabitEthernet0/0, xe-0/0/0).
    """
    intf_err = validate_multi_vendor_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}

    driver = conn_mgr.get_driver(host)
    commands = _build_no_shutdown_commands(driver.platform, interface)
    return run_config_command(conn_mgr, host, commands, "net_no_shutdown_interface")
