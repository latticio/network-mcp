"""CPU, memory, environment, NTP, and logging tools for Arista EOS."""

from network_mcp.helpers import READ_ONLY, handle_tool_errors, run_show_command
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_cpu_usage(host: str) -> dict:
    """Get CPU utilization and top processes from an Arista EOS device.

    When to use: To investigate high CPU or identify resource-hungry processes.
    Output: CPU idle/user/system percentages and top processes.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show processes top once"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_memory_usage(host: str) -> dict:
    """Get memory utilization (total, free, used percentage) from an Arista EOS device.

    When to use: To check for memory pressure or leaks on the device.
    Output: Total, free, used memory in KB and used percentage.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show version"])
    if result["status"] == "error":
        return result
    version = result["data"][0]
    mem_total = version.get("memTotal", 0)
    mem_free = version.get("memFree", 0)
    mem_used = mem_total - mem_free
    mem_used_pct = round(mem_used / mem_total * 100, 1) if mem_total else 0
    return {
        "status": "success",
        "device": host,
        "data": {
            "total_kb": mem_total,
            "free_kb": mem_free,
            "used_kb": mem_used,
            "used_percent": mem_used_pct,
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_environment_temp(host: str) -> dict:
    """Get temperature sensor readings from an Arista EOS device.

    When to use: To check for overheating or thermal issues.
    Output: Sensor readings with current temperature and threshold status.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show environment temperature"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_environment_power(host: str) -> dict:
    """Get power supply status and readings from an Arista EOS device.

    When to use: To verify power supply redundancy and status.
    Output: PSU status, input/output power readings.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show environment power"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_environment_cooling(host: str) -> dict:
    """Get fan status and speed readings from an Arista EOS device.

    When to use: To check fan operation and airflow status.
    Output: Fan tray status and speed readings.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show environment cooling"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_ntp_status(host: str) -> dict:
    """Get NTP synchronization status and peer associations from an Arista EOS device.

    When to use: To verify time synchronization or diagnose clock drift.
    Output: Sync status, peer associations with stratum, offset, and jitter.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show ntp status", "show ntp associations"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {
            "ntp_status": result["data"][0],
            "associations": result["data"][1],
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_logging(host: str, lines: int = 50) -> dict:
    """Get recent syslog messages from an Arista EOS device.

    When to use: To review recent syslog messages for errors or events.
    Output: Recent log lines (configurable count, default 50).

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        lines: Number of recent log lines to retrieve (default: 50).
    """
    if lines < 1 or lines > 10000:
        return {"status": "error", "device": host, "error": "lines must be between 1 and 10000"}
    result = run_show_command(conn_mgr, host, [f"show logging last {lines}"], encoding="text")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {"output": result["data"][0].get("output", "")},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_reload_cause(host: str) -> dict:
    """Get the last reload/reboot reason from an Arista EOS device.

    When to use: To determine why a device last rebooted (crash, user, power loss).
    Output: Last reload reason and timestamp.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show reload cause"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }
