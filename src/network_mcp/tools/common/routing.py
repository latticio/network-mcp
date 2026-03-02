"""Vendor-agnostic routing tools."""

from __future__ import annotations

import logging

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import (
    READ_ONLY,
    WRITE_SAFE,
    handle_tool_errors,
    paginate_list,
    run_config_command,
    validate_cli_param,
    validate_ip_address,
    validate_ip_prefix,
)
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_bgp_summary(host: str, vrf: str = "default") -> dict:
    """Get BGP summary from any supported network device.

    Returns normalized BGP peer states, prefix counts, and uptime.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vrf: VRF name (default: "default"). Use "all" for all VRFs where supported.
    """
    driver = conn_mgr.get_driver(host)
    bgp = driver.get_bgp_summary(vrf=vrf)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": bgp,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_ospf_neighbors(host: str, vrf: str = "default") -> dict:
    """Get OSPF neighbor adjacencies from any supported network device.

    Returns neighbor ID, state, interface, area, and uptime.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vrf: VRF name (default: "default").
    """
    driver = conn_mgr.get_driver(host)
    try:
        neighbors = driver.get_ospf_neighbors(vrf=vrf)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_ospf_neighbors is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": neighbors,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_ospf_interfaces(host: str, vrf: str = "default") -> dict:
    """Get OSPF-enabled interfaces from any supported network device.

    Returns interface, area, network type, cost, and neighbor count.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vrf: VRF name (default: "default").
    """
    driver = conn_mgr.get_driver(host)
    try:
        interfaces = driver.get_ospf_interfaces(vrf=vrf)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_ospf_interfaces is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": interfaces,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_route_table(
    host: str, protocol: str = "", vrf: str = "default", page: int = 1, page_size: int = 100
) -> dict:
    """Get IP routing table from any supported network device. Supports pagination and protocol filter.

    Returns prefix, next-hop, protocol, metric, and administrative distance.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        protocol: Filter by protocol (bgp, ospf, static, connected). Empty = all.
        vrf: VRF name (default: "default").
        page: Page number (default 1, minimum 1).
        page_size: Results per page (default 100, range 1-500).
    """
    if page < 1:
        return {"status": "error", "device": host, "error": "page must be >= 1"}
    if page_size < 1 or page_size > 500:
        return {"status": "error", "device": host, "error": "page_size must be between 1 and 500"}

    driver = conn_mgr.get_driver(host)
    try:
        # Fetch a large batch from the driver — pagination is applied on the tool side
        routes = driver.get_route_table(protocol=protocol, vrf=vrf, limit=10000)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_route_table is not supported on {driver.platform}",
        }

    offset = (page - 1) * page_size
    page_data, pagination = paginate_list(routes, limit=page_size, offset=offset)
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
def net_get_route_summary(host: str, vrf: str = "default") -> dict:
    """Get route count summary by protocol from any supported network device.

    Returns total route count and per-protocol breakdown (connected, static, BGP, OSPF, IS-IS).
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vrf: VRF name (default: "default"). Note: VRF filtering depends on driver support.
    """
    driver = conn_mgr.get_driver(host)
    try:
        summary = driver.get_route_summary()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_route_summary is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": summary,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_arp_table(host: str, vrf: str = "default", page: int = 1, page_size: int = 100) -> dict:
    """Get ARP/NDP table from any supported network device. Supports pagination.

    Returns IP address, MAC address, interface, and age.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vrf: VRF name (default: "default"). Note: VRF filtering depends on driver support.
        page: Page number (default 1, minimum 1).
        page_size: Results per page (default 100, range 1-500).
    """
    if page < 1:
        return {"status": "error", "device": host, "error": "page must be >= 1"}
    if page_size < 1 or page_size > 500:
        return {"status": "error", "device": host, "error": "page_size must be between 1 and 500"}

    driver = conn_mgr.get_driver(host)
    try:
        arp_entries = driver.get_arp_table()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_arp_table is not supported on {driver.platform}",
        }

    offset = (page - 1) * page_size
    page_data, pagination = paginate_list(arp_entries, limit=page_size, offset=offset)
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
def net_get_bgp_neighbors(host: str, vrf: str = "default") -> dict:
    """Get detailed BGP neighbor information from any supported network device.

    Returns neighbor IP, ASN, state, prefixes received/sent, uptime, and description.
    More detailed than net_get_bgp_summary. Works with Arista EOS, Cisco IOS-XE,
    Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        vrf: VRF name (default: "default").
    """
    driver = conn_mgr.get_driver(host)
    try:
        neighbors = driver.get_bgp_neighbors(vrf=vrf)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_bgp_neighbors is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": neighbors,
    }


# --- Write Tools ---


def _cidr_to_mask(prefix_len: int) -> str:
    """Convert CIDR prefix length to dotted-decimal subnet mask.

    IOS-XE static routes require a subnet mask rather than CIDR notation.
    Example: 24 -> '255.255.255.0'
    """
    bits = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return f"{(bits >> 24) & 0xFF}.{(bits >> 16) & 0xFF}.{(bits >> 8) & 0xFF}.{bits & 0xFF}"


def _build_static_route_commands(platform: str, prefix: str, next_hop: str, admin_distance: int, vrf: str) -> list[str]:
    """Build vendor-specific commands to create a static route."""
    if platform == "junos":
        # JunOS: set routing-options static route <prefix> next-hop <nh>
        if vrf != "default":
            cmd = f"set routing-instances {vrf} routing-options static route {prefix} next-hop {next_hop}"
        else:
            cmd = f"set routing-options static route {prefix} next-hop {next_hop}"
        if admin_distance != 1:
            cmd += f" preference {admin_distance}"
        return [cmd]
    elif platform == "iosxe":
        # IOS-XE uses subnet mask, not CIDR
        parts = prefix.split("/")
        network = parts[0]
        mask = _cidr_to_mask(int(parts[1]))
        if vrf != "default":
            cmd = f"ip route vrf {vrf} {network} {mask} {next_hop}"
        else:
            cmd = f"ip route {network} {mask} {next_hop}"
        if admin_distance != 1:
            cmd += f" {admin_distance}"
        return [cmd]
    else:
        # EOS and NX-OS use CIDR notation
        if vrf != "default":
            cmd = f"ip route vrf {vrf} {prefix} {next_hop}"
        else:
            cmd = f"ip route {prefix} {next_hop}"
        if admin_distance != 1:
            cmd += f" {admin_distance}"
        return [cmd]


def _build_bgp_neighbor_commands(
    platform: str, local_as: int, neighbor: str, remote_as: int, description: str
) -> list[str]:
    """Build vendor-specific commands to add a BGP neighbor."""
    if platform == "junos":
        # JunOS: set protocols bgp group <group> neighbor <ip> peer-as <as>
        group = f"AS{remote_as}"
        cmds = [f"set protocols bgp group {group} neighbor {neighbor} peer-as {remote_as}"]
        if description:
            cmds.append(f'set protocols bgp group {group} neighbor {neighbor} description "{description}"')
        return cmds
    elif platform == "nxos":
        # NX-OS: nested neighbor config
        cmds = [f"router bgp {local_as}", f"neighbor {neighbor}", f"remote-as {remote_as}"]
        if description:
            cmds.append(f"description {description}")
        return cmds
    else:
        # EOS and IOS-XE
        cmds = [f"router bgp {local_as}", f"neighbor {neighbor} remote-as {remote_as}"]
        if description:
            cmds.append(f"neighbor {neighbor} description {description}")
        return cmds


@mcp.tool(annotations=WRITE_SAFE)
def net_create_static_route(
    host: str, prefix: str, next_hop: str, admin_distance: int = 1, vrf: str = "default"
) -> dict:
    """[WRITE] Create a static route on any vendor device. Requires NET_READ_ONLY=false.

    Adds a static route for the given prefix pointing to the specified next-hop.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP address, or inventory name.
        prefix: IP prefix in CIDR notation (e.g., 10.0.0.0/24).
        next_hop: Next-hop IP address (e.g., 10.0.0.1).
        admin_distance: Administrative distance (1-255, default 1).
        vrf: VRF context (default: "default").
    """
    prefix_err = validate_ip_prefix(prefix)
    if prefix_err:
        return {"status": "error", "device": host, "error": prefix_err}
    nh_err = validate_ip_address(next_hop)
    if nh_err:
        return {"status": "error", "device": host, "error": nh_err}
    if not isinstance(admin_distance, int) or admin_distance < 1 or admin_distance > 255:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid admin_distance {admin_distance}. Must be between 1 and 255.",
        }
    if vrf != "default":
        vrf_err = validate_cli_param(vrf, "vrf")
        if vrf_err:
            return {"status": "error", "device": host, "error": vrf_err}

    driver = conn_mgr.get_driver(host)
    commands = _build_static_route_commands(driver.platform, prefix, next_hop, admin_distance, vrf)
    return run_config_command(conn_mgr, host, commands, "net_create_static_route")


@mcp.tool(annotations=WRITE_SAFE)
def net_configure_bgp_neighbor(host: str, local_as: int, neighbor: str, remote_as: int, description: str = "") -> dict:
    """[WRITE] Add a BGP neighbor on any vendor device. Requires NET_READ_ONLY=false.

    Configures a new BGP peer under the specified local AS. On JunOS, creates a peer
    group named after the remote AS. Works with Arista EOS, Cisco IOS-XE,
    Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP address, or inventory name.
        local_as: Local AS number (required for EOS/IOS-XE/NX-OS router bgp context).
        neighbor: Neighbor IP address.
        remote_as: Remote AS number (1-4294967295).
        description: Optional neighbor description.
    """
    nbr_err = validate_ip_address(neighbor)
    if nbr_err:
        return {"status": "error", "device": host, "error": nbr_err}
    if not isinstance(local_as, int) or local_as < 1 or local_as > 4294967295:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid local_as {local_as}. Must be between 1 and 4294967295.",
        }
    if not isinstance(remote_as, int) or remote_as < 1 or remote_as > 4294967295:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid remote_as {remote_as}. Must be between 1 and 4294967295.",
        }
    if description:
        desc_err = validate_cli_param(description, "description")
        if desc_err:
            return {"status": "error", "device": host, "error": desc_err}

    driver = conn_mgr.get_driver(host)
    commands = _build_bgp_neighbor_commands(driver.platform, local_as, neighbor, remote_as, description)
    return run_config_command(conn_mgr, host, commands, "net_configure_bgp_neighbor")
