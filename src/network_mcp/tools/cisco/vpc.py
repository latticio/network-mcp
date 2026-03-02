"""Cisco NX-OS vPC (Virtual Port Channel) status tool.

vPC is the NX-OS equivalent of Arista MLAG — provides active-active
dual-homing for servers and network devices.

Only loaded when the ``cisco`` extra is installed (httpx available).
"""

from __future__ import annotations

import logging

from network_mcp.helpers import READ_ONLY, handle_tool_errors, validate_host
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def nxos_get_vpc_status(host: str) -> dict:
    """Get vPC (Virtual Port Channel) status from a Cisco NX-OS device.

    Returns vPC domain, role (primary/secondary), peer-link status, peer
    keepalive status, and individual vPC port-channel states. vPC is the
    NX-OS equivalent of Arista MLAG.

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
                f"vPC is only available on NX-OS devices. Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show vpc"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get vPC status: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_vpc(data),
    }


def _normalize_vpc(data: dict) -> dict:
    """Normalize NX-API 'show vpc' output to a structured format.

    NX-API returns vPC data with keys like ``vpc-domain-id``, ``vpc-role``,
    ``vpc-peer-status``, etc. This normalizes to a clean dict.
    """
    # Extract vPC domain info
    domain_id = _int_or_default(data.get("vpc-domain-id", data.get("domain-id", 0)))
    role = data.get("vpc-role", data.get("role", ""))
    peer_status = data.get("vpc-peer-status", data.get("peer-status", ""))
    peer_keepalive_status = data.get("vpc-peer-keepalive-status", data.get("peer-keepalive-status", ""))

    # Peer link info
    peer_link_status = data.get("vpc-peer-link-status", data.get("peer-link-status", "up"))
    peer_link_pc = data.get("vpc-peer-link-port-channel", data.get("peerlink-ifindex", ""))

    # Individual vPC members
    vpcs = []
    vpc_table = data.get("TABLE_vpc", {})
    if vpc_table:
        rows = vpc_table.get("ROW_vpc", [])
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            vpcs.append(
                {
                    "id": _int_or_default(row.get("vpc-id", row.get("vpc-ifindex", 0))),
                    "port_channel": row.get("vpc-port-channel", row.get("vpc-ifindex", "")),
                    "status": row.get("vpc-port-status", row.get("vpc-status", "")),
                    "consistency": row.get("vpc-consistency-status", row.get("vpc-consistency", "")),
                }
            )

    return {
        "domain_id": domain_id,
        "role": role,
        "peer_status": peer_status,
        "peer_keepalive_status": peer_keepalive_status,
        "peer_link": {
            "status": peer_link_status,
            "port_channel": peer_link_pc,
        },
        "vpcs": vpcs,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def nxos_get_vpc_detail(host: str) -> dict:
    """Get detailed vPC information from a Cisco NX-OS device.

    Returns vPC domain config, peer-keepalive details, type-1 consistency
    checks, and per-vPC status with active VLANs. More detailed than
    nxos_get_vpc_status.

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
                f"vPC is only available on NX-OS devices. Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show vpc detail"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get vPC detail: {error_msg}"}

    data = result[0]
    return {
        "status": "success",
        "device": host,
        "data": _normalize_vpc_detail(data),
    }


def _normalize_vpc_detail(data: dict) -> dict:
    """Normalize NX-API 'show vpc detail' output to a structured format.

    Includes peer-keepalive details, consistency parameters, and per-vPC
    active VLANs in addition to the fields in _normalize_vpc.
    """
    base = _normalize_vpc(data)

    # Peer-keepalive details
    keepalive = {
        "destination": data.get("vpc-peer-keepalive-dest", data.get("peer-keepalive-dest", "")),
        "source": data.get("vpc-peer-keepalive-src", data.get("peer-keepalive-src", "")),
        "vrf": data.get("vpc-peer-keepalive-vrf", data.get("peer-keepalive-vrf", "")),
        "interval_ms": _int_or_default(data.get("vpc-keepalive-interval", data.get("keepalive-interval", 1000))),
        "timeout_ms": _int_or_default(data.get("vpc-keepalive-timeout", data.get("keepalive-timeout", 5000))),
    }

    # Type-1 consistency checks
    consistency_params: list[dict] = []
    consist_table = data.get("TABLE_consistency_params", {})
    if consist_table:
        rows = consist_table.get("ROW_consistency_params", [])
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            consistency_params.append(
                {
                    "name": row.get("param-name", row.get("name", "")),
                    "local_value": row.get("local-value", row.get("local", "")),
                    "peer_value": row.get("peer-value", row.get("peer", "")),
                    "result": row.get("result", ""),
                }
            )

    # Per-vPC detail with active VLANs
    detail_vpcs: list[dict] = []
    vpc_detail_table = data.get("TABLE_vpc_detail", data.get("TABLE_vpc", {}))
    if vpc_detail_table:
        rows = vpc_detail_table.get("ROW_vpc_detail", vpc_detail_table.get("ROW_vpc", []))
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            detail_vpcs.append(
                {
                    "id": _int_or_default(row.get("vpc-id", row.get("vpc-ifindex", 0))),
                    "port_channel": row.get("vpc-port-channel", row.get("vpc-ifindex", "")),
                    "status": row.get("vpc-port-status", row.get("vpc-status", "")),
                    "consistency": row.get("vpc-consistency-status", row.get("vpc-consistency", "")),
                    "active_vlans": row.get("active-vlans", row.get("up-vlans", "")),
                }
            )

    if detail_vpcs:
        base["vpcs"] = detail_vpcs

    base["peer_keepalive"] = keepalive
    base["consistency_params"] = consistency_params

    return base


def _int_or_default(value, default: int = 0) -> int:
    """Safely convert a value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
