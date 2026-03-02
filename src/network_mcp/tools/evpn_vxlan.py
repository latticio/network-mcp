"""EVPN/VXLAN data center fabric tools for Arista EOS."""

from network_mcp.helpers import READ_ONLY, run_show_command, validate_vlan_id
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
def eos_get_vxlan_interface(host: str) -> dict:
    """Get VXLAN tunnel interface (Vxlan1) configuration and status on an Arista EOS device.

    Shows the VXLAN source interface, VTEP IP, UDP port, VNI-to-VLAN mappings,
    VRF-to-VNI mappings, and flood list. This is the primary VXLAN status command.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show interfaces vxlan1"])
    if result["status"] == "error":
        return result
    vxlan1 = result["data"][0].get("interfaces", {}).get("Vxlan1", {})
    return {
        "status": "success",
        "device": host,
        "data": vxlan1,
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_vxlan_vtep(host: str) -> dict:
    """Get discovered remote VTEPs (VXLAN Tunnel Endpoints) on an Arista EOS device.

    Lists all remote VTEPs this device has learned about, showing the overlay network
    connectivity. Missing VTEPs indicate EVPN control plane issues.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show vxlan vtep"])
    if result["status"] == "error":
        return result
    vtep_data = result["data"][0].get("interfaces", {}).get("Vxlan1", {})
    return {
        "status": "success",
        "device": host,
        "data": {
            "vteps": vtep_data.get("vteps", []),
        },
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_vxlan_vni(host: str) -> dict:
    """Get VNI (VXLAN Network Identifier) to VLAN and VRF mappings on an Arista EOS device.

    Shows L2 VNI-to-VLAN bindings and L3 VNI-to-VRF bindings. Critical for verifying
    that the EVPN/VXLAN data plane is correctly mapped.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show vxlan vni"])
    if result["status"] == "error":
        return result
    vxlan_data = result["data"][0].get("vxlanIntfs", {}).get("Vxlan1", {})
    return {
        "status": "success",
        "device": host,
        "data": {
            "vniBindings": vxlan_data.get("vniBindings", {}),
            "vniBindingsToVrf": vxlan_data.get("vniBindingsToVrf", {}),
        },
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_vxlan_address_table(host: str, vlan: int | None = None) -> dict:
    """Get VXLAN remote MAC address table on an Arista EOS device.

    Shows MAC addresses learned via VXLAN from remote VTEPs with their associated
    VNI and remote VTEP IP. Use to verify L2 learning across the VXLAN fabric.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vlan: Filter by VLAN ID (optional).
    """
    if vlan is not None:
        vlan_err = validate_vlan_id(vlan)
        if vlan_err:
            return {"status": "error", "device": host, "error": vlan_err}
        cmd = f"show vxlan address-table vlan {vlan}"
    else:
        cmd = "show vxlan address-table"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {
            "addresses": result["data"][0].get("addresses", {}),
        },
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_vxlan_config_sanity(host: str) -> dict:
    """Run VXLAN configuration sanity check on an Arista EOS device.

    Validates VXLAN configuration including source interface, VTEP reachability,
    VLAN-to-VNI mappings, and MLAG consistency (if applicable). Returns pass/fail
    status for each category. This is the first tool to use when diagnosing VXLAN issues.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show vxlan config-sanity detail"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {
            "categories": result["data"][0].get("categories", {}),
        },
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_bgp_evpn_summary(host: str) -> dict:
    """Get BGP EVPN peer summary on an Arista EOS device.

    Shows all BGP neighbors in the EVPN address family with their state, prefix
    counts, and uptime. Peers not in Established state indicate overlay control
    plane issues.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show bgp evpn summary"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0].get("vrfs", {}),
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_bgp_evpn_instance(host: str) -> dict:
    """Get BGP EVPN instance details on an Arista EOS device.

    Shows EVPN instances with their RD (Route Distinguisher), import/export RT
    (Route Targets), and associated VNIs. Used to verify EVPN control plane
    configuration.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show bgp evpn instance"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {
            "evpnInstances": result["data"][0].get("evpnInstances", {}),
        },
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_evpn_route_type_macip(host: str, vni: int | None = None) -> dict:
    """Get EVPN Type-2 MAC/IP routes on an Arista EOS device.

    Type-2 routes carry MAC and optionally IP bindings learned via EVPN. These are
    the fundamental routes for L2 forwarding across the VXLAN fabric. Missing Type-2
    routes mean the host cannot be reached via VXLAN.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vni: Filter by VNI (optional).
    """
    if vni is not None:
        if vni < 1 or vni > 16777215:
            return {"status": "error", "device": host, "error": "VNI must be between 1 and 16777215"}
    cmd = "show bgp evpn route-type mac-ip"
    if vni is not None:
        cmd = f"{cmd} vni {vni}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {
            "evpnRoutes": result["data"][0].get("evpnRoutes", {}),
        },
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_evpn_route_type_imet(host: str) -> dict:
    """Get EVPN Type-3 IMET (Inclusive Multicast Ethernet Tag) routes on an Arista EOS device.

    Type-3 routes are used for BUM (Broadcast, Unknown unicast, Multicast) traffic
    replication across the VXLAN fabric. Each VTEP should advertise Type-3 routes
    for its locally configured VNIs. Missing Type-3 routes cause BUM flooding failures.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show bgp evpn route-type imet"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {
            "evpnRoutes": result["data"][0].get("evpnRoutes", {}),
        },
    }
