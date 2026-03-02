"""Interface status, counters, detail, and configuration tools for Arista EOS."""

from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    filter_fields,
    handle_tool_errors,
    paginate_dict,
    run_config_command,
    run_show_command,
    validate_duplex,
    validate_interface_name,
    validate_ip_address,
    validate_mtu,
    validate_speed,
    validate_vlan_id,
)
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_interfaces_status(
    host: str,
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get all interfaces with their status, VLAN, speed, and type from an Arista EOS device.

    When to use: Overview of all interfaces to find down links or check port modes.
    Output: Per-interface link status, speed, VLAN, description.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        fields: Optional list of field names to include per interface (e.g., ['linkStatus', 'bandwidth']).
        limit: Maximum number of interfaces to return (pagination). None returns all.
        offset: Number of interfaces to skip (pagination). Default: 0.
    """
    result = run_show_command(conn_mgr, host, ["show interfaces status"])
    if result["status"] == "error":
        return result
    data = result["data"][0].get("interfaceStatuses", {})
    if fields:
        data = {k: filter_fields(v, fields) for k, v in data.items()}
    pagination = None
    if limit is not None or offset:
        data, pagination = paginate_dict(data, limit, offset)
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
def eos_get_interface_detail(host: str, interface: str) -> dict:
    """Get detailed statistics and configuration for a specific interface on an Arista EOS device.

    When to use: Deep-dive into a specific interface's configuration and counters.
    Output: Interface config, line protocol status, MTU, counters, description.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1', 'Management1', 'Loopback0').
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    result = run_show_command(conn_mgr, host, [f"show interfaces {interface}"])
    if result["status"] == "error":
        return result
    interfaces = result["data"][0].get("interfaces", {})
    if interface not in interfaces:
        return {
            "status": "error",
            "device": host,
            "error": f"Interface {interface} not found",
        }
    return {
        "status": "success",
        "device": host,
        "data": interfaces[interface],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_interface_counters(
    host: str,
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get packet and byte counters for all interfaces on an Arista EOS device.

    When to use: To check traffic volume and throughput on interfaces.
    Output: Per-interface packet/byte counters for input and output.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        fields: Optional list of field names to include per interface (e.g., ['inOctets', 'outOctets']).
        limit: Maximum number of interfaces to return (pagination). None returns all.
        offset: Number of interfaces to skip (pagination). Default: 0.
    """
    result = run_show_command(conn_mgr, host, ["show interfaces counters"])
    if result["status"] == "error":
        return result
    data = result["data"][0].get("interfaces", {})
    if fields:
        data = {k: filter_fields(v, fields) for k, v in data.items()}
    pagination = None
    if limit is not None or offset:
        data, pagination = paginate_dict(data, limit, offset)
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
def eos_get_interface_errors(
    host: str,
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get error counters (CRC, runts, giants, etc.) for all interfaces on an Arista EOS device.

    When to use: To diagnose physical layer issues (bad cables, optics, speed mismatch).
    Output: Error counters (CRC, FCS, runts, giants) per interface.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        fields: Optional list of field names to include per interface (e.g., ['inputErrors', 'fcsErrors']).
        limit: Maximum number of interfaces to return (pagination). None returns all.
        offset: Number of interfaces to skip (pagination). Default: 0.
    """
    result = run_show_command(conn_mgr, host, ["show interfaces counters errors"])
    if result["status"] == "error":
        return result
    data = result["data"][0].get("interfaceErrorCounters", {})
    if fields:
        data = {k: filter_fields(v, fields) for k, v in data.items()}
    pagination = None
    if limit is not None or offset:
        data, pagination = paginate_dict(data, limit, offset)
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
def eos_get_ip_interfaces(
    host: str,
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get Layer 3 interfaces with IP addresses and status from an Arista EOS device.

    When to use: To find interface IP addresses or check L3 interface configuration.
    Output: IP addresses, VRF assignments, and protocol status per interface.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        fields: Optional list of field names to include per interface
            (e.g., ['interfaceAddress', 'lineProtocolStatus']).
        limit: Maximum number of interfaces to return (pagination). None returns all.
        offset: Number of interfaces to skip (pagination). Default: 0.
    """
    result = run_show_command(conn_mgr, host, ["show ip interface brief"])
    if result["status"] == "error":
        return result
    data = result["data"][0].get("interfaces", {})
    if fields:
        data = {k: filter_fields(v, fields) for k, v in data.items()}
    pagination = None
    if limit is not None or offset:
        data, pagination = paginate_dict(data, limit, offset)
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
def eos_get_transceiver_info(host: str, fields: list[str] | None = None) -> dict:
    """Get transceiver/optic information including type, power levels, and temperature from an Arista EOS device.

    When to use: To check optical power levels or identify transceiver types.
    Output: Rx/Tx power, temperature, voltage, media type per transceiver.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        fields: Optional list of field names to include per transceiver (e.g., ['mediaType', 'rxPower', 'txPower']).
    """
    result = run_show_command(conn_mgr, host, ["show interfaces transceiver"])
    if result["status"] == "error":
        return result
    data = result["data"][0].get("interfaces", {})
    if fields:
        data = {k: filter_fields(v, fields) for k, v in data.items()}
    return {
        "status": "success",
        "device": host,
        "data": data,
    }


# --- Write Tools ---


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_interface_description(host: str, interface: str, description: str) -> dict:
    """[WRITE] Set the description on an interface of an Arista EOS device.

    Updates the interface description without affecting traffic. Useful for documenting
    circuit IDs, connected devices, or purpose. Requires NET_READ_ONLY=false.

    When to use: To label interfaces with descriptions for documentation.
    Output: Confirmation of description change.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1', 'Port-Channel10').
        description: Description text to set on the interface.
    """
    err = validate_interface_name(interface)
    if err:
        return {"status": "error", "device": host, "error": err}
    if "\n" in description or "\r" in description or "!" in description:
        return {"status": "error", "device": host, "error": "Description cannot contain newlines or '!' characters"}
    if len(description) > 254:
        return {"status": "error", "device": host, "error": "Description must be 254 characters or fewer"}
    return run_config_command(
        conn_mgr,
        host,
        [f"interface {interface}", f"description {description}"],
        "configure_interface_description",
    )


@mcp.tool(annotations=DESTRUCTIVE)
def eos_shutdown_interface(host: str, interface: str) -> dict:
    """[WRITE] Administratively disable an interface on an Arista EOS device.

    Shuts down the interface, which will drop all traffic on it. Use with caution —
    this will cause a link-down event. Requires NET_READ_ONLY=false.

    When to use: To administratively disable an interface for maintenance.
    Output: Confirmation that the interface was shut down.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1').
    """
    err = validate_interface_name(interface)
    if err:
        return {"status": "error", "device": host, "error": err}
    return run_config_command(
        conn_mgr,
        host,
        [f"interface {interface}", "shutdown"],
        "shutdown_interface",
    )


@mcp.tool(annotations=WRITE_SAFE)
def eos_no_shutdown_interface(host: str, interface: str) -> dict:
    """[WRITE] Administratively enable an interface on an Arista EOS device.

    Removes the shutdown state, allowing the interface to come up. This is safe
    to use — it restores the interface to its default operational state.
    Requires NET_READ_ONLY=false.

    When to use: To re-enable a previously disabled interface.
    Output: Confirmation that the interface was enabled.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1').
    """
    err = validate_interface_name(interface)
    if err:
        return {"status": "error", "device": host, "error": err}
    return run_config_command(
        conn_mgr,
        host,
        [f"interface {interface}", "no shutdown"],
        "no_shutdown_interface",
    )


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_interface_speed(host: str, interface: str, speed: str, duplex: str = "auto") -> dict:
    """[WRITE] Set speed and duplex on an interface of an Arista EOS device.

    Configures the interface speed and duplex settings. Use 'auto' for auto-negotiation.
    Requires NET_READ_ONLY=false.

    When to use: To set or change the speed/duplex on a physical interface.
    Output: Confirmation of speed/duplex change.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1').
        speed: Speed setting ('auto', '10', '100', '1000', '2500', '5000', '10000', '25000',
               '40000', '50000', '100000').
        duplex: Duplex setting ('auto', 'full', 'half'). Default: 'auto'.
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    speed_err = validate_speed(speed)
    if speed_err:
        return {"status": "error", "device": host, "error": speed_err}
    duplex_err = validate_duplex(duplex)
    if duplex_err:
        return {"status": "error", "device": host, "error": duplex_err}
    commands = [f"interface {interface}", f"speed {speed}", f"duplex {duplex}"]
    return run_config_command(conn_mgr, host, commands, "configure_interface_speed")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_interface_mtu(host: str, interface: str, mtu: int) -> dict:
    """[WRITE] Set the MTU on an interface of an Arista EOS device.

    Configures the Maximum Transmission Unit size. Standard Ethernet is 1500,
    jumbo frames typically use 9214. Requires NET_READ_ONLY=false.

    When to use: To set jumbo frames or adjust MTU for specific requirements.
    Output: Confirmation of MTU change.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1', 'Port-Channel10').
        mtu: MTU size in bytes (68-9214).
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    mtu_err = validate_mtu(mtu)
    if mtu_err:
        return {"status": "error", "device": host, "error": mtu_err}
    commands = [f"interface {interface}", f"mtu {mtu}"]
    return run_config_command(conn_mgr, host, commands, "configure_interface_mtu")


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_svi(host: str, vlan_id: int, ip_address: str, mask: int) -> dict:
    """[WRITE] Create a VLAN interface (SVI) with an IP address on an Arista EOS device.

    Creates a Layer 3 VLAN interface and assigns an IP address. The interface
    is brought up automatically with 'no shutdown'. Requires NET_READ_ONLY=false.

    When to use: To create a routed VLAN interface for inter-VLAN routing.
    Output: Confirmation of SVI creation.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vlan_id: VLAN ID (1-4094) for the SVI.
        ip_address: IPv4 address to assign (e.g., '10.0.100.1').
        mask: Subnet mask length (0-32, e.g., 24 for /24).
    """
    vlan_err = validate_vlan_id(vlan_id)
    if vlan_err:
        return {"status": "error", "device": host, "error": vlan_err}
    ip_err = validate_ip_address(ip_address)
    if ip_err:
        return {"status": "error", "device": host, "error": ip_err}
    if not 0 <= mask <= 32:
        return {"status": "error", "device": host, "error": f"Subnet mask must be between 0 and 32, got {mask}"}
    commands = [f"interface vlan {vlan_id}", f"ip address {ip_address}/{mask}", "no shutdown"]
    return run_config_command(conn_mgr, host, commands, "create_svi")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_trunk(host: str, interface: str, allowed_vlans: str = "all", native_vlan: int | None = None) -> dict:
    """[WRITE] Configure an interface as a trunk port on an Arista EOS device.

    Sets the interface to trunk mode and configures allowed VLANs and optional
    native VLAN. Requires NET_READ_ONLY=false.

    When to use: To configure a trunk port for carrying multiple VLANs.
    Output: Confirmation of trunk configuration.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1', 'Port-Channel10').
        allowed_vlans: Allowed VLANs as a string (e.g., '100,200,300-400' or 'all'). Default: 'all'.
        native_vlan: Optional native VLAN ID (1-4094).
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    commands = [f"interface {interface}", "switchport mode trunk", f"switchport trunk allowed vlan {allowed_vlans}"]
    if native_vlan is not None:
        vlan_err = validate_vlan_id(native_vlan)
        if vlan_err:
            return {"status": "error", "device": host, "error": f"Invalid native VLAN: {vlan_err}"}
        commands.append(f"switchport trunk native vlan {native_vlan}")
    return run_config_command(conn_mgr, host, commands, "configure_trunk")
