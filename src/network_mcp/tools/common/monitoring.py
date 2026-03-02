"""Vendor-agnostic monitoring and environment tools."""

from __future__ import annotations

import logging

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import READ_ONLY, handle_tool_errors
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_cpu_usage(host: str) -> dict:
    """Get CPU utilization from any supported network device.

    Returns CPU usage percentage from the device. Works with Arista EOS,
    Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        data = driver.get_cpu_memory()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_cpu_memory is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"cpu_percent": data["cpu_percent"]},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_memory_usage(host: str) -> dict:
    """Get memory utilization from any supported network device.

    Returns total, used, and free memory in KB. Works with Arista EOS,
    Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        data = driver.get_cpu_memory()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_cpu_memory is not supported on {driver.platform}",
        }
    mem_total = data["memory_total"]
    mem_used = data["memory_used"]
    mem_free = data["memory_free"]
    mem_percent = round(mem_used / mem_total * 100, 1) if mem_total else 0.0
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {
            "memory_total_kb": mem_total,
            "memory_used_kb": mem_used,
            "memory_free_kb": mem_free,
            "memory_percent": mem_percent,
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_environment_temp(host: str) -> dict:
    """Get temperature sensor readings from any supported network device.

    Returns sensor names, values in Celsius, and threshold status.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        env = driver.get_environment()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_environment is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"temperature": env.get("temperature", [])},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_environment_power(host: str) -> dict:
    """Get power supply status from any supported network device.

    Returns PSU name, status, and output power in watts.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        env = driver.get_environment()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_environment is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"power": env.get("power", [])},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_environment_cooling(host: str) -> dict:
    """Get fan/cooling status from any supported network device.

    Returns fan tray names and operational status.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        env = driver.get_environment()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_environment is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"fans": env.get("fans", [])},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_ntp_status(host: str) -> dict:
    """Get NTP synchronization status from any supported network device.

    Returns sync state, reference peer, and peer list with stratum/offset/jitter.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    try:
        data = driver.get_ntp_status()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_ntp_status is not supported on {driver.platform}",
        }
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": data,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_device_health(host: str) -> dict:
    """Comprehensive health check for any supported network device.

    Combines CPU, memory, temperature, fan, and power status into a
    single response with an overall health assessment. Works with
    Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    driver = conn_mgr.get_driver(host)
    health: dict = {}
    issues: list[str] = []

    # CPU and memory
    try:
        cpu_mem = driver.get_cpu_memory()
        health["cpu_percent"] = cpu_mem["cpu_percent"]
        health["memory_total_kb"] = cpu_mem["memory_total"]
        health["memory_used_kb"] = cpu_mem["memory_used"]
        health["memory_free_kb"] = cpu_mem["memory_free"]
        mem_total = cpu_mem["memory_total"]
        mem_used = cpu_mem["memory_used"]
        health["memory_percent"] = round(mem_used / mem_total * 100, 1) if mem_total else 0.0
        if cpu_mem["cpu_percent"] > 90:
            issues.append(f"High CPU utilization: {cpu_mem['cpu_percent']}%")
        if health["memory_percent"] > 90:
            issues.append(f"High memory utilization: {health['memory_percent']}%")
    except NotSupportedError:
        health["cpu_memory"] = "not_supported"

    # Environment (temperature, fans, power)
    try:
        env = driver.get_environment()
        health["temperature"] = env.get("temperature", [])
        health["fans"] = env.get("fans", [])
        health["power"] = env.get("power", [])
        for sensor in health["temperature"]:
            if sensor.get("status") == "critical":
                issues.append(f"Critical temperature on {sensor.get('sensor', 'unknown')}: {sensor.get('value')}C")
            elif sensor.get("status") == "warning":
                issues.append(f"Warning temperature on {sensor.get('sensor', 'unknown')}: {sensor.get('value')}C")
        for fan in health["fans"]:
            if fan.get("status") != "ok":
                issues.append(f"Fan issue: {fan.get('name', 'unknown')} status={fan.get('status')}")
        for psu in health["power"]:
            if psu.get("status") != "ok":
                issues.append(f"PSU issue: {psu.get('name', 'unknown')} status={psu.get('status')}")
    except NotSupportedError:
        health["environment"] = "not_supported"

    health["issues"] = issues
    health["overall"] = "healthy" if not issues else "degraded"

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": health,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_logging(host: str, lines: int = 50) -> dict:
    """Get recent syslog messages from any supported network device.

    Retrieves recent log entries using the device's native logging command.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        lines: Number of recent log lines to retrieve (default 50, max 10000).
    """
    if lines < 1 or lines > 10000:
        return {"status": "error", "device": host, "error": "lines must be between 1 and 10000"}

    driver = conn_mgr.get_driver(host)

    # Build vendor-appropriate logging command
    platform = driver.platform
    if platform == "eos":
        cmd = f"show logging last {lines}"
    elif platform in ("iosxe", "nxos"):
        cmd = f"show logging last {lines}"
    elif platform == "junos":
        cmd = f"show log messages | last {lines}"
    else:
        cmd = f"show logging last {lines}"

    try:
        result = driver.run_show([cmd], encoding="text")
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"Logging retrieval is not supported on {driver.platform}",
        }

    output = ""
    if result and isinstance(result[0], dict):
        output = result[0].get("output", "")
    elif result and isinstance(result[0], str):
        output = result[0]

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"lines_requested": lines, "output": output},
    }
