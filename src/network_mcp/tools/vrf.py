"""VRF (Virtual Routing and Forwarding) tools for Arista EOS."""

from network_mcp.helpers import (
    READ_ONLY,
    WRITE_SAFE,
    run_config_command,
    run_show_command,
    validate_cli_param,
    validate_interface_name,
    validate_route_distinguisher,
)
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
def eos_get_vrfs(host: str) -> dict:
    """Get all VRFs (Virtual Routing and Forwarding instances) on an Arista EOS device.

    Shows configured VRFs with their route distinguisher, protocols, and interface
    assignments. VRFs provide network segmentation — most EVPN/VXLAN fabrics use
    multiple VRFs for tenant isolation.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show vrf"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0].get("vrfs", {}),
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_vrf_detail(host: str, name: str) -> dict:
    """Get detailed information for a specific VRF on an Arista EOS device.

    Shows the VRF's route distinguisher, route targets, associated interfaces,
    routing protocols, and route counts.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: VRF name to query.
    """
    name_err = validate_cli_param(name, "name")
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    result = run_show_command(conn_mgr, host, [f"show vrf {name}"])
    if result["status"] == "error":
        return result
    vrf_data = result["data"][0].get("vrfs", {}).get(name, {})
    if not vrf_data:
        return {
            "status": "error",
            "device": host,
            "error": f"VRF {name} not found",
        }
    return {
        "status": "success",
        "device": host,
        "data": vrf_data,
    }


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_vrf(host: str, name: str, rd: str, route_targets: list[dict]) -> dict:
    """[WRITE] Create a VRF with route distinguisher and route targets on an Arista EOS device.

    Requires NET_READ_ONLY=false. Creates a VRF instance with the specified RD and
    import/export route targets for L3VPN or EVPN fabrics.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: VRF instance name (e.g., "PROD", "TENANT-A").
        rd: Route Distinguisher in ASN:nn (e.g., "65001:100") or IP:nn (e.g., "10.0.0.1:100") format.
        route_targets: List of route target dicts, each with "rt" (string, e.g., "65001:100")
            and "direction" ("import", "export", or "both").
    """
    name_err = validate_cli_param(name, "name")
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    rd_err = validate_route_distinguisher(rd)
    if rd_err:
        return {"status": "error", "device": host, "error": rd_err}
    if not route_targets:
        return {"status": "error", "device": host, "error": "At least one route target is required"}
    valid_directions = {"import", "export", "both"}
    commands = [f"vrf instance {name}", f"rd {rd}"]
    for rt_entry in route_targets:
        rt = rt_entry.get("rt", "")
        direction = rt_entry.get("direction", "")
        rt_err = validate_cli_param(rt, "route_target")
        if rt_err:
            return {"status": "error", "device": host, "error": rt_err}
        if direction not in valid_directions:
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid route target direction: {direction}. Must be one of: import, export, both",
            }
        if direction in ("import", "both"):
            commands.append(f"route-target import {rt}")
        if direction in ("export", "both"):
            commands.append(f"route-target export {rt}")
    return run_config_command(conn_mgr, host, commands, "create_vrf")


@mcp.tool(annotations=WRITE_SAFE)
def eos_assign_interface_to_vrf(host: str, interface: str, vrf: str) -> dict:
    """[WRITE] Assign an interface to a VRF on an Arista EOS device.

    Requires NET_READ_ONLY=false. WARNING: Moving an interface to a VRF removes its
    existing IP configuration. Re-apply IP addresses after VRF assignment.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., "Ethernet1", "Loopback0", "Vlan100").
        vrf: VRF name to assign the interface to.
    """
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    vrf_err = validate_cli_param(vrf, "vrf")
    if vrf_err:
        return {"status": "error", "device": host, "error": vrf_err}
    commands = [f"interface {interface}", f"vrf {vrf}"]
    return run_config_command(conn_mgr, host, commands, "assign_interface_to_vrf")


@mcp.tool(annotations=WRITE_SAFE)
def eos_configure_vrf_route_leaking(host: str, source_vrf: str, dest_vrf: str, prefix_list: str) -> dict:
    """[WRITE] Configure inter-VRF route leaking via BGP on an Arista EOS device.

    Requires NET_READ_ONLY=false. Imports routes from source_vrf into dest_vrf,
    filtered by a prefix-list. This uses BGP-based route leaking and requires BGP
    to be configured on the device with an existing 'router bgp' stanza.

    The tool fetches the device's BGP ASN from 'show ip bgp summary' to build the
    correct 'router bgp {asn}' context.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        source_vrf: VRF to export/leak routes from.
        dest_vrf: VRF to import leaked routes into.
        prefix_list: Name of the prefix-list to filter leaked routes.
    """
    src_err = validate_cli_param(source_vrf, "source_vrf")
    if src_err:
        return {"status": "error", "device": host, "error": src_err}
    dst_err = validate_cli_param(dest_vrf, "dest_vrf")
    if dst_err:
        return {"status": "error", "device": host, "error": dst_err}
    pl_err = validate_cli_param(prefix_list, "prefix_list")
    if pl_err:
        return {"status": "error", "device": host, "error": pl_err}
    # Fetch the BGP ASN from the device
    bgp_result = run_show_command(conn_mgr, host, ["show ip bgp summary"])
    if bgp_result["status"] == "error":
        return bgp_result
    vrfs_data = bgp_result["data"][0].get("vrfs", {})
    default_vrf = vrfs_data.get("default", {})
    asn = default_vrf.get("asn", "")
    if not asn:
        return {
            "status": "error",
            "device": host,
            "error": "BGP is not configured on this device. Route leaking requires an active BGP process.",
        }
    commands = [
        f"router bgp {asn}",
        f"vrf {dest_vrf}",
        "address-family ipv4",
        f"import vrf {source_vrf} prefix-list {prefix_list}",
    ]
    return run_config_command(conn_mgr, host, commands, "configure_vrf_route_leaking")
