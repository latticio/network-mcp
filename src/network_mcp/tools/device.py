"""Device information and health tools for Arista EOS."""

from network_mcp.helpers import (
    READ_ONLY,
    circuit_breaker_registry,
    command_cache,
    handle_tool_errors,
    run_show_command,
)
from network_mcp.observability import PROMETHEUS_AVAILABLE, get_server_health, metrics_collector
from network_mcp.schemas import DeviceInfoOutput, ServerHealthOutput
from network_mcp.server import conn_mgr, get_module_status, mcp


@mcp.tool(annotations=READ_ONLY, structured_output=True)
@handle_tool_errors
def eos_get_device_info(host: str) -> DeviceInfoOutput:
    """Get version, model, serial number, and uptime from an Arista EOS device.

    When to use: First tool for initial device discovery.
    Output: hostname, model, EOS version, serial, uptime, memory.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show version"])
    if result["status"] == "error":
        return result  # type: ignore[return-value]
    version = result["data"][0]
    return {  # type: ignore[return-value]
        "status": "success",
        "device": host,
        "data": {
            "hostname": version.get("hostname"),
            "model": version.get("modelName"),
            "eos_version": version.get("version"),
            "serial_number": version.get("serialNumber"),
            "system_mac": version.get("systemMacAddress"),
            "uptime_seconds": version.get("uptime"),
            "total_memory": version.get("memTotal"),
            "free_memory": version.get("memFree"),
            "hardware_revision": version.get("hardwareRevision"),
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_device_health(host: str) -> dict:
    """Get comprehensive health overview: CPU, memory, temperature, fans, and power supplies from an Arista EOS device.

    When to use: Comprehensive health check when investigating device issues.
    Output: CPU, memory utilization, temperature, fan, power status.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(
        conn_mgr,
        host,
        [
            "show version",
            "show processes top once",
            "show environment temperature",
            "show environment cooling",
            "show environment power",
        ],
    )
    if result["status"] == "error":
        return result

    version = result["data"][0]
    processes = result["data"][1]
    temperature = result["data"][2]
    cooling = result["data"][3]
    power = result["data"][4]

    mem_total = version.get("memTotal", 0)
    mem_free = version.get("memFree", 0)
    mem_used_pct = round((mem_total - mem_free) / mem_total * 100, 1) if mem_total else 0

    return {
        "status": "success",
        "device": host,
        "data": {
            "memory": {
                "total_kb": mem_total,
                "free_kb": mem_free,
                "used_percent": mem_used_pct,
            },
            "cpu": {
                "processes": processes.get("cpuInfo", {}),
            },
            "temperature": {
                "sensors": temperature.get("tempSensors", []),
                "system_status": temperature.get("systemStatus", "unknown"),
            },
            "cooling": cooling.get("fanTraySlots", cooling.get("powerSupplySlots", {})),
            "power": power.get("powerSupplies", {}),
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_hardware_inventory(host: str) -> dict:
    """Get hardware inventory including modules, line cards, fans, and PSUs with part numbers from an Arista EOS device.

    When to use: To identify installed hardware modules, serial numbers, or transceiver types.
    Output: Module list with part numbers, serial numbers, descriptions.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show inventory"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_running_extensions(host: str) -> dict:
    """Get installed EOS extensions and packages from an Arista EOS device.

    When to use: To check installed EOS extensions (RPM packages).
    Output: Extension names, versions, and install status.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show extensions"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_users(host: str) -> dict:
    """Get currently logged-in users and active sessions on an Arista EOS device.

    When to use: To audit active user sessions or check who is logged in.
    Output: Connected users with connection type and idle time.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show users"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_hardware_capacity(host: str) -> dict:
    """Get hardware forwarding table capacity and TCAM utilization on an Arista EOS device.

    When to use: To check TCAM/forwarding table utilization before adding routes or ACLs.
    Output: Table utilization with used/max/free counts per table.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show hardware capacity"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_hardware_drops(host: str) -> dict:
    """Get hardware drop counters showing packets dropped by the forwarding ASIC on an Arista EOS device.

    When to use: To investigate packet loss or forwarding issues.
    Output: Drop counters by reason and hardware pipeline stage.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show hardware counter drop"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_terminattr_status(host: str) -> dict:
    """Get TerminAttr streaming telemetry agent status on an Arista EOS device.

    When to use: To verify CloudVision connectivity or streaming telemetry status.
    Output: Agent running state, version, and CVP connection status.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show daemon TerminAttr"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_cache_clear(host: str | None = None) -> dict:
    """Clear cached command results. Clears all caches if no host specified.

    When to use: After out-of-band config changes or to force fresh data retrieval.
    Output: Count of cache entries cleared.

    Args:
        host: Optional hostname or IP. If provided, clears only that host's cache.
              If omitted, clears all cached data.
    """
    if host:
        count = command_cache.invalidate_host(host)
        return {
            "status": "success",
            "message": f"Cleared {count} cached entries for host '{host}'",
            "entries_cleared": count,
        }
    count = command_cache.clear()
    return {
        "status": "success",
        "message": f"Cleared all {count} cached entries",
        "entries_cleared": count,
    }


@mcp.tool(annotations=READ_ONLY)
def eos_cache_stats() -> dict:
    """Get command cache statistics including hit rate, entry count, and memory usage.

    When to use: To monitor cache effectiveness and diagnose stale data issues.
    Output: Hit rate, entry count, memory usage.
    """
    return {
        "status": "success",
        "data": command_cache.stats(),
    }


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def eos_server_health() -> ServerHealthOutput:
    """Get MCP server health status including connection pool, circuit breakers, cache, and uptime.

    When to use: To check MCP server operational status and connection pool health.
    Output: Server status (healthy/degraded), connection pool stats, circuit breaker states.
    """
    health = get_server_health(
        conn_mgr, command_cache, circuit_breaker_registry, metrics_collector, get_module_status()
    )
    return {"status": "success", "data": health}  # type: ignore[return-value]


@mcp.tool(annotations=READ_ONLY)
def eos_server_metrics(output_format: str = "json") -> dict:
    """Get server metrics: tool call counts, durations, cache stats, and rate limiter events.

    When to use: To get performance metrics for the MCP server itself.
    Output: Tool call counts, durations, cache stats. Supports JSON and Prometheus formats.

    Args:
        output_format: Output format — "json" (default) or "prometheus" (Prometheus exposition format).
                       Prometheus format works without the prometheus-client library installed.
    """
    if output_format == "prometheus":
        return {
            "status": "success",
            "format": "prometheus",
            "data": metrics_collector.get_metrics_prometheus(),
            "prometheus_client_available": PROMETHEUS_AVAILABLE,
        }
    return {
        "status": "success",
        "format": "json",
        "data": metrics_collector.get_metrics_json(),
    }
