"""QoS monitoring tools for Arista EOS."""

from network_mcp.helpers import READ_ONLY, run_show_command, validate_cli_param
from network_mcp.server import conn_mgr, mcp

VALID_TRAFFIC_POLICY_DIRECTIONS = ("input", "output")


@mcp.tool(annotations=READ_ONLY)
def eos_get_policy_maps(host: str, name: str | None = None) -> dict:
    """Get QoS policy-map configuration from an Arista EOS device.

    Shows configured policy-maps with their class-maps, actions, and match criteria.
    Optionally filter by a specific policy-map name.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: Specific policy-map name to query (optional, returns all if not set).
    """
    if name is not None:
        name_err = validate_cli_param(name, "name")
        if name_err:
            return {"status": "error", "device": host, "error": name_err}
    cmd = f"show policy-map {name}" if name else "show policy-map"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_class_maps(host: str, name: str | None = None) -> dict:
    """Get QoS class-map configuration from an Arista EOS device.

    Shows configured class-maps with their match criteria. Optionally filter
    by a specific class-map name.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        name: Specific class-map name to query (optional, returns all if not set).
    """
    if name is not None:
        name_err = validate_cli_param(name, "name")
        if name_err:
            return {"status": "error", "device": host, "error": name_err}
    cmd = f"show class-map {name}" if name else "show class-map"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_traffic_policy(host: str, direction: str | None = None) -> dict:
    """Get traffic-policy configuration from an Arista EOS device.

    Shows traffic-policy definitions and their counters. Optionally filter by direction.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        direction: Filter by direction — 'input' or 'output' (optional, returns all if not set).
    """
    if direction is not None and direction not in VALID_TRAFFIC_POLICY_DIRECTIONS:
        return {
            "status": "error",
            "device": host,
            "error": (f"Invalid direction '{direction}'. Must be one of: {', '.join(VALID_TRAFFIC_POLICY_DIRECTIONS)}"),
        }
    cmd = "show traffic-policy"
    if direction:
        cmd = f"show traffic-policy {direction}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }
