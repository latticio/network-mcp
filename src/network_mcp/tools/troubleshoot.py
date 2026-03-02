"""Ping, traceroute, and interface flap detection tools for Arista EOS."""

from network_mcp.helpers import (
    READ_ONLY,
    handle_tool_errors,
    run_show_command,
    validate_host,
    validate_interface_name,
    validate_ip_or_hostname,
)
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
def eos_ping_from_device(host: str, destination: str, source: str | None = None, count: int = 5) -> dict:
    """Execute a ping from an Arista EOS device to test reachability.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        destination: Target IP address or hostname to ping.
        source: Source IP or interface to ping from (optional).
        count: Number of ping packets to send (default: 5).
    """
    dest_err = validate_ip_or_hostname(destination, "destination")
    if dest_err:
        return {"status": "error", "device": host, "error": dest_err}
    if source:
        src_err = validate_ip_or_hostname(source, "source")
        if src_err:
            return {"status": "error", "device": host, "error": src_err}
    if count < 1 or count > 100:
        return {"status": "error", "device": host, "error": "count must be between 1 and 100"}
    cmd = f"ping {destination} repeat {count}"
    if source:
        cmd = f"ping {destination} source {source} repeat {count}"
    result = run_show_command(conn_mgr, host, [cmd], encoding="text")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {"output": result["data"][0].get("output", "")},
    }


@mcp.tool(annotations=READ_ONLY)
def eos_traceroute_from_device(host: str, destination: str, source: str | None = None) -> dict:
    """Execute a traceroute from an Arista EOS device to trace the path to a destination.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        destination: Target IP address or hostname to trace.
        source: Source IP or interface for traceroute (optional).
    """
    dest_err = validate_ip_or_hostname(destination, "destination")
    if dest_err:
        return {"status": "error", "device": host, "error": dest_err}
    if source:
        src_err = validate_ip_or_hostname(source, "source")
        if src_err:
            return {"status": "error", "device": host, "error": src_err}
    cmd = f"traceroute {destination}"
    if source:
        cmd = f"traceroute {destination} source {source}"
    result = run_show_command(conn_mgr, host, [cmd], encoding="text")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {"output": result["data"][0].get("output", "")},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_check_interface_flaps(host: str, interface: str | None = None, log_lines: int = 100) -> dict:
    """Check for recent interface flapping on an Arista EOS device by examining interface status and logs.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Specific interface to check (optional, checks all if not set).
        log_lines: Number of recent log lines to search for flap events (default: 100).
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    if interface:
        intf_err = validate_interface_name(interface)
        if intf_err:
            return {"status": "error", "device": host, "error": intf_err}

    # Get interface status
    status_result = run_show_command(conn_mgr, host, ["show interfaces status"])
    if status_result["status"] == "error":
        return status_result
    interface_status = status_result["data"][0].get("interfaceStatuses", {})

    # Get specific interface detail if requested
    intf_detail = None
    if interface:
        intf_result = run_show_command(conn_mgr, host, [f"show interfaces {interface}"])
        if intf_result["status"] == "success":
            intf_detail = intf_result["data"][0].get("interfaces", {})

    # Get log lines (text encoding required)
    log_result = run_show_command(conn_mgr, host, [f"show logging last {log_lines}"], encoding="text")
    if log_result["status"] == "error":
        return log_result
    log_output = log_result["data"][0].get("output", "")

    # Filter log lines for interface up/down events
    flap_lines = []
    for line in log_output.splitlines():
        if "changed state to" in line.lower() or "link status changed" in line.lower():
            if interface is None or (interface and interface.lower() in line.lower()):
                flap_lines.append(line.strip())

    data: dict = {
        "flap_events": flap_lines,
        "flap_count": len(flap_lines),
    }

    if interface and interface in interface_status:
        data["current_status"] = interface_status[interface]
    elif interface and intf_detail and interface in intf_detail:
        data["current_status"] = intf_detail[interface]

    return {
        "status": "success",
        "device": host,
        "data": data,
    }
