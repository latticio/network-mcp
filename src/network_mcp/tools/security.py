"""Security visibility tools for Arista EOS."""

from network_mcp.helpers import (
    READ_ONLY,
    WRITE_SAFE,
    run_config_command,
    run_show_command,
    validate_cli_param,
    validate_interface_name,
    validate_ip_address,
)
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
def eos_get_ip_access_lists(host: str, name: str | None = None) -> dict:
    """Get IP access lists (ACLs) configured on an Arista EOS device.

    Shows standard and extended ACLs with their rules, match criteria, and hit counters.
    Optionally filter by a specific ACL name.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: Specific ACL name to query (optional, returns all if not set).
    """
    if name is not None:
        name_err = validate_cli_param(name, "name")
        if name_err:
            return {"status": "error", "device": host, "error": name_err}
    cmd = f"show ip access-lists {name}" if name else "show ip access-lists"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0].get("aclList", []),
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_aaa_status(host: str) -> dict:
    """Get AAA (Authentication, Authorization, Accounting) configuration status on an Arista EOS device.

    Shows authentication methods, authorization policies, and accounting settings.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show aaa"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_radius_status(host: str) -> dict:
    """Get RADIUS server configuration and status on an Arista EOS device.

    Shows configured RADIUS servers with their reachability status, timeouts,
    and request/response counters.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show radius"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_tacacs_status(host: str) -> dict:
    """Get TACACS+ server configuration and status on an Arista EOS device.

    Shows configured TACACS+ servers with their reachability status, timeouts,
    and connection statistics.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show tacacs"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_copp_policy(host: str) -> dict:
    """Get Control Plane Policing (CoPP) policy on an Arista EOS device.

    Shows the policy-map applied to the control plane with traffic classes, rate
    limits, and drop counters. Important for detecting control plane overload.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show policy-map interface control-plane"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0].get("policyMaps", {}),
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_eapi_status(host: str) -> dict:
    """Get eAPI (management HTTP API) status on an Arista EOS device.

    Shows whether eAPI is enabled, which protocols (HTTP/HTTPS) are active,
    listening ports, and connected clients. This is the API that this MCP server
    uses to communicate with the device.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show management api http-commands"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


# --- ACL Assignment Tools ---

VALID_ACL_DIRECTIONS = ("in", "out")


@mcp.tool(annotations=WRITE_SAFE)
def eos_apply_acl_to_interface(host: str, interface: str, acl_name: str, direction: str) -> dict:
    """[WRITE] Apply an IP access list to an interface on an Arista EOS device. Requires NET_READ_ONLY=false.

    Applies the named ACL to the specified interface in the given direction.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1', 'Vlan100').
        acl_name: Name of the IP access list to apply.
        direction: Direction to apply — 'in' or 'out'.
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    acl_err = validate_cli_param(acl_name, "acl_name")
    if acl_err:
        return {"status": "error", "device": host, "error": acl_err}
    if direction not in VALID_ACL_DIRECTIONS:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid direction '{direction}'. Must be one of: {', '.join(VALID_ACL_DIRECTIONS)}",
        }
    commands = [f"interface {interface}", f"ip access-group {acl_name} {direction}"]
    return run_config_command(conn_mgr, host, commands, "apply_acl_to_interface")


@mcp.tool(annotations=WRITE_SAFE)
def eos_remove_acl_from_interface(host: str, interface: str, acl_name: str, direction: str) -> dict:
    """[WRITE] Remove an IP access list from an interface on an Arista EOS device. Requires NET_READ_ONLY=false.

    Removes the named ACL from the specified interface in the given direction.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1', 'Vlan100').
        acl_name: Name of the IP access list to remove.
        direction: Direction to remove — 'in' or 'out'.
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    acl_err = validate_cli_param(acl_name, "acl_name")
    if acl_err:
        return {"status": "error", "device": host, "error": acl_err}
    if direction not in VALID_ACL_DIRECTIONS:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid direction '{direction}'. Must be one of: {', '.join(VALID_ACL_DIRECTIONS)}",
        }
    commands = [f"interface {interface}", f"no ip access-group {acl_name} {direction}"]
    return run_config_command(conn_mgr, host, commands, "remove_acl_from_interface")


# --- ACL Configuration Tools ---

VALID_ACL_TYPES = ("standard", "extended")
VALID_ACL_ACTIONS = ("permit", "deny")


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_acl(host: str, name: str, acl_type: str, entries: list[dict]) -> dict:
    """[WRITE] Create an IP access control list on an Arista EOS device. Requires NET_READ_ONLY=false.

    Creates a standard or extended ACL with the specified entries.

    For standard ACLs, each entry needs: seq, action, source.
    For extended ACLs, each entry needs: seq, action, protocol, source, destination, and optional dst_port.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: ACL name.
        acl_type: ACL type — 'standard' or 'extended'.
        entries: List of ACL entry dicts. Each must have 'seq' (int), 'action' ('permit'/'deny'),
            and for standard: 'source'; for extended: 'protocol', 'source', 'destination',
            optional 'dst_port'.
    """
    name_err = validate_cli_param(name, "name")
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    if acl_type not in VALID_ACL_TYPES:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid acl_type '{acl_type}'. Must be one of: {', '.join(VALID_ACL_TYPES)}",
        }
    if not entries:
        return {"status": "error", "device": host, "error": "At least one ACL entry is required"}

    commands = [f"ip access-list {name}"]
    for entry in entries:
        seq = entry.get("seq")
        action = entry.get("action", "")
        if not isinstance(seq, int) or seq < 1:
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid sequence number: {seq}. Must be a positive integer",
            }
        if action not in VALID_ACL_ACTIONS:
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid action '{action}'. Must be one of: {', '.join(VALID_ACL_ACTIONS)}",
            }
        if acl_type == "standard":
            source = entry.get("source", "")
            src_err = validate_cli_param(source, "source")
            if src_err:
                return {"status": "error", "device": host, "error": src_err}
            commands.append(f"{seq} {action} {source}")
        else:
            protocol = entry.get("protocol", "")
            source = entry.get("source", "")
            destination = entry.get("destination", "")
            dst_port = entry.get("dst_port")
            for param_name, param_val in [("protocol", protocol), ("source", source), ("destination", destination)]:
                p_err = validate_cli_param(param_val, param_name)
                if p_err:
                    return {"status": "error", "device": host, "error": p_err}
            if dst_port is not None:
                port_err = validate_cli_param(str(dst_port), "dst_port")
                if port_err:
                    return {"status": "error", "device": host, "error": port_err}
                commands.append(f"{seq} {action} {protocol} {source} {destination} eq {dst_port}")
            else:
                commands.append(f"{seq} {action} {protocol} {source} {destination}")

    return run_config_command(conn_mgr, host, commands, "create_acl")


VALID_ACL_RULE_ACTIONS = ("add", "remove")


@mcp.tool(annotations=WRITE_SAFE)
def eos_modify_acl_rule(host: str, name: str, seq: int, action: str, entry: str = "") -> dict:
    """[WRITE] Add or remove a single ACL entry on an Arista EOS device. Requires NET_READ_ONLY=false.

    To add a rule, provide action='add' and the rule text in 'entry'.
    To remove a rule, provide action='remove' — only the sequence number is needed.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: ACL name.
        seq: Sequence number of the ACL entry.
        action: 'add' to add/replace a rule, 'remove' to delete it.
        entry: The ACL rule text (required when action is 'add', ignored for 'remove').
    """
    name_err = validate_cli_param(name, "name")
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    if not isinstance(seq, int) or seq < 1:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid sequence number: {seq}. Must be a positive integer",
        }
    if action not in VALID_ACL_RULE_ACTIONS:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid action '{action}'. Must be one of: {', '.join(VALID_ACL_RULE_ACTIONS)}",
        }
    if action == "add":
        entry_err = validate_cli_param(entry, "entry")
        if entry_err:
            return {"status": "error", "device": host, "error": entry_err}
        commands = [f"ip access-list {name}", f"{seq} {entry}"]
    else:
        commands = [f"ip access-list {name}", f"no {seq}"]
    return run_config_command(conn_mgr, host, commands, "modify_acl_rule")


@mcp.tool(annotations=WRITE_SAFE)
def eos_apply_acl(host: str, interface: str, acl_name: str, direction: str) -> dict:
    """[WRITE] Apply an IP access list to an interface on an Arista EOS device. Requires NET_READ_ONLY=false.

    Applies the named ACL to the specified interface in the given direction.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1', 'Vlan100').
        acl_name: Name of the IP access list to apply.
        direction: Direction to apply — 'in' or 'out'.
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    acl_err = validate_cli_param(acl_name, "acl_name")
    if acl_err:
        return {"status": "error", "device": host, "error": acl_err}
    if direction not in VALID_ACL_DIRECTIONS:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid direction '{direction}'. Must be one of: {', '.join(VALID_ACL_DIRECTIONS)}",
        }
    commands = [f"interface {interface}", f"ip access-group {acl_name} {direction}"]
    return run_config_command(conn_mgr, host, commands, "apply_acl")


# --- RADIUS Configuration Tools ---


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_radius_server(
    host: str, server_ip: str, key: str, auth_port: int = 1812, acct_port: int = 1813
) -> dict:
    """[WRITE] Add a RADIUS server on an Arista EOS device. Requires NET_READ_ONLY=false.

    Configures a RADIUS server with the specified IP, shared key, and ports.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        server_ip: RADIUS server IPv4 address (e.g., '10.1.1.100').
        key: RADIUS shared secret key.
        auth_port: Authentication port (default 1812).
        acct_port: Accounting port (default 1813).
    """
    ip_err = validate_ip_address(server_ip)
    if ip_err:
        return {"status": "error", "device": host, "error": ip_err}
    key_err = validate_cli_param(key, "key")
    if key_err:
        return {"status": "error", "device": host, "error": key_err}
    if not isinstance(auth_port, int) or auth_port < 1 or auth_port > 65535:
        return {"status": "error", "device": host, "error": f"Invalid auth_port: {auth_port}. Must be 1-65535"}
    if not isinstance(acct_port, int) or acct_port < 1 or acct_port > 65535:
        return {"status": "error", "device": host, "error": f"Invalid acct_port: {acct_port}. Must be 1-65535"}
    commands = [f"radius-server host {server_ip} auth-port {auth_port} acct-port {acct_port} key 7 {key}"]
    return run_config_command(conn_mgr, host, commands, "configure_radius_server")


# --- CoPP Configuration Tools ---


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_copp(host: str, class_name: str, rate: int, burst: int) -> dict:
    """[WRITE] Modify a CoPP policy-map class rate on an Arista EOS device. Requires NET_READ_ONLY=false.

    Adjusts the rate limit and burst size for a traffic class within the system CoPP policy-map
    applied to the control plane.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        class_name: Traffic class name within the CoPP policy-map (e.g., 'copp-system-acllog').
        rate: Rate limit in packets per second (positive integer).
        burst: Burst size in packets (positive integer).
    """
    cls_err = validate_cli_param(class_name, "class_name")
    if cls_err:
        return {"status": "error", "device": host, "error": cls_err}
    if not isinstance(rate, int) or rate < 1:
        return {"status": "error", "device": host, "error": f"Invalid rate: {rate}. Must be a positive integer"}
    if not isinstance(burst, int) or burst < 1:
        return {"status": "error", "device": host, "error": f"Invalid burst: {burst}. Must be a positive integer"}
    commands = [
        "policy-map copp-system-policy",
        f"class {class_name}",
        f"shape rate {rate} pps burst-size {burst} packets",
    ]
    return run_config_command(conn_mgr, host, commands, "configure_copp")


# --- SNMP Tools ---


@mcp.tool(annotations=READ_ONLY)
def eos_get_snmp_config(host: str) -> dict:
    """Get SNMP configuration from an Arista EOS device.

    Shows SNMP communities, users, groups, views, and trap hosts. Community strings
    are automatically redacted from the output for security.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show snmp"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_snmp_traps(host: str) -> dict:
    """Get SNMP trap configuration from an Arista EOS device.

    Shows configured SNMP trap receivers and which trap types are enabled.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show snmp trap"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }
