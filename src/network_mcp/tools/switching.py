"""MLAG, STP, MAC table, LLDP, and port-channel tools for Arista EOS."""

from network_mcp.helpers import (
    READ_ONLY,
    WRITE_SAFE,
    filter_fields,
    handle_tool_errors,
    paginate_list,
    run_config_command,
    run_show_command,
    validate_interface_name,
    validate_port_channel_id,
    validate_stp_priority,
    validate_vlan_id,
)
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_mlag_status(host: str) -> dict:
    """Get MLAG domain status, peer link state, and port summary from an Arista EOS device.

    When to use: To check MLAG domain health, peer link state, and port status.
    Output: MLAG state, peer address, negotiation status, active/inactive port counts.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show mlag", "show mlag detail"])
    if result["status"] == "error":
        return result
    mlag = result["data"][0]
    mlag_detail = result["data"][1]
    return {
        "status": "success",
        "device": host,
        "data": {
            "domain_id": mlag.get("domainId"),
            "local_interface": mlag.get("localInterface"),
            "peer_address": mlag.get("peerAddress"),
            "peer_link": mlag.get("peerLink"),
            "state": mlag.get("state"),
            "neg_status": mlag.get("negStatus"),
            "mlag_ports": mlag.get("mlagPorts", {}),
            "detail": mlag_detail,
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_spanning_tree(host: str) -> dict:
    """Get spanning tree topology, root bridge, and port states from an Arista EOS device.

    When to use: To check STP topology, root bridge, or port states for loop prevention.
    Output: STP instance details with root bridge info and port states.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show spanning-tree"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_mac_address_table(
    host: str,
    vlan: int | None = None,
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get MAC address table entries with VLANs and ports from an Arista EOS device.

    When to use: To find which port a MAC address is learned on, or check MAC table size.
    Output: MAC entries with VLAN, interface, and type (dynamic/static).

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vlan: Filter by specific VLAN ID (optional).
        fields: Optional list of field names to include per MAC entry (e.g., ['macAddress', 'interface']).
        limit: Maximum number of MAC entries to return (pagination). None returns all.
        offset: Number of MAC entries to skip (pagination). Default: 0.
    """
    if vlan is not None:
        vlan_err = validate_vlan_id(vlan)
        if vlan_err:
            return {"status": "error", "device": host, "error": vlan_err}
    cmd = "show mac address-table"
    if vlan is not None:
        cmd = f"show mac address-table vlan {vlan}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    data = result["data"][0]
    # MAC table uses unicastTable.tableEntries (a list), apply field filtering + pagination
    entries = data.get("unicastTable", {}).get("tableEntries", [])
    if fields:
        entries = [{k: v for k, v in entry.items() if k in fields} for entry in entries]
    pagination = None
    if limit is not None or offset:
        entries, pagination = paginate_list(entries, limit, offset)
    resp = {
        "status": "success",
        "device": host,
        "data": {**data, "unicastTable": {"tableEntries": entries}},
    }
    if pagination:
        resp["pagination"] = pagination
    return resp


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_lldp_neighbors(
    host: str,
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get LLDP neighbor discovery information showing connected devices from an Arista EOS device.

    When to use: To discover physically connected neighbor devices.
    Output: Per-port neighbor device name, port, and TTL.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        fields: Optional list of field names to include per LLDP neighbor (e.g., ['neighborDevice', 'neighborPort']).
        limit: Maximum number of LLDP neighbors to return (pagination). None returns all.
        offset: Number of LLDP neighbors to skip (pagination). Default: 0.
    """
    result = run_show_command(conn_mgr, host, ["show lldp neighbors"])
    if result["status"] == "error":
        return result
    data = result["data"][0]
    neighbors = data.get("lldpNeighbors", [])
    if fields:
        neighbors = [filter_fields(n, fields) for n in neighbors]
    pagination = None
    if limit is not None or offset:
        neighbors, pagination = paginate_list(neighbors, limit, offset)
    data = {**data, "lldpNeighbors": neighbors}
    resp = {
        "status": "success",
        "device": host,
        "data": data,
    }
    if pagination:
        resp["pagination"] = pagination
    return resp


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_lldp_neighbor_detail(host: str, interface: str) -> dict:
    """Get detailed LLDP neighbor information for a specific interface on an Arista EOS device.

    When to use: To get detailed neighbor info for a specific interface (system description, capabilities).
    Output: Full LLDP TLV data for the specified interface's neighbor.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name to query (e.g., 'Ethernet1').
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    result = run_show_command(conn_mgr, host, [f"show lldp neighbors {interface} detail"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_port_channels(host: str) -> dict:
    """Get LAG/port-channel summary with member interface status from an Arista EOS device.

    When to use: To check LAG/port-channel member status and load balancing.
    Output: Port-channel summary with member interfaces and their states.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show port-channel summary"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_mlag_config_sanity(host: str) -> dict:
    """Check MLAG configuration consistency between peers on an Arista EOS device.

    When to use: First tool when troubleshooting MLAG issues — checks peer config consistency.
    Output: Config sanity result (consistent/inconsistent) with mismatch details.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show mlag config-sanity"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_mlag_interfaces(host: str) -> dict:
    """Get per-interface MLAG status showing local and remote link states on an Arista EOS device.

    When to use: To check per-interface MLAG status including local and remote link states.
    Output: MLAG interface list with local/peer link status and MLAG ID.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show mlag interfaces"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_trunk_interfaces(host: str) -> dict:
    """Get trunk interfaces with allowed, native, and active VLANs on an Arista EOS device.

    When to use: To verify VLAN propagation on trunk ports or check allowed VLANs.
    Output: Trunk ports with allowed, native, and active VLANs.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show interfaces trunk"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


# --- Write Tools ---


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_port_channel(host: str, channel_id: int, mode: str, members: list[str]) -> dict:
    """[WRITE] Create a port-channel (LAG) with LACP or static mode on an Arista EOS device.

    Creates the port-channel interface and adds each member interface to it. Supports
    LACP active, passive, or static (on) modes. Requires NET_READ_ONLY=false.

    When to use: To create a LAG/port-channel and assign member interfaces.
    Output: Confirmation of port-channel creation with member assignments.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        channel_id: Port-channel ID (1-2000).
        mode: LACP mode ('active', 'passive', or 'on' for static).
        members: List of member interface names (e.g., ['Ethernet1', 'Ethernet2']).
    """
    ch_err = validate_port_channel_id(channel_id)
    if ch_err:
        return {"status": "error", "device": host, "error": ch_err}
    valid_modes = {"active", "passive", "on"}
    if mode not in valid_modes:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid mode '{mode}'. Must be one of: {sorted(valid_modes)}",
        }
    if not members:
        return {"status": "error", "device": host, "error": "At least one member interface is required"}
    for member in members:
        intf_err = validate_interface_name(member)
        if intf_err:
            return {"status": "error", "device": host, "error": f"Invalid member interface: {intf_err}"}
    commands = [f"interface Port-Channel{channel_id}"]
    for member in members:
        commands.extend([f"interface {member}", f"channel-group {channel_id} mode {mode}"])
    return run_config_command(conn_mgr, host, commands, "create_port_channel")


@mcp.tool(annotations=WRITE_SAFE)
def eos_add_port_channel_member(host: str, interface: str, channel_id: int) -> dict:
    """[WRITE] Add a member interface to an existing port-channel on an Arista EOS device.

    Adds a single interface as a member of an existing port-channel using LACP active mode.
    Requires NET_READ_ONLY=false.

    When to use: To add an interface to an existing LAG/port-channel.
    Output: Confirmation of member addition.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name to add (e.g., 'Ethernet3').
        channel_id: Target port-channel ID (1-2000).
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    ch_err = validate_port_channel_id(channel_id)
    if ch_err:
        return {"status": "error", "device": host, "error": ch_err}
    commands = [f"interface {interface}", f"channel-group {channel_id} mode active"]
    return run_config_command(conn_mgr, host, commands, "add_port_channel_member")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_stp(host: str, mode: str, priority: int = 32768, instances: list[str] | None = None) -> dict:
    """[WRITE] Configure spanning tree mode and bridge priority on an Arista EOS device.

    Sets the STP mode (MSTP, RSTP, or Rapid-PVST) and bridge priority. Optionally
    configures MSTP instance-to-VLAN mappings. Requires NET_READ_ONLY=false.

    When to use: To configure STP mode and set bridge priority for root election.
    Output: Confirmation of STP configuration.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        mode: STP mode ('mstp', 'rstp', or 'rapid-pvst').
        priority: Bridge priority (must be a multiple of 4096, range 0-61440). Default: 32768.
        instances: Optional list of MSTP instance/VLAN mappings (e.g., ['instance 1 vlan 100-200']).
    """
    valid_modes = {"mstp", "rstp", "rapid-pvst"}
    if mode not in valid_modes:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid STP mode '{mode}'. Must be one of: {sorted(valid_modes)}",
        }
    prio_err = validate_stp_priority(priority)
    if prio_err:
        return {"status": "error", "device": host, "error": prio_err}
    commands = [f"spanning-tree mode {mode}"]
    if priority == 0:
        commands.append("spanning-tree root primary")
    else:
        commands.append(f"spanning-tree priority {priority}")
    if instances:
        for inst in instances:
            commands.append(f"spanning-tree {inst}")
    return run_config_command(conn_mgr, host, commands, "configure_stp")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_bpdu_guard(host: str, interface: str, enabled: bool) -> dict:
    """[WRITE] Enable or disable BPDU guard on an interface of an Arista EOS device.

    BPDU guard shuts down the port if a BPDU is received, protecting the STP topology
    from rogue switches. Typically used on access/edge ports. Requires NET_READ_ONLY=false.

    When to use: To protect edge ports from STP topology changes caused by rogue switches.
    Output: Confirmation of BPDU guard configuration.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1').
        enabled: True to enable BPDU guard, False to disable.
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    cmd = "spanning-tree bpduguard enable" if enabled else "no spanning-tree bpduguard"
    commands = [f"interface {interface}", cmd]
    return run_config_command(conn_mgr, host, commands, "configure_bpdu_guard")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_storm_control(host: str, interface: str, level: float, traffic_type: str) -> dict:
    """[WRITE] Set storm control threshold on an interface of an Arista EOS device.

    Configures storm control to limit broadcast, multicast, or unicast traffic as a
    percentage of link bandwidth. Requires NET_READ_ONLY=false.

    When to use: To protect against broadcast/multicast/unicast storms on an interface.
    Output: Confirmation of storm control configuration.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1').
        level: Storm control threshold as a percentage (0.01-100.0).
        traffic_type: Traffic type to limit ('broadcast', 'multicast', or 'unicast').
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    if not 0.01 <= level <= 100.0:
        return {
            "status": "error",
            "device": host,
            "error": f"Storm control level must be between 0.01 and 100.0, got {level}",
        }
    valid_types = {"broadcast", "multicast", "unicast"}
    if traffic_type not in valid_types:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid traffic type '{traffic_type}'. Must be one of: {sorted(valid_types)}",
        }
    commands = [f"interface {interface}", f"storm-control {traffic_type} level {level}"]
    return run_config_command(conn_mgr, host, commands, "configure_storm_control")
