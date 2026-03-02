"""Vendor-agnostic VLAN tools (read and write)."""

from __future__ import annotations

import logging

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    check_read_only,
    handle_tool_errors,
    paginate_list,
    validate_cli_param,
    validate_vlan_id,
    validate_vlan_name,
)
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_vlans(host: str, page: int = 1, page_size: int = 50) -> dict:
    """Get all VLANs from any supported network device. Supports pagination.

    Returns a normalized VLAN list with ID, name, status, and member interfaces.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

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
        vlans = driver.get_vlans()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_vlans is not supported on {driver.platform}",
        }

    offset = (page - 1) * page_size
    page_data, pagination = paginate_list(vlans, limit=page_size, offset=offset)
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
def net_get_vlan_detail(host: str, vlan_id: int) -> dict:
    """Get detailed info for a specific VLAN from any supported network device.

    Returns VLAN ID, name, status, member interfaces, and type for the
    requested VLAN. Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS,
    and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vlan_id: VLAN ID (1-4094).
    """
    vlan_err = validate_vlan_id(vlan_id)
    if vlan_err:
        return {"status": "error", "device": host, "error": vlan_err}

    driver = conn_mgr.get_driver(host)
    try:
        detail = driver.get_vlan_detail(vlan_id)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_vlan_detail is not supported on {driver.platform}",
        }

    if not detail:
        return {
            "status": "error",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"VLAN {vlan_id} not found on {host}",
        }

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": detail,
    }


# --- Write Tools ---


def _build_vlan_create_commands(platform: str, vlan_id: int, name: str) -> list[str]:
    """Build platform-specific commands to create a VLAN."""
    if platform == "junos":
        return [f"set vlans {name} vlan-id {vlan_id}"]
    # EOS, IOS-XE, NX-OS all use the same syntax
    return [f"vlan {vlan_id}", f"name {name}"]


def _build_vlan_delete_commands(platform: str, vlan_id: int) -> list[str]:
    """Build platform-specific commands to delete a VLAN."""
    if platform == "junos":
        # JunOS needs VLAN name, not ID — caller must resolve
        return [f"delete vlans vlan-id {vlan_id}"]
    return [f"no vlan {vlan_id}"]


def _build_vlan_rename_commands(platform: str, vlan_id: int, name: str) -> list[str]:
    """Build platform-specific commands to rename a VLAN."""
    if platform == "junos":
        return [f"set vlans {name} vlan-id {vlan_id}"]
    return [f"vlan {vlan_id}", f"name {name}"]


def _build_interface_vlan_commands(platform: str, interface: str, vlan_id: int) -> list[str]:
    """Build platform-specific commands to assign an access VLAN to an interface."""
    if platform == "junos":
        return [
            f"set interfaces {interface} unit 0 family ethernet-switching vlan members vlan{vlan_id}",
        ]
    # EOS, IOS-XE, NX-OS all use the same syntax
    return [
        f"interface {interface}",
        "switchport mode access",
        f"switchport access vlan {vlan_id}",
    ]


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def net_create_vlan(host: str, vlan_id: int, name: str) -> dict:
    """[WRITE] Create a VLAN on any supported network device. Requires NET_READ_ONLY=false.

    Creates the VLAN with the specified ID and name. Works with Arista EOS,
    Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vlan_id: VLAN ID to create (1-4094).
        name: Descriptive name for the VLAN (alphanumeric, hyphens, underscores, max 32 chars).
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    err = validate_vlan_id(vlan_id)
    if err:
        return {"status": "error", "device": host, "error": err}
    name_err = validate_vlan_name(name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    driver = conn_mgr.get_driver(host)
    commands = _build_vlan_create_commands(driver.platform, vlan_id, name)
    driver.run_config(commands)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "action": "create_vlan",
        "data": {"vlan_id": vlan_id, "name": name, "commands": commands},
        "message": f"VLAN {vlan_id} ({name}) created successfully",
    }


@mcp.tool(annotations=DESTRUCTIVE)
@handle_tool_errors
def net_delete_vlan(host: str, vlan_id: int) -> dict:
    """[WRITE] Delete a VLAN from any supported network device. Requires NET_READ_ONLY=false.

    Permanently removes the VLAN. Any interfaces assigned to this VLAN will become
    unassigned. Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vlan_id: VLAN ID to delete (1-4094).
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    err = validate_vlan_id(vlan_id)
    if err:
        return {"status": "error", "device": host, "error": err}

    driver = conn_mgr.get_driver(host)
    commands = _build_vlan_delete_commands(driver.platform, vlan_id)
    driver.run_config(commands)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "action": "delete_vlan",
        "data": {"vlan_id": vlan_id, "commands": commands},
        "message": f"VLAN {vlan_id} deleted successfully",
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def net_rename_vlan(host: str, vlan_id: int, name: str) -> dict:
    """[WRITE] Rename a VLAN on any supported network device. Requires NET_READ_ONLY=false.

    Changes the VLAN name without affecting interface assignments or traffic.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vlan_id: VLAN ID to rename (1-4094).
        name: New name for the VLAN (alphanumeric, hyphens, underscores, max 32 chars).
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    err = validate_vlan_id(vlan_id)
    if err:
        return {"status": "error", "device": host, "error": err}
    name_err = validate_vlan_name(name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    driver = conn_mgr.get_driver(host)
    commands = _build_vlan_rename_commands(driver.platform, vlan_id, name)
    driver.run_config(commands)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "action": "rename_vlan",
        "data": {"vlan_id": vlan_id, "name": name, "commands": commands},
        "message": f"VLAN {vlan_id} renamed to '{name}' successfully",
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def net_assign_interface_vlan(host: str, interface: str, vlan_id: int) -> dict:
    """[WRITE] Assign an access VLAN to an interface on any supported device. Requires NET_READ_ONLY=false.

    Sets the interface to access mode on the specified VLAN. Works with
    Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        interface: Interface name (e.g., 'Ethernet1', 'GigabitEthernet0/1', 'ge-0/0/0').
        vlan_id: VLAN ID to assign (1-4094).
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    err = validate_vlan_id(vlan_id)
    if err:
        return {"status": "error", "device": host, "error": err}
    intf_err = validate_cli_param(interface, "interface")
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}

    driver = conn_mgr.get_driver(host)
    commands = _build_interface_vlan_commands(driver.platform, interface, vlan_id)
    driver.run_config(commands)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "action": "assign_interface_vlan",
        "data": {"interface": interface, "vlan_id": vlan_id, "commands": commands},
        "message": f"Interface {interface} assigned to VLAN {vlan_id} successfully",
    }
