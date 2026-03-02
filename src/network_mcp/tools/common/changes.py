"""Vendor-agnostic change management tools.

Provides MCP tools for listing, inspecting, approving, rolling back,
auditing, and analyzing the impact of change requests when enterprise
change management is enabled (NET_CHANGE_MGMT_ENABLED=true).
"""

from __future__ import annotations

import logging
import re

from network_mcp.helpers import READ_ONLY, WRITE_SAFE, get_change_manager
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
def net_list_pending_changes(host: str = "") -> dict:
    """List pending change requests awaiting approval or execution.

    Returns all pending/approved change requests. Filter by device hostname
    to see changes for a specific device only.

    Args:
        host: Optional device hostname to filter by. Empty string returns all.
    """
    cm = get_change_manager()
    if not cm.enabled:
        return {
            "status": "error",
            "error": "Change management is not enabled. Set NET_CHANGE_MGMT_ENABLED=true to enable.",
        }

    pending = cm.list_pending(device=host)
    return {
        "status": "success",
        "count": len(pending),
        "data": [cm.to_dict(cr) for cr in pending],
    }


@mcp.tool(annotations=READ_ONLY)
def net_get_change_request(host: str, change_id: str) -> dict:
    """Get details of a specific change request by ID.

    Returns the full change request including risk level, status, commands,
    snapshots, and rollback commands.

    Args:
        host: Device hostname associated with the change (for context).
        change_id: UUID of the change request.
    """
    cm = get_change_manager()
    if not cm.enabled:
        return {
            "status": "error",
            "device": host,
            "error": "Change management is not enabled. Set NET_CHANGE_MGMT_ENABLED=true to enable.",
        }

    cr = cm.get_change_request(change_id)
    if cr is None:
        return {
            "status": "error",
            "device": host,
            "error": f"Change request {change_id} not found.",
        }

    return {
        "status": "success",
        "device": host,
        "data": cm.to_dict(cr),
    }


@mcp.tool(annotations=WRITE_SAFE)
def net_approve_change(host: str, change_id: str, approved_by: str = "admin", reason: str = "") -> dict:
    """[WRITE] Approve a pending change request. Requires network:admin scope when RBAC is enabled.

    After approval, the change can be executed by re-running the original tool.

    Args:
        host: Device hostname associated with the change.
        change_id: UUID of the change request to approve.
        approved_by: Name of the approver (defaults to 'admin').
        reason: Reason for the approval.
    """
    cm = get_change_manager()
    if not cm.enabled:
        return {
            "status": "error",
            "device": host,
            "error": "Change management is not enabled. Set NET_CHANGE_MGMT_ENABLED=true to enable.",
        }

    cr = cm.approve_change(change_id, approved_by=approved_by, reason=reason)
    if cr is None:
        return {
            "status": "error",
            "device": host,
            "error": f"Change request {change_id} not found or is not in pending status.",
        }

    return {
        "status": "success",
        "device": host,
        "change_id": cr.id,
        "action": "approved",
        "approved_by": approved_by,
        "risk": cr.risk.value,
        "message": f"Change request {cr.id} approved. Re-run the original tool to execute.",
    }


@mcp.tool(annotations=WRITE_SAFE)
def net_rollback_change(host: str, change_id: str) -> dict:
    """[WRITE] Roll back a completed or failed change using its rollback commands.

    Applies the reverse of the original configuration commands to restore
    the device to its pre-change state. After applying rollback commands,
    validates the device state against the pre-change snapshot.

    Args:
        host: Device hostname where the rollback will be applied.
        change_id: UUID of the change request to roll back.
    """
    cm = get_change_manager()
    if not cm.enabled:
        return {
            "status": "error",
            "device": host,
            "error": "Change management is not enabled. Set NET_CHANGE_MGMT_ENABLED=true to enable.",
        }

    cr = cm.get_change_request(change_id)
    if cr is None:
        return {
            "status": "error",
            "device": host,
            "error": f"Change request {change_id} not found.",
        }

    return cm.rollback(cr, conn_mgr)


@mcp.tool(annotations=READ_ONLY)
def net_get_change_history(host: str, change_id: str) -> dict:
    """Get the full audit trail for a change request.

    Returns the complete approval chain and execution log, providing
    full traceability of who approved the change, when it was executed,
    and whether rollback was performed.

    Args:
        host: Device hostname associated with the change (for context).
        change_id: UUID of the change request.
    """
    cm = get_change_manager()
    if not cm.enabled:
        return {
            "status": "error",
            "device": host,
            "error": "Change management is not enabled. Set NET_CHANGE_MGMT_ENABLED=true to enable.",
        }

    cr = cm.get_change_request(change_id)
    if cr is None:
        return {
            "status": "error",
            "device": host,
            "error": f"Change request {change_id} not found.",
        }

    return {
        "status": "success",
        "device": host,
        "change_id": cr.id,
        "data": {
            "change_id": cr.id,
            "tool_name": cr.tool_name,
            "device": cr.device,
            "status": cr.status.value,
            "risk": cr.risk.value,
            "created_at": cr.created_at.isoformat(),
            "created_by": cr.created_by,
            "approval_chain": cr.approval_chain,
            "execution_log": cr.execution_log,
        },
    }


# --- Blast radius analysis patterns ---

_INTERFACE_PATTERN = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_VLAN_PATTERN = re.compile(r"^vlan\s+(\d+)", re.IGNORECASE)
_BGP_PATTERN = re.compile(r"^router\s+bgp\b", re.IGNORECASE)
_BGP_NEIGHBOR_PATTERN = re.compile(r"^\s*neighbor\s+(\S+)", re.IGNORECASE)
_ROUTING_PATTERNS = [
    re.compile(r"^ip\s+route\b", re.IGNORECASE),
    re.compile(r"^route-map\b", re.IGNORECASE),
    re.compile(r"^router\s+ospf\b", re.IGNORECASE),
    re.compile(r"^router\s+bgp\b", re.IGNORECASE),
]
_MANAGEMENT_INTERFACES = {"management0", "management1", "loopback0", "vlan1"}
_DESCRIPTION_PATTERN = re.compile(r"^\s*description\s+", re.IGNORECASE)


def _parse_affected_resources(commands: list[str]) -> dict:
    """Parse configuration commands to identify affected network resources.

    Args:
        commands: List of proposed configuration commands.

    Returns:
        Dict with affected_interfaces, affected_vlans, affected_bgp_sessions,
        has_routing_impact, and has_description_only flags.
    """
    affected_interfaces: list[str] = []
    affected_vlans: list[int] = []
    affected_bgp_sessions: list[str] = []
    has_routing_impact = False
    has_description_only = True
    current_interface = None

    for cmd in commands:
        stripped = cmd.strip()
        if not stripped:
            continue

        # Interface commands
        iface_match = _INTERFACE_PATTERN.match(stripped)
        if iface_match:
            current_interface = iface_match.group(1)
            if current_interface not in affected_interfaces:
                affected_interfaces.append(current_interface)
            has_description_only = False
            continue

        # VLAN commands
        vlan_match = _VLAN_PATTERN.match(stripped)
        if vlan_match:
            vlan_id = int(vlan_match.group(1))
            if vlan_id not in affected_vlans:
                affected_vlans.append(vlan_id)
            has_description_only = False
            current_interface = None
            continue

        # BGP commands
        if _BGP_PATTERN.match(stripped):
            has_routing_impact = True
            has_description_only = False
            current_interface = None
            continue

        # BGP neighbor commands
        neighbor_match = _BGP_NEIGHBOR_PATTERN.match(stripped)
        if neighbor_match:
            peer = neighbor_match.group(1)
            if peer not in affected_bgp_sessions:
                affected_bgp_sessions.append(peer)
            has_routing_impact = True
            has_description_only = False
            continue

        # General routing commands
        for rp in _ROUTING_PATTERNS:
            if rp.match(stripped):
                has_routing_impact = True
                has_description_only = False
                break

        # Check if it's just a description change under an interface
        if current_interface and not _DESCRIPTION_PATTERN.match(stripped):
            has_description_only = False

    return {
        "affected_interfaces": affected_interfaces,
        "affected_vlans": affected_vlans,
        "affected_bgp_sessions": affected_bgp_sessions,
        "has_routing_impact": has_routing_impact,
        "has_description_only": has_description_only and len(commands) > 0,
    }


def _calculate_risk_level(resources: dict) -> str:
    """Calculate risk level based on affected resources.

    Risk levels:
    - LOW: only description/cosmetic changes or empty commands
    - MEDIUM: single interface or VLAN changes
    - HIGH: multiple interfaces, BGP sessions, or trunk ports
    - CRITICAL: management interfaces, routing protocol config, or >5 interfaces

    Args:
        resources: Dict from _parse_affected_resources().

    Returns:
        Risk level string: "LOW", "MEDIUM", "HIGH", or "CRITICAL".
    """
    interfaces = resources["affected_interfaces"]
    vlans = resources["affected_vlans"]
    bgp_sessions = resources["affected_bgp_sessions"]
    has_routing_impact = resources["has_routing_impact"]
    has_description_only = resources["has_description_only"]

    # No affected resources or only description changes
    if has_description_only or (not interfaces and not vlans and not bgp_sessions and not has_routing_impact):
        return "LOW"

    # Check for management interface changes -> CRITICAL
    for iface in interfaces:
        if iface.lower() in _MANAGEMENT_INTERFACES:
            return "CRITICAL"

    # Routing protocol config changes -> CRITICAL
    if has_routing_impact and bgp_sessions:
        return "CRITICAL"

    # >5 interfaces -> CRITICAL
    if len(interfaces) > 5:
        return "CRITICAL"

    # Multiple interfaces or BGP sessions -> HIGH
    if len(interfaces) > 1 or bgp_sessions or has_routing_impact:
        return "HIGH"

    # Single interface or VLAN -> MEDIUM
    if interfaces or vlans:
        return "MEDIUM"

    return "LOW"


@mcp.tool(annotations=READ_ONLY)
def net_analyze_change_impact(host: str, commands: list[str]) -> dict:
    """Analyze the blast radius of proposed configuration changes.

    Identifies affected interfaces, VLANs, BGP sessions, and dependent
    devices (via LLDP neighbors) that could be impacted by the proposed changes.

    Args:
        host: Target device hostname or IP address.
        commands: List of proposed configuration commands to analyze.
    """
    if not commands:
        return {
            "status": "success",
            "device": host,
            "impact": {
                "affected_interfaces": [],
                "affected_vlans": [],
                "affected_bgp_sessions": [],
                "dependent_devices": [],
                "risk_level": "LOW",
                "summary": "No commands provided",
            },
        }

    # Parse commands to identify affected resources
    resources = _parse_affected_resources(commands)
    risk_level = _calculate_risk_level(resources)

    # Query LLDP neighbors for affected interfaces
    dependent_devices: list[dict] = []
    if resources["affected_interfaces"]:
        try:
            driver = conn_mgr.get_driver(host)
            neighbors = driver.get_lldp_neighbors()
            # neighbors is expected to be a dict mapping interface -> list of neighbor dicts
            for iface in resources["affected_interfaces"]:
                if iface in neighbors:
                    iface_neighbors = neighbors[iface]
                    if isinstance(iface_neighbors, list):
                        for neighbor in iface_neighbors:
                            dep = {
                                "device": neighbor.get("hostname", neighbor.get("neighbor", "unknown")),
                                "interface": iface,
                            }
                            if dep not in dependent_devices:
                                dependent_devices.append(dep)
                    elif isinstance(iface_neighbors, dict):
                        dep = {
                            "device": iface_neighbors.get("hostname", iface_neighbors.get("neighbor", "unknown")),
                            "interface": iface,
                        }
                        if dep not in dependent_devices:
                            dependent_devices.append(dep)
        except Exception as e:
            logger.warning("Failed to query LLDP neighbors on %s: %s", host, e)

    # Build summary
    parts = []
    if resources["affected_interfaces"]:
        parts.append(f"{len(resources['affected_interfaces'])} interface(s)")
    if resources["affected_vlans"]:
        parts.append(f"{len(resources['affected_vlans'])} VLAN(s)")
    if resources["affected_bgp_sessions"]:
        parts.append(f"{len(resources['affected_bgp_sessions'])} BGP session(s)")
    if dependent_devices:
        parts.append(f"{len(dependent_devices)} LLDP neighbor(s)")

    summary = f"Changes affect {', '.join(parts)}" if parts else "No significant impact detected"

    return {
        "status": "success",
        "device": host,
        "impact": {
            "affected_interfaces": resources["affected_interfaces"],
            "affected_vlans": resources["affected_vlans"],
            "affected_bgp_sessions": resources["affected_bgp_sessions"],
            "dependent_devices": dependent_devices,
            "risk_level": risk_level,
            "summary": summary,
        },
    }
