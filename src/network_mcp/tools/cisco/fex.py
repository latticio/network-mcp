"""Cisco NX-OS Fabric Extender (FEX) status tool.

FEX is a Cisco NX-OS feature that extends the fabric by connecting
remote line cards (Fabric Extenders) to a parent Nexus switch.
FEX is specific to NX-OS and has no equivalent on other platforms.

Only loaded when the ``cisco`` extra is installed (httpx available).
"""

from __future__ import annotations

import logging

from network_mcp.helpers import READ_ONLY, handle_tool_errors, validate_host
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def nxos_get_fex_status(host: str) -> dict:
    """Get Fabric Extender (FEX) status from a Cisco NX-OS device.

    Returns FEX unit IDs, models, serial numbers, operational state, and
    uplink connectivity. FEX is specific to Cisco NX-OS and has no
    equivalent on other platforms.

    Args:
        host: Hostname, IP address, or inventory name of the NX-OS device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    # Verify this is an NX-OS device
    if getattr(driver, "platform", None) != "nxos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"FEX is only available on NX-OS devices. Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show fex"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get FEX status: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_fex(data),
    }


def _normalize_fex(data: dict) -> dict:
    """Normalize NX-API 'show fex' output to a structured format.

    NX-API returns FEX data under ``TABLE_fex.ROW_fex`` with details
    about each connected Fabric Extender.
    """
    fex_list = []
    fex_table = data.get("TABLE_fex", {})
    if fex_table:
        rows = fex_table.get("ROW_fex", [])
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            fex_list.append(
                {
                    "id": _int_or_default(row.get("fex_id", row.get("fex", 0))),
                    "description": row.get("fex_descr", row.get("descr", "")),
                    "state": row.get("fex_state", row.get("state", "")),
                    "model": row.get("fex_model", row.get("model", "")),
                    "serial": row.get("fex_serial", row.get("serial", "")),
                    "uplink_port_channel": row.get("fex_fabric_if", row.get("fabric-if", "")),
                }
            )

    return {
        "fex_count": len(fex_list),
        "fex_units": fex_list,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def nxos_get_fex_detail(host: str, fex_id: int) -> dict:
    """Get detailed FEX unit information from a Cisco NX-OS device.

    Returns model, serial, firmware version, uplink interfaces, and pinning
    information for a specific Fabric Extender unit.

    Args:
        host: Hostname, IP address, or inventory name of the NX-OS device.
        fex_id: FEX unit ID (100-199).
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if not isinstance(fex_id, int) or fex_id < 100 or fex_id > 199:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid FEX ID: {fex_id}. Must be an integer between 100 and 199.",
        }

    driver = conn_mgr.get_driver(host)

    # Verify this is an NX-OS device
    if getattr(driver, "platform", None) != "nxos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"FEX is only available on NX-OS devices. Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show([f"show fex {fex_id} detail"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get FEX {fex_id} detail: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_fex_detail(data, fex_id),
    }


def _normalize_fex_detail(data: dict, fex_id: int) -> dict:
    """Normalize NX-API 'show fex <id> detail' output to a structured format.

    Includes pinning information, firmware version, and per-port status in
    addition to the basic FEX fields.
    """
    # Basic info
    actual_id = _int_or_default(data.get("fex_id", data.get("fex", fex_id)), fex_id)
    description = data.get("fex_descr", data.get("descr", ""))
    state = data.get("fex_state", data.get("state", ""))
    model = data.get("fex_model", data.get("model", ""))
    serial = data.get("fex_serial", data.get("serial", ""))
    firmware = data.get("fex_version", data.get("firmware", ""))

    # Uplink interfaces
    uplinks: list[dict] = []
    uplink_table = data.get("TABLE_fex_uplink", {})
    if uplink_table:
        rows = uplink_table.get("ROW_fex_uplink", [])
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            uplinks.append(
                {
                    "interface": row.get("uplink_if", row.get("interface", "")),
                    "status": row.get("uplink_status", row.get("status", "")),
                    "speed": row.get("uplink_speed", row.get("speed", "")),
                }
            )

    # Pinning information
    pinning: list[dict] = []
    pin_table = data.get("TABLE_fex_pinning", {})
    if pin_table:
        rows = pin_table.get("ROW_fex_pinning", [])
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            pinning.append(
                {
                    "host_interface": row.get("host_if", row.get("host_interface", "")),
                    "fabric_interface": row.get("fabric_if", row.get("fabric_interface", "")),
                }
            )

    return {
        "fex_id": actual_id,
        "description": description,
        "state": state,
        "model": model,
        "serial": serial,
        "firmware": firmware,
        "uplinks": uplinks,
        "pinning": pinning,
    }


def _int_or_default(value, default: int = 0) -> int:
    """Safely convert a value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
