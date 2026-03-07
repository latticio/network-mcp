"""Vendor-agnostic switching tools."""

from __future__ import annotations

import logging

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import READ_ONLY, handle_tool_errors, paginate_list
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
def net_build_topology_from_lldp(hosts: list[str]) -> dict:
    """Build a network topology graph from LLDP neighbor data across multiple devices.

    Queries LLDP neighbors on each host and assembles a graph with nodes (devices)
    and edges (physical links). Duplicate links (A->B and B->A from separate queries)
    are automatically merged into a single edge. Hosts that are unreachable or fail
    are still included as nodes with an ``error`` field.

    Args:
        hosts: List of device hostnames, IPs, or inventory names to query.

    Returns:
        dict with keys:
            - status: 'success'
            - topology.nodes: list of node dicts (id, hostname, platform, management_ip,
              and optionally error)
            - topology.edges: list of edge dicts (source, target, source_port,
              target_port, speed)
    """
    nodes: dict[str, dict] = {}  # host_id -> node dict
    hostname_to_host_id: dict[str, str] = {}  # LLDP-reported hostname -> host_id
    lldp_by_host: dict[str, dict[str, list[dict]] | None] = {}  # host_id -> LLDP data
    speeds_by_host: dict[str, dict[str, int]] = {}  # host_id -> {interface: speed_mbps}

    # First pass: connect to each host, collect facts + LLDP data
    for host in hosts:
        try:
            driver = conn_mgr.get_driver(host)
        except Exception as exc:
            nodes[host] = {
                "id": host,
                "hostname": host,
                "platform": "",
                "management_ip": host,
                "error": str(exc),
            }
            lldp_by_host[host] = None
            continue

        # Facts (best-effort: failure doesn't abort this host)
        hostname = host
        platform = ""
        try:
            facts = driver.get_facts()
            hostname = facts.get("hostname", host)
            platform = facts.get("platform", "")
        except Exception:  # noqa: BLE001
            logger.debug("get_facts failed for %s; using host as fallback hostname", host)

        nodes[host] = {
            "id": host,
            "hostname": hostname,
            "platform": platform,
            "management_ip": host,
        }
        hostname_to_host_id[hostname] = host

        # Interface speeds (best-effort)
        try:
            ifaces = driver.get_interfaces()
            speeds_by_host[host] = {name: data.get("speed", 0) for name, data in ifaces.items()}
        except Exception:
            speeds_by_host[host] = {}

        # LLDP neighbors (failure marks this host with error but keeps the node)
        try:
            lldp_by_host[host] = driver.get_lldp_neighbors()
        except Exception as exc:
            lldp_by_host[host] = None
            nodes[host]["error"] = str(exc)

    # Build management IP reverse lookup for fallback matching
    mgmt_ip_to_host_id: dict[str, str] = {}
    for neighbor_data in lldp_by_host.values():
        if neighbor_data is None:
            continue
        for _port, nlist in neighbor_data.items():
            for n in nlist:
                mgmt_ip = n.get("management_ip", "")
                if mgmt_ip and mgmt_ip in nodes:
                    mgmt_ip_to_host_id[mgmt_ip] = mgmt_ip
    # Also map host IDs themselves as potential IPs
    for host_id in nodes:
        mgmt_ip_to_host_id[host_id] = host_id

    def _resolve_neighbor(remote_hostname: str, remote_mgmt_ip: str) -> str:
        """Resolve LLDP neighbor to a known host_id with fallback strategies.

        1. Exact hostname match (fast path)
        2. Management IP match (handles truncated hostnames on cEOS)
        3. Prefix match — if exactly one known hostname starts with the remote name,
           it's likely the same device with a truncated LLDP system name
        """
        # 1. Exact match
        if remote_hostname in hostname_to_host_id:
            return hostname_to_host_id[remote_hostname]

        # 2. Management IP match
        if remote_mgmt_ip and remote_mgmt_ip in mgmt_ip_to_host_id:
            return mgmt_ip_to_host_id[remote_mgmt_ip]

        # 3. Prefix match for truncated hostnames (e.g., "leaf" matching "leaf1")
        if remote_hostname:
            prefix_matches = [
                hid for hname, hid in hostname_to_host_id.items()
                if hname.startswith(remote_hostname) and hname != remote_hostname
            ]
            if len(prefix_matches) == 1:
                logger.debug(
                    "LLDP hostname '%s' prefix-matched to '%s'",
                    remote_hostname, prefix_matches[0],
                )
                return prefix_matches[0]

        return remote_hostname

    # Second pass: build edges, deduplicating symmetric A->B / B->A links
    edges: list[dict] = []
    seen_edge_keys: set[frozenset] = set()

    for host, neighbors_by_port in lldp_by_host.items():
        if neighbors_by_port is None:
            continue

        speeds = speeds_by_host.get(host, {})

        for local_port, neighbor_list in neighbors_by_port.items():
            for neighbor in neighbor_list:
                remote_hostname = neighbor.get("hostname", "")
                remote_port = neighbor.get("port", "")
                remote_mgmt_ip = neighbor.get("management_ip", "")

                # Resolve remote hostname to a known host_id with fallback strategies
                remote_id = _resolve_neighbor(remote_hostname, remote_mgmt_ip)

                speed = speeds.get(local_port, 0)

                # Canonical key: frozenset deduplicates A->B and B->A
                edge_key: frozenset = frozenset([(host, local_port), (remote_id, remote_port)])
                if edge_key in seen_edge_keys:
                    continue
                seen_edge_keys.add(edge_key)

                edges.append(
                    {
                        "source": host,
                        "target": remote_id,
                        "source_port": local_port,
                        "target_port": remote_port,
                        "speed": speed,
                    }
                )

                # Add a placeholder node for neighbours not in the queried hosts list
                if remote_id not in nodes:
                    nodes[remote_id] = {
                        "id": remote_id,
                        "hostname": remote_hostname,
                        "platform": "",
                        "management_ip": "",
                    }

    return {
        "status": "success",
        "topology": {
            "nodes": list(nodes.values()),
            "edges": edges,
        },
    }


def _mbps_to_speed_label(speed_mbps: int) -> str | None:
    """Convert interface speed in Mbps to a human-readable label (e.g. '100G').

    Returns None when speed is zero or unknown.
    """
    if not speed_mbps:
        return None
    if speed_mbps >= 400000:
        return "400G"
    if speed_mbps >= 100000:
        return "100G"
    if speed_mbps >= 40000:
        return "40G"
    if speed_mbps >= 25000:
        return "25G"
    if speed_mbps >= 10000:
        return "10G"
    if speed_mbps >= 1000:
        return "1G"
    return f"{speed_mbps}M"


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_lldp_neighbors(host: str) -> dict:
    """Get LLDP neighbor information from any supported network device.

    Returns discovered neighbors per interface with hostname, port, system description,
    and local_port_speed. The local_port_speed field shows the speed of the local
    interface in human-readable form (e.g. '1G', '10G', '25G', '100G', '400G').
    If interface speed data is unavailable, local_port_speed is set to null.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    neighbors = driver.get_lldp_neighbors()

    # Fetch interface speeds (best-effort — failure yields null for all ports)
    speeds: dict[str, int] = {}
    try:
        ifaces = driver.get_interfaces()
        speeds = {name: data.get("speed", 0) for name, data in ifaces.items()}
    except Exception:  # noqa: BLE001
        logger.debug("get_interfaces failed for %s; local_port_speed will be null", host)

    # Merge local_port_speed into each neighbor entry
    for local_port, neighbor_list in neighbors.items():
        speed_label = _mbps_to_speed_label(speeds.get(local_port, 0))
        for neighbor in neighbor_list:
            neighbor["local_port_speed"] = speed_label

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
