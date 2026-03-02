"""Cisco NX-OS vendor-specific general tools.

These tools expose NX-OS-specific features that don't have cross-vendor
equivalents: feature management and detailed system resource utilization.

NX-OS requires features to be explicitly enabled (e.g., ``feature bgp``,
``feature vpc``) which is a platform-specific concept.

Only loaded when the ``cisco`` extra is installed (httpx available).
"""

from __future__ import annotations

import logging

from network_mcp.helpers import READ_ONLY, handle_tool_errors, validate_host
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def nxos_get_feature_list(host: str) -> dict:
    """Get enabled/disabled feature list from a Cisco NX-OS device.

    NX-OS requires features to be explicitly enabled (e.g., ``feature bgp``,
    ``feature vpc``). Returns the complete feature list with their operational
    status and instance count.

    Args:
        host: Hostname, IP address, or inventory name of the NX-OS device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "nxos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Feature list is only available on NX-OS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show feature"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get feature list: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_feature_list(data),
    }


def _normalize_feature_list(data: dict) -> dict:
    """Normalize NX-API 'show feature' output to a structured format.

    NX-API returns feature data under ``TABLE_cfcFeatureCtrlTable.ROW_cfcFeatureCtrlTable``
    with feature name, instance, and status.
    """
    features: list[dict] = []
    enabled_count = 0

    feature_table = data.get("TABLE_cfcFeatureCtrlTable", {})
    if feature_table:
        rows = feature_table.get("ROW_cfcFeatureCtrlTable", [])
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            name = row.get("cfcFeatureCtrlName2", row.get("name", ""))
            status = row.get("cfcFeatureCtrlOpStatus2", row.get("status", ""))
            instance = _int_or_default(row.get("cfcFeatureCtrlOpStatusInstance", row.get("instance", 1)))
            enabled = status.lower() == "enabled" if isinstance(status, str) else False
            if enabled:
                enabled_count += 1
            features.append(
                {
                    "name": name,
                    "status": status,
                    "instance": instance,
                    "enabled": enabled,
                }
            )

    return {
        "features": features,
        "total_count": len(features),
        "enabled_count": enabled_count,
        "disabled_count": len(features) - enabled_count,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def nxos_get_system_resources(host: str) -> dict:
    """Get detailed system resources from a Cisco NX-OS device.

    Returns CPU utilization (1min/5min/15min), memory usage (total/used/free),
    and process count. More detailed than the vendor-agnostic monitoring tools.

    Args:
        host: Hostname, IP address, or inventory name of the NX-OS device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "nxos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"System resources is only available on NX-OS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show system resources"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get system resources: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_system_resources(data),
    }


def _normalize_system_resources(data: dict) -> dict:
    """Normalize NX-API 'show system resources' output to a structured format.

    NX-API returns CPU and memory stats as top-level keys.
    """
    cpu_1min = _float_or_default(data.get("cpu_state_user_1min", data.get("load_avg_1min", 0.0)))
    cpu_5min = _float_or_default(data.get("cpu_state_user_5min", data.get("load_avg_5min", 0.0)))
    cpu_15min = _float_or_default(data.get("cpu_state_user_15min", data.get("load_avg_15min", 0.0)))

    mem_total = _int_or_default(data.get("memory_usage_total", data.get("mem_total", 0)))
    mem_used = _int_or_default(data.get("memory_usage_used", data.get("mem_used", 0)))
    mem_free = _int_or_default(data.get("memory_usage_free", data.get("mem_free", 0)))

    process_count = _int_or_default(data.get("processes_total", data.get("process_count", 0)))

    # Calculate memory utilization percentage
    mem_pct = round((mem_used / mem_total) * 100, 1) if mem_total > 0 else 0.0

    return {
        "cpu": {
            "load_1min": cpu_1min,
            "load_5min": cpu_5min,
            "load_15min": cpu_15min,
        },
        "memory": {
            "total_kb": mem_total,
            "used_kb": mem_used,
            "free_kb": mem_free,
            "utilization_pct": mem_pct,
        },
        "process_count": process_count,
    }


def _int_or_default(value, default: int = 0) -> int:
    """Safely convert a value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _float_or_default(value, default: float = 0.0) -> float:
    """Safely convert a value to float, returning default on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default
