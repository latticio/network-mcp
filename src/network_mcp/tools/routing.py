"""BGP, OSPF, route table, and ARP tools for Arista EOS."""

from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    filter_fields,
    paginate_dict,
    paginate_list,
    run_config_command,
    run_show_command,
    validate_bgp_asn,
    validate_cli_param,
    validate_interface_name,
    validate_ip_address,
    validate_ip_or_hostname,
    validate_ip_prefix,
)
from network_mcp.schemas import BgpSummaryOutput
from network_mcp.server import conn_mgr, mcp

VALID_ADDRESS_FAMILIES = ("ipv4", "ipv6")
VALID_BGP_ADDRESS_FAMILIES = ("ipv4 unicast", "ipv6 unicast", "evpn")


def _validate_vrf(vrf: str, host: str) -> dict | None:
    """Validate VRF name. Returns error dict if invalid, None if OK."""
    if vrf != "default":
        vrf_err = validate_cli_param(vrf, "vrf")
        if vrf_err:
            return {"status": "error", "device": host, "error": vrf_err}
    return None


def _validate_address_family(address_family: str, host: str) -> dict | None:
    """Validate address_family parameter. Returns error dict if invalid, None if OK."""
    if address_family not in VALID_ADDRESS_FAMILIES:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid address_family '{address_family}'. Must be one of: {', '.join(VALID_ADDRESS_FAMILIES)}",
        }
    return None


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def eos_get_bgp_summary(
    host: str,
    address_family: str = "ipv4",
    vrf: str = "default",
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> BgpSummaryOutput:
    """Get BGP neighbor summary including peer states, prefix counts, and uptime from an Arista EOS device.

    When to use: To check BGP peer status and prefix counts across all peers.
    Output: Per-peer state (Established/Idle/Active), ASN, prefix counts, uptime.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        address_family: Address family — 'ipv4' or 'ipv6'. IPv6 uses 'show ipv6 bgp' commands
            and may return different JSON structures. Default: 'ipv4'.
        vrf: VRF name (default: 'default').
        fields: Optional list of field names to include in each peer entry (e.g., ['peerState', 'prefixReceived']).
        limit: Maximum number of peers to return (pagination). None returns all.
        offset: Number of peers to skip (pagination). Default: 0.
    """
    af_err = _validate_address_family(address_family, host)
    if af_err:
        return af_err  # type: ignore[return-value]
    vrf_err = _validate_vrf(vrf, host)
    if vrf_err:
        return vrf_err  # type: ignore[return-value]
    prefix = "ip" if address_family == "ipv4" else "ipv6"
    cmd = f"show {prefix} bgp summary"
    if vrf != "default":
        cmd = f"show {prefix} bgp summary vrf {vrf}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result  # type: ignore[return-value]
    vrfs = result["data"][0].get("vrfs", {})
    vrf_data = vrfs.get(vrf, {})
    peers = vrf_data.get("peers", {})
    if fields:
        peers = {k: filter_fields(v, fields) for k, v in peers.items()}
    if limit is not None or offset:
        peers, pagination = paginate_dict(peers, limit, offset)
    else:
        pagination = None
    resp = {
        "status": "success",
        "device": host,
        "data": {
            "vrf": vrf,
            "address_family": address_family,
            "router_id": vrf_data.get("routerId"),
            "local_asn": vrf_data.get("asn"),
            "peers": peers,
        },
    }
    if pagination:
        resp["pagination"] = pagination
    return resp  # type: ignore[return-value]


@mcp.tool(annotations=READ_ONLY)
def eos_get_bgp_neighbors(
    host: str,
    address_family: str = "ipv4",
    neighbor: str | None = None,
    vrf: str = "default",
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get detailed BGP neighbor information from an Arista EOS device.

    When to use: To deep-dive into BGP peer configuration, timers, and route filtering.
    Output: Detailed per-peer info including route-maps, message counts, transitions.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        address_family: Address family — 'ipv4' or 'ipv6'. IPv6 uses 'show ipv6 bgp' commands
            and may return different JSON structures. Default: 'ipv4'.
        neighbor: Specific neighbor IP to query (optional, returns all if not set).
        vrf: VRF name (default: 'default').
        fields: Optional list of field names to include per peer entry (e.g., ['peerState', 'prefixReceived']).
        limit: Maximum number of BGP neighbor entries to return (pagination). None returns all.
        offset: Number of BGP neighbor entries to skip (pagination). Default: 0.
    """
    af_err = _validate_address_family(address_family, host)
    if af_err:
        return af_err
    vrf_err = _validate_vrf(vrf, host)
    if vrf_err:
        return vrf_err
    if neighbor is not None:
        neighbor_err = validate_ip_or_hostname(neighbor, "neighbor")
        if neighbor_err:
            return {"status": "error", "device": host, "error": neighbor_err}
    prefix = "ip" if address_family == "ipv4" else "ipv6"
    cmd = f"show {prefix} bgp neighbors"
    if neighbor:
        cmd = f"show {prefix} bgp neighbors {neighbor}"
    if vrf != "default":
        cmd = f"{cmd} vrf {vrf}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    data = result["data"][0]
    pagination = None
    vrf_neighbors = data.get("vrfBgpNeighbors", {})
    for vrf_data in vrf_neighbors.values():
        peer_list = vrf_data.get("peerList", [])
        if fields:
            peer_list = [filter_fields(peer, fields) for peer in peer_list]
        if limit is not None or offset:
            peer_list, pagination = paginate_list(peer_list, limit, offset)
        vrf_data["peerList"] = peer_list
    resp = {
        "status": "success",
        "device": host,
        "data": data,
    }
    if pagination:
        resp["pagination"] = pagination
    return resp


@mcp.tool(annotations=READ_ONLY)
def eos_get_bgp_routes(
    host: str,
    address_family: str = "ipv4",
    vrf: str = "default",
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get the BGP routing table from an Arista EOS device.

    When to use: To check BGP learned routes or verify route advertisement.
    Output: BGP RIB entries with next-hop, AS-path, communities.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        address_family: Address family — 'ipv4' or 'ipv6'. IPv6 uses 'show ipv6 bgp' commands
            and may return different JSON structures. Default: 'ipv4'.
        vrf: VRF name (default: 'default').
        fields: Optional list of field names to include per route entry (e.g., ['routeType', 'nextHop']).
        limit: Maximum number of BGP route entries to return (pagination). None returns all.
        offset: Number of BGP route entries to skip (pagination). Default: 0.
    """
    af_err = _validate_address_family(address_family, host)
    if af_err:
        return af_err
    vrf_err = _validate_vrf(vrf, host)
    if vrf_err:
        return vrf_err
    prefix = "ip" if address_family == "ipv4" else "ipv6"
    cmd = f"show {prefix} bgp"
    if vrf != "default":
        cmd = f"show {prefix} bgp vrf {vrf}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    data = result["data"][0]
    # BGP routes are stored as a dict keyed by prefix in vrfs.<vrf>.bgpRouteEntries
    vrf_data = data.get("vrfs", {}).get(vrf, {})
    routes = vrf_data.get("bgpRouteEntries", {})
    if fields:
        routes = {k: filter_fields(v, fields) for k, v in routes.items()}
    pagination = None
    if limit is not None or offset:
        routes, pagination = paginate_dict(routes, limit, offset)
    if "vrfs" in data and vrf in data["vrfs"]:
        data["vrfs"][vrf]["bgpRouteEntries"] = routes
    resp = {
        "status": "success",
        "device": host,
        "data": data,
    }
    if pagination:
        resp["pagination"] = pagination
    return resp


@mcp.tool(annotations=READ_ONLY)
def eos_get_ospf_neighbors(
    host: str,
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get OSPF adjacencies and neighbor states from an Arista EOS device.

    When to use: To check OSPF adjacency status across all interfaces.
    Output: OSPF neighbor list with state (Full/Init), DR/BDR, interface.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        fields: Optional list of field names to include per OSPF neighbor entry (e.g., ['routerId', 'adjacencyState']).
        limit: Maximum number of OSPF neighbor entries to return (pagination). None returns all.
        offset: Number of OSPF neighbor entries to skip (pagination). Default: 0.
    """
    result = run_show_command(conn_mgr, host, ["show ip ospf neighbor"])
    if result["status"] == "error":
        return result
    data = result["data"][0]
    pagination = None
    for vrf_data in data.get("vrfs", {}).values():
        for inst_data in vrf_data.get("instList", {}).values():
            entries = inst_data.get("ospfNeighborEntries", [])
            if fields:
                entries = [filter_fields(e, fields) for e in entries]
            if limit is not None or offset:
                entries, pagination = paginate_list(entries, limit, offset)
            inst_data["ospfNeighborEntries"] = entries
    resp = {
        "status": "success",
        "device": host,
        "data": data,
    }
    if pagination:
        resp["pagination"] = pagination
    return resp


@mcp.tool(annotations=READ_ONLY)
def eos_get_ospf_interfaces(host: str) -> dict:
    """Get OSPF-enabled interfaces and their configuration from an Arista EOS device.

    When to use: To inspect OSPF interface configuration, area assignments, and network types.
    Output: OSPF interfaces with area, network type, cost, and state.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show ip ospf interface brief"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_route_table(
    host: str,
    address_family: str = "ipv4",
    vrf: str = "default",
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get the full IP routing table from an Arista EOS device.

    When to use: To check the routing table or verify a specific route exists.
    Output: Routes with type (connected/BGP/OSPF/static), next-hop, interface.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        address_family: Address family — 'ipv4' or 'ipv6'. IPv6 uses 'show ipv6 route' commands
            and may return different JSON structures. Default: 'ipv4'.
        vrf: VRF name (default: 'default').
        fields: Optional list of field names to include per route (e.g., ['routeType', 'metric']).
        limit: Maximum number of routes to return (pagination). None returns all.
        offset: Number of routes to skip (pagination). Default: 0.
    """
    af_err = _validate_address_family(address_family, host)
    if af_err:
        return af_err
    vrf_err = _validate_vrf(vrf, host)
    if vrf_err:
        return vrf_err
    prefix = "ip" if address_family == "ipv4" else "ipv6"
    cmd = f"show {prefix} route"
    if vrf != "default":
        cmd = f"show {prefix} route vrf {vrf}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    data = result["data"][0]
    # Apply field filtering and pagination to routes within the VRF
    vrf_routes = data.get("vrfs", {}).get(vrf, {}).get("routes", {})
    if fields:
        vrf_routes = {k: filter_fields(v, fields) for k, v in vrf_routes.items()}
    pagination = None
    if limit is not None or offset:
        vrf_routes, pagination = paginate_dict(vrf_routes, limit, offset)
    resp = {
        "status": "success",
        "device": host,
        "data": {**data, "vrfs": {vrf: {**data.get("vrfs", {}).get(vrf, {}), "routes": vrf_routes}}},
    }
    if pagination:
        resp["pagination"] = pagination
    return resp


@mcp.tool(annotations=READ_ONLY)
def eos_get_route_summary(host: str, address_family: str = "ipv4", vrf: str = "default") -> dict:
    """Get route count by protocol from an Arista EOS device.

    When to use: To get a quick overview of route distribution by protocol without full table details.
    Output: Route counts grouped by protocol (connected, static, BGP, OSPF, etc.).

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        address_family: Address family — 'ipv4' or 'ipv6'. IPv6 uses 'show ipv6 route summary'
            and may return different JSON structures. Default: 'ipv4'.
        vrf: VRF name (default: 'default').
    """
    af_err = _validate_address_family(address_family, host)
    if af_err:
        return af_err
    vrf_err = _validate_vrf(vrf, host)
    if vrf_err:
        return vrf_err
    prefix = "ip" if address_family == "ipv4" else "ipv6"
    cmd = f"show {prefix} route summary"
    if vrf != "default":
        cmd = f"show {prefix} route summary vrf {vrf}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_arp_table(
    host: str,
    address_family: str = "ipv4",
    vrf: str = "default",
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get ARP table (IPv4) or IPv6 Neighbor Discovery table from an Arista EOS device.

    When to use: To find IP-to-MAC mappings or diagnose ARP resolution issues.
    Output: ARP entries with IP, MAC, interface, and age.

    For IPv4, returns ARP entries via 'show arp'. For IPv6, returns Neighbor Discovery
    entries via 'show ipv6 neighbors'. The IPv6 response has a different JSON structure.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        address_family: Address family — 'ipv4' for ARP table or 'ipv6' for IPv6 Neighbor
            Discovery table. Default: 'ipv4'.
        vrf: VRF name (default: 'default').
        fields: Optional list of field names to include per ARP/neighbor entry
            (e.g., ['address', 'hwAddress', 'interface']).
        limit: Maximum number of entries to return (pagination). None returns all.
        offset: Number of entries to skip (pagination). Default: 0.
    """
    af_err = _validate_address_family(address_family, host)
    if af_err:
        return af_err
    vrf_err = _validate_vrf(vrf, host)
    if vrf_err:
        return vrf_err
    if address_family == "ipv4":
        cmd = "show arp"
        if vrf != "default":
            cmd = f"show arp vrf {vrf}"
    else:
        cmd = "show ipv6 neighbors"
        if vrf != "default":
            cmd = f"show ipv6 neighbors vrf {vrf}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    data = result["data"][0]
    pagination = None
    # Apply field filtering and pagination to neighbor list entries
    for key in ("ipV4Neighbors", "ipV6Neighbors"):
        if key in data:
            entries = data[key]
            if fields:
                entries = [filter_fields(entry, fields) for entry in entries]
            if limit is not None or offset:
                entries, pagination = paginate_list(entries, limit, offset)
            data[key] = entries
    resp = {
        "status": "success",
        "device": host,
        "data": data,
    }
    if pagination:
        resp["pagination"] = pagination
    return resp


@mcp.tool(annotations=READ_ONLY)
def eos_get_prefix_list(host: str, name: str | None = None) -> dict:
    """Get configured IP prefix lists from an Arista EOS device.

    When to use: To audit prefix-list configuration used in route filtering.
    Output: Prefix-list entries with sequence numbers, actions, and prefixes.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: Specific prefix-list name to query (optional, returns all if not set).
    """
    if name is not None:
        name_err = validate_cli_param(name, "name")
        if name_err:
            return {"status": "error", "device": host, "error": name_err}
    cmd = "show ip prefix-list"
    if name:
        cmd = f"show ip prefix-list {name}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_route_maps(host: str, name: str | None = None) -> dict:
    """Get configured route maps from an Arista EOS device.

    When to use: To audit route-map configuration used in BGP/OSPF policy.
    Output: Route-map entries with match/set clauses and permit/deny actions.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: Specific route-map name to query (optional, returns all if not set).
    """
    if name is not None:
        name_err = validate_cli_param(name, "name")
        if name_err:
            return {"status": "error", "device": host, "error": name_err}
    cmd = "show route-map"
    if name:
        cmd = f"show route-map {name}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_vrrp_status(host: str) -> dict:
    """Get VRRP (Virtual Router Redundancy Protocol) status on an Arista EOS device.

    When to use: To check VRRP gateway redundancy status.
    Output: VRRP groups with state (Master/Backup), virtual IP, priority.

    Shows VRRP groups, their state (master/backup), virtual IPs, and priority.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show vrrp"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_varp_status(host: str) -> dict:
    """Get Virtual ARP (VARP) status on an Arista EOS device.

    When to use: To check Arista Virtual ARP (anycast gateway) configuration.
    Output: Virtual MAC, interface assignments, and IP mappings.

    VARP is Arista's anycast gateway feature used in VXLAN fabrics. Shows the virtual
    MAC address and per-interface virtual IPs. Essential for EVPN/VXLAN troubleshooting.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show ip virtual-router"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


# --- BGP Write Tools ---


def _validate_asn(asn: int, host: str) -> dict | None:
    """Validate BGP ASN range (1-4294967295). Returns error dict if invalid, None if OK."""
    err = validate_bgp_asn(asn)
    if err:
        return {"status": "error", "device": host, "error": err}
    return None


def _validate_bgp_address_family(address_family: str, host: str) -> dict | None:
    """Validate BGP address-family for write operations. Returns error dict if invalid, None if OK."""
    if address_family not in VALID_BGP_ADDRESS_FAMILIES:
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Invalid address_family '{address_family}'. Must be one of: {', '.join(VALID_BGP_ADDRESS_FAMILIES)}"
            ),
        }
    return None


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_bgp_neighbor(
    host: str, asn: int, neighbor_ip: str, remote_as: int, description: str | None = None
) -> dict:
    """[WRITE] Create a BGP neighbor on an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To add a new BGP peering session with a neighbor device.
    Output: Confirmation of neighbor configuration applied, or error details on failure.

    Adds a new BGP neighbor under the specified BGP process. Optionally sets a description.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        asn: Local BGP autonomous system number (1-4294967295).
        neighbor_ip: IP address of the BGP neighbor (e.g., '10.0.0.2').
        remote_as: Remote AS number of the neighbor (1-4294967295).
        description: Optional description for the neighbor.
    """
    asn_err = _validate_asn(asn, host)
    if asn_err:
        return asn_err
    remote_asn_err = _validate_asn(remote_as, host)
    if remote_asn_err:
        return remote_asn_err
    ip_err = validate_ip_or_hostname(neighbor_ip, "neighbor_ip")
    if ip_err:
        return {"status": "error", "device": host, "error": ip_err}
    commands = [f"router bgp {asn}", f"neighbor {neighbor_ip} remote-as {remote_as}"]
    if description is not None:
        desc_err = validate_cli_param(description, "description")
        if desc_err:
            return {"status": "error", "device": host, "error": desc_err}
        commands.append(f"neighbor {neighbor_ip} description {description}")
    return run_config_command(conn_mgr, host, commands, "create_bgp_neighbor")


@mcp.tool(annotations=DESTRUCTIVE)
def eos_delete_bgp_neighbor(host: str, asn: int, neighbor_ip: str) -> dict:
    """[WRITE] Delete a BGP neighbor from an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To remove an existing BGP peering session and tear down the adjacency.
    Output: Confirmation of neighbor removal, or error details on failure.

    Removes a BGP neighbor and all associated configuration. This is a significant
    change that will tear down the BGP session.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        asn: Local BGP autonomous system number (1-4294967295).
        neighbor_ip: IP address of the BGP neighbor to remove.
    """
    asn_err = _validate_asn(asn, host)
    if asn_err:
        return asn_err
    ip_err = validate_ip_or_hostname(neighbor_ip, "neighbor_ip")
    if ip_err:
        return {"status": "error", "device": host, "error": ip_err}
    commands = [f"router bgp {asn}", f"no neighbor {neighbor_ip}"]
    return run_config_command(conn_mgr, host, commands, "delete_bgp_neighbor")


@mcp.tool(annotations=WRITE_SAFE)
def eos_enable_bgp_af(host: str, asn: int, address_family: str, neighbor_ip: str) -> dict:
    """[WRITE] Activate a BGP neighbor in an address family on an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To enable a BGP neighbor within a specific address family (IPv4/IPv6/EVPN).
    Output: Confirmation of address-family activation, or error details on failure.

    Enters the specified address-family context and activates the neighbor.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        asn: Local BGP autonomous system number (1-4294967295).
        address_family: Address family to activate — 'ipv4 unicast', 'ipv6 unicast', or 'evpn'.
        neighbor_ip: IP address of the BGP neighbor to activate.
    """
    asn_err = _validate_asn(asn, host)
    if asn_err:
        return asn_err
    af_err = _validate_bgp_address_family(address_family, host)
    if af_err:
        return af_err
    ip_err = validate_ip_or_hostname(neighbor_ip, "neighbor_ip")
    if ip_err:
        return {"status": "error", "device": host, "error": ip_err}
    commands = [f"router bgp {asn}", f"address-family {address_family}", f"neighbor {neighbor_ip} activate"]
    return run_config_command(conn_mgr, host, commands, "enable_bgp_af")


# --- Routing Policy Write Tools ---

VALID_PL_ACTIONS = ("permit", "deny")


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_prefix_list(host: str, name: str, entries: list[dict]) -> dict:
    """[WRITE] Create or replace an IP prefix-list on an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To define or update prefix-list filters for BGP/OSPF route policy.
    Output: Confirmation of prefix-list configuration applied, or error details on failure.

    Idempotent: removes any existing prefix-list with the same name before creating.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: Name of the prefix-list (e.g., 'ALLOW-DEFAULT').
        entries: List of prefix-list entries. Each entry is a dict with keys:
            seq (int): Sequence number (1-65535).
            action (str): 'permit' or 'deny'.
            prefix (str): IP prefix in CIDR notation (e.g., '10.0.0.0/8').
            ge (int, optional): Minimum prefix length.
            le (int, optional): Maximum prefix length.
    """
    name_err = validate_cli_param(name, "name")
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    if not entries:
        return {"status": "error", "device": host, "error": "entries must not be empty."}
    commands: list[str] = [f"no ip prefix-list {name}"]
    for entry in entries:
        seq = entry.get("seq")
        action = entry.get("action")
        prefix = entry.get("prefix")
        if seq is None or not isinstance(seq, int) or seq < 1 or seq > 65535:
            return {"status": "error", "device": host, "error": f"Invalid seq '{seq}'. Must be an integer 1-65535."}
        if action not in VALID_PL_ACTIONS:
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid action '{action}'. Must be 'permit' or 'deny'.",
            }
        if prefix is None:
            return {"status": "error", "device": host, "error": "Each entry must include a 'prefix' key."}
        prefix_err = validate_ip_prefix(prefix)
        if prefix_err:
            return {"status": "error", "device": host, "error": prefix_err}
        mask_len = int(prefix.strip().split("/")[1])
        ge = entry.get("ge")
        le = entry.get("le")
        if ge is not None:
            if not isinstance(ge, int) or ge < mask_len or ge > 32:
                return {
                    "status": "error",
                    "device": host,
                    "error": f"Invalid ge value {ge}. Must be between {mask_len} and 32.",
                }
        if le is not None:
            if not isinstance(le, int) or le < mask_len or le > 32:
                return {
                    "status": "error",
                    "device": host,
                    "error": f"Invalid le value {le}. Must be between {mask_len} and 32.",
                }
        if ge is not None and le is not None and ge > le:
            return {"status": "error", "device": host, "error": f"ge ({ge}) must not exceed le ({le})."}
        cmd = f"ip prefix-list {name} seq {seq} {action} {prefix}"
        if ge is not None:
            cmd += f" ge {ge}"
        if le is not None:
            cmd += f" le {le}"
        commands.append(cmd)
    return run_config_command(conn_mgr, host, commands, "create_prefix_list")


VALID_RM_ACTIONS = ("permit", "deny")


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_route_map(host: str, name: str, clauses: list[dict]) -> dict:
    """[WRITE] Create or replace a route-map on an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To define or update route-map policy for BGP/OSPF redistribution or filtering.
    Output: Confirmation of route-map configuration applied, or error details on failure.

    Idempotent: removes any existing route-map with the same name before creating.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: Name of the route-map (e.g., 'EXPORT-MAP').
        clauses: List of route-map clauses. Each clause is a dict with keys:
            seq (int): Sequence number (1-65535).
            action (str): 'permit' or 'deny'.
            match (list[str], optional): List of match statements (e.g., ['ip address prefix-list PL1']).
            set (list[str], optional): List of set statements (e.g., ['local-preference 200']).
    """
    name_err = validate_cli_param(name, "name")
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    if not clauses:
        return {"status": "error", "device": host, "error": "clauses must not be empty."}
    commands: list[str] = [f"no route-map {name}"]
    for clause in clauses:
        seq = clause.get("seq")
        action = clause.get("action")
        if seq is None or not isinstance(seq, int) or seq < 1 or seq > 65535:
            return {"status": "error", "device": host, "error": f"Invalid seq '{seq}'. Must be an integer 1-65535."}
        if action not in VALID_RM_ACTIONS:
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid action '{action}'. Must be 'permit' or 'deny'.",
            }
        commands.append(f"route-map {name} {action} {seq}")
        for match_stmt in clause.get("match", []):
            match_err = validate_cli_param(match_stmt, "match statement")
            if match_err:
                return {"status": "error", "device": host, "error": match_err}
            commands.append(f"match {match_stmt}")
        for set_stmt in clause.get("set", []):
            set_err = validate_cli_param(set_stmt, "set statement")
            if set_err:
                return {"status": "error", "device": host, "error": set_err}
            commands.append(f"set {set_stmt}")
    return run_config_command(conn_mgr, host, commands, "create_route_map")


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_static_route(
    host: str,
    prefix: str,
    next_hop: str,
    vrf: str | None = None,
    admin_distance: int = 1,
    name: str | None = None,
) -> dict:
    """[WRITE] Add a static route on an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To add a static route entry for a destination prefix.
    Output: Confirmation of static route configuration applied, or error details on failure.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        prefix: Destination prefix in CIDR notation (e.g., '10.1.0.0/24').
        next_hop: Next-hop IP address (e.g., '10.0.0.1').
        vrf: VRF name (optional, global table if omitted).
        admin_distance: Administrative distance (1-255, default 1).
        name: Optional route name/tag.
    """
    prefix_err = validate_ip_prefix(prefix)
    if prefix_err:
        return {"status": "error", "device": host, "error": prefix_err}
    nh_err = validate_ip_address(next_hop)
    if nh_err:
        return {"status": "error", "device": host, "error": nh_err}
    if vrf is not None:
        vrf_err = _validate_vrf(vrf, host)
        if vrf_err:
            return vrf_err
    if not isinstance(admin_distance, int) or admin_distance < 1 or admin_distance > 255:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid admin_distance {admin_distance}. Must be between 1 and 255.",
        }
    if name is not None:
        name_err = validate_cli_param(name, "name")
        if name_err:
            return {"status": "error", "device": host, "error": name_err}
    if vrf is not None:
        cmd = f"ip route vrf {vrf} {prefix} {next_hop} {admin_distance}"
    else:
        cmd = f"ip route {prefix} {next_hop} {admin_distance}"
    if name is not None:
        cmd += f" name {name}"
    return run_config_command(conn_mgr, host, [cmd], "create_static_route")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_ospf(
    host: str,
    process_id: int,
    router_id: str | None = None,
    networks: list[dict] | None = None,
    passive_interfaces: list[str] | None = None,
) -> dict:
    """[WRITE] Configure an OSPF process on an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To create or update an OSPF routing process with network statements and passive interfaces.
    Output: Confirmation of OSPF configuration applied, or error details on failure.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        process_id: OSPF process ID (1-65535).
        router_id: Router ID in dotted-decimal format (optional).
        networks: List of network statements. Each dict has keys:
            prefix (str): Network prefix (e.g., '10.0.0.0').
            wildcard (str): Wildcard mask (e.g., '0.0.0.255').
            area (str): OSPF area (e.g., '0.0.0.0' or '0').
        passive_interfaces: Optional list of interface names to set as passive.
    """
    if not isinstance(process_id, int) or process_id < 1 or process_id > 65535:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid process_id {process_id}. Must be between 1 and 65535.",
        }
    commands: list[str] = [f"router ospf {process_id}"]
    if router_id is not None:
        rid_err = validate_ip_address(router_id)
        if rid_err:
            return {"status": "error", "device": host, "error": f"Invalid router_id: {rid_err}"}
        commands.append(f"router-id {router_id}")
    if networks:
        for net in networks:
            net_prefix = net.get("prefix")
            wildcard = net.get("wildcard")
            area = net.get("area")
            if net_prefix is None or wildcard is None or area is None:
                return {
                    "status": "error",
                    "device": host,
                    "error": "Each network entry must include 'prefix', 'wildcard', and 'area'.",
                }
            ip_err = validate_ip_address(net_prefix)
            if ip_err:
                return {"status": "error", "device": host, "error": f"Invalid network prefix: {ip_err}"}
            wc_err = validate_ip_address(wildcard)
            if wc_err:
                return {"status": "error", "device": host, "error": f"Invalid wildcard mask: {wc_err}"}
            area_err = validate_cli_param(str(area), "area")
            if area_err:
                return {"status": "error", "device": host, "error": area_err}
            commands.append(f"network {net_prefix} {wildcard} area {area}")
    if passive_interfaces:
        for iface in passive_interfaces:
            iface_err = validate_interface_name(iface)
            if iface_err:
                return {"status": "error", "device": host, "error": iface_err}
            commands.append(f"passive-interface {iface}")
    return run_config_command(conn_mgr, host, commands, "configure_ospf")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_bgp_timers(host: str, asn: int, keepalive: int, holdtime: int) -> dict:
    """[WRITE] Configure BGP keepalive and hold timers on an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To tune BGP convergence timers for the BGP process.
    Output: Confirmation of BGP timer configuration applied, or error details on failure.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        asn: Local BGP autonomous system number (1-4294967295).
        keepalive: Keepalive interval in seconds (1-3600).
        holdtime: Hold time in seconds (3-7200). Must be >= 3x keepalive.
    """
    asn_err = _validate_asn(asn, host)
    if asn_err:
        return asn_err
    if not isinstance(keepalive, int) or keepalive < 1 or keepalive > 3600:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid keepalive {keepalive}. Must be between 1 and 3600.",
        }
    if not isinstance(holdtime, int) or holdtime < 3 or holdtime > 7200:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid holdtime {holdtime}. Must be between 3 and 7200.",
        }
    if holdtime < 3 * keepalive:
        return {
            "status": "error",
            "device": host,
            "error": (
                f"holdtime ({holdtime}) must be at least 3x keepalive ({keepalive}). Minimum holdtime: {3 * keepalive}."
            ),
        }
    commands = [f"router bgp {asn}", f"timers bgp {keepalive} {holdtime}"]
    return run_config_command(conn_mgr, host, commands, "configure_bgp_timers")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_bgp_auth(host: str, asn: int, neighbor: str, md5_key: str) -> dict:
    """[WRITE] Configure BGP MD5 authentication for a neighbor on an Arista EOS device. Requires NET_READ_ONLY=false.

    When to use: To enable MD5 authentication on a BGP neighbor session for security.
    Output: Confirmation of BGP authentication configuration applied, or error details on failure.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        asn: Local BGP autonomous system number (1-4294967295).
        neighbor: IP address of the BGP neighbor.
        md5_key: MD5 authentication key string.
    """
    asn_err = _validate_asn(asn, host)
    if asn_err:
        return asn_err
    ip_err = validate_ip_address(neighbor)
    if ip_err:
        return {"status": "error", "device": host, "error": ip_err}
    if not md5_key or not md5_key.strip():
        return {"status": "error", "device": host, "error": "md5_key cannot be empty."}
    key_err = validate_cli_param(md5_key, "md5_key")
    if key_err:
        return {"status": "error", "device": host, "error": key_err}
    commands = [f"router bgp {asn}", f"neighbor {neighbor} password 7 {md5_key}"]
    return run_config_command(conn_mgr, host, commands, "configure_bgp_auth")
