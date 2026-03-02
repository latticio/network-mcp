"""VLAN tools (read and write) for Arista EOS."""

from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    filter_fields,
    paginate_dict,
    run_config_command,
    run_show_command,
    validate_interface_name,
    validate_vlan_id,
    validate_vlan_name,
)
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
def eos_get_vlans(
    host: str,
    fields: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Get all VLANs with their names and assigned ports from an Arista EOS device.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        fields: Optional list of field names to include per VLAN (e.g., ['name', 'status']).
        limit: Maximum number of VLANs to return (pagination). None returns all.
        offset: Number of VLANs to skip (pagination). Default: 0.
    """
    result = run_show_command(conn_mgr, host, ["show vlan"])
    if result["status"] == "error":
        return result
    data = result["data"][0].get("vlans", {})
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
def eos_get_vlan_detail(host: str, vlan_id: int) -> dict:
    """Get detailed information for a specific VLAN on an Arista EOS device.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vlan_id: VLAN ID to query (1-4094).
    """
    vlan_err = validate_vlan_id(vlan_id)
    if vlan_err:
        return {"status": "error", "device": host, "error": vlan_err}

    result = run_show_command(conn_mgr, host, [f"show vlan {vlan_id}"])
    if result["status"] == "error":
        return result
    vlans = result["data"][0].get("vlans", {})
    vlan_key = str(vlan_id)
    if vlan_key not in vlans:
        return {
            "status": "error",
            "device": host,
            "error": f"VLAN {vlan_id} not found",
        }
    return {
        "status": "success",
        "device": host,
        "data": vlans[vlan_key],
    }


# --- Write Tools ---


@mcp.tool(annotations=WRITE_SAFE)
def eos_create_vlan(host: str, vlan_id: int, name: str) -> dict:
    """[WRITE] Create a new VLAN on an Arista EOS device.

    Creates the VLAN with the specified ID and name. This modifies the device
    configuration immediately. Requires NET_READ_ONLY=false.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vlan_id: VLAN ID to create (1-4094).
        name: Descriptive name for the VLAN (e.g., 'SERVERS', 'MGMT').
    """
    err = validate_vlan_id(vlan_id)
    if err:
        return {"status": "error", "device": host, "error": err}
    name_err = validate_vlan_name(name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    return run_config_command(conn_mgr, host, [f"vlan {vlan_id}", f"name {name}"], "create_vlan")


@mcp.tool(annotations=DESTRUCTIVE)
def eos_delete_vlan(host: str, vlan_id: int) -> dict:
    """[WRITE] Delete a VLAN from an Arista EOS device.

    Permanently removes the VLAN. Any interfaces assigned to this VLAN will become
    unassigned. Requires NET_READ_ONLY=false.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vlan_id: VLAN ID to delete (1-4094).
    """
    err = validate_vlan_id(vlan_id)
    if err:
        return {"status": "error", "device": host, "error": err}
    return run_config_command(conn_mgr, host, [f"no vlan {vlan_id}"], "delete_vlan")


@mcp.tool(annotations=WRITE_SAFE)
def eos_rename_vlan(host: str, vlan_id: int, name: str) -> dict:
    """[WRITE] Rename an existing VLAN on an Arista EOS device.

    Changes the VLAN name without affecting interface assignments or traffic.
    Requires NET_READ_ONLY=false.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vlan_id: VLAN ID to rename (1-4094).
        name: New name for the VLAN.
    """
    err = validate_vlan_id(vlan_id)
    if err:
        return {"status": "error", "device": host, "error": err}
    name_err = validate_vlan_name(name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    return run_config_command(conn_mgr, host, [f"vlan {vlan_id}", f"name {name}"], "rename_vlan")


@mcp.tool(annotations=WRITE_SAFE)
def eos_assign_interface_vlan(host: str, interface: str, vlan_id: int) -> dict:
    """[WRITE] Assign an access VLAN to an interface on an Arista EOS device.

    Sets the interface to access mode on the specified VLAN. The interface must
    not be a routed port. Requires NET_READ_ONLY=false.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name (e.g., 'Ethernet1').
        vlan_id: VLAN ID to assign (1-4094).
    """
    err = validate_vlan_id(vlan_id)
    if err:
        return {"status": "error", "device": host, "error": err}
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}
    return run_config_command(
        conn_mgr,
        host,
        [
            f"interface {interface}",
            "switchport mode access",
            f"switchport access vlan {vlan_id}",
        ],
        "assign_interface_vlan",
    )
