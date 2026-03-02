"""Cisco IOS-XE vendor-specific tools using RESTCONF.

These tools expose IOS-XE-specific features that don't have cross-vendor
equivalents: RESTCONF capabilities, platform hardware details, and SD-WAN
status.

Only loaded when the ``cisco`` extra is installed (httpx available).
"""

from __future__ import annotations

import logging

from network_mcp.helpers import READ_ONLY, handle_tool_errors, validate_host
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def iosxe_get_restconf_capabilities(host: str) -> dict:
    """Get RESTCONF capabilities from a Cisco IOS-XE device.

    Returns supported YANG modules, RESTCONF protocol capabilities, and their
    versions. Useful for determining which RESTCONF operations are available.

    Args:
        host: Hostname, IP address, or inventory name of the IOS-XE device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "iosxe":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"RESTCONF capabilities are only available on IOS-XE devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    # Use RESTCONF path for YANG module library
    result = driver.run_show(["show restconf capabilities"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get RESTCONF capabilities: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_restconf_capabilities(data),
    }


def _normalize_restconf_capabilities(data: dict) -> dict:
    """Normalize RESTCONF capabilities response to a structured format.

    Handles both the ``ietf-yang-library:modules-state`` and
    ``ietf-restconf-monitoring:restconf-state`` response formats.
    """
    capabilities: list[str] = []
    yang_modules: list[dict] = []

    # RESTCONF monitoring capabilities
    restconf_state = data.get("ietf-restconf-monitoring:restconf-state", {})
    caps = restconf_state.get("capabilities", {}).get("capability", [])
    if isinstance(caps, list):
        capabilities = caps

    # YANG library modules (if present)
    modules_state = data.get("ietf-yang-library:modules-state", {})
    modules = modules_state.get("module", [])
    if isinstance(modules, list):
        for mod in modules:
            yang_modules.append(
                {
                    "name": mod.get("name", ""),
                    "revision": mod.get("revision", ""),
                    "namespace": mod.get("namespace", ""),
                    "conformance_type": mod.get("conformance-type", ""),
                }
            )

    return {
        "capabilities": capabilities,
        "yang_modules": yang_modules,
        "capability_count": len(capabilities),
        "module_count": len(yang_modules),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def iosxe_get_platform_info(host: str) -> dict:
    """Get detailed platform information from a Cisco IOS-XE device.

    Returns hardware model, serial number, PIDs, license status, and boot
    variables. More detailed than the vendor-agnostic net_get_device_info.

    Args:
        host: Hostname, IP address, or inventory name of the IOS-XE device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "iosxe":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Platform info is only available on IOS-XE devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show platform info"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get platform info: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_platform_info(data),
    }


def _normalize_platform_info(data: dict) -> dict:
    """Normalize IOS-XE platform info response.

    Handles RESTCONF ``Cisco-IOS-XE-platform-oper:components`` and
    simpler show command output formats.
    """
    components_data = data.get("Cisco-IOS-XE-platform-oper:components", data)
    components = components_data.get("component", [])

    hardware: list[dict] = []
    if isinstance(components, list):
        for comp in components:
            state = comp.get("state", comp.get("cstate", {}))
            hardware.append(
                {
                    "name": comp.get("cname", comp.get("name", "")),
                    "description": state.get("description", ""),
                    "part_no": state.get("part-no", state.get("pid", "")),
                    "serial_no": state.get("serial-no", state.get("sn", "")),
                    "type": comp.get("type", ""),
                }
            )

    # Extract top-level fields if present (from simplified responses)
    chassis_pid = data.get("pid", "")
    chassis_sn = data.get("sn", "")
    boot_variable = data.get("boot_variable", "")
    license_status = data.get("license_status", "")

    return {
        "chassis_pid": chassis_pid,
        "chassis_sn": chassis_sn,
        "boot_variable": boot_variable,
        "license_status": license_status,
        "components": hardware,
        "component_count": len(hardware),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def iosxe_get_sdwan_status(host: str) -> dict:
    """Get SD-WAN status from a Cisco IOS-XE device (if applicable).

    Returns control connections, BFD sessions, and OMP status. Returns
    ``not_supported`` status on non-SD-WAN IOS-XE devices.

    Args:
        host: Hostname, IP address, or inventory name of the IOS-XE device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "iosxe":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"SD-WAN status is only available on IOS-XE devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show sdwan control connections"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        # SD-WAN commands fail on non-SD-WAN devices — report gracefully
        if "not supported" in error_msg.lower() or "invalid" in error_msg.lower():
            return {
                "status": "not_supported",
                "device": host,
                "data": {
                    "sdwan_enabled": False,
                    "message": "SD-WAN is not enabled on this device",
                },
            }
        return {"status": "error", "device": host, "error": f"Failed to get SD-WAN status: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_sdwan_status(data),
    }


def _normalize_sdwan_status(data: dict) -> dict:
    """Normalize SD-WAN control connections response."""
    connections: list[dict] = []

    # Handle TABLE/ROW pattern or flat list
    conn_table = data.get("TABLE_connections", data)
    rows = conn_table.get("ROW_connections", conn_table.get("connections", []))
    if isinstance(rows, dict):
        rows = [rows]
    if isinstance(rows, list):
        for row in rows:
            connections.append(
                {
                    "peer_type": row.get("peer-type", row.get("peer_type", "")),
                    "peer_ip": row.get("system-ip", row.get("peer_ip", "")),
                    "site_id": _int_or_default(row.get("site-id", row.get("site_id", 0))),
                    "state": row.get("state", ""),
                    "uptime": row.get("uptime", ""),
                }
            )

    return {
        "sdwan_enabled": True,
        "control_connections": connections,
        "connection_count": len(connections),
    }


def _int_or_default(value, default: int = 0) -> int:
    """Safely convert a value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
