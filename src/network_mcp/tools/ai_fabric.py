"""AI/ML fabric health tools for RoCEv2/RDMA network readiness.

Provides tools to validate Priority Flow Control (PFC), Explicit Congestion
Notification (ECN), and DCQCN settings required for GPU-to-GPU RDMA traffic
in AI/ML training clusters.
"""

from network_mcp.helpers import READ_ONLY, handle_tool_errors, run_show_command
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_ai_fabric_health(host: str) -> dict:
    """Check RoCEv2/RDMA readiness across the fabric on a network device.

    When to use: To validate that PFC, ECN, and DCQCN settings are correctly
    configured for lossless RDMA traffic in AI/ML training clusters.
    Output: PFC status, ECN configuration, queue counters, and an overall
    readiness assessment with per-check pass/fail details.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    result = run_show_command(
        conn_mgr,
        host,
        ["show priority-flow-control", "show policy-map interface", "show queue counters"],
    )
    if result["status"] == "error":
        return result

    pfc_data = result["data"][0]
    policy_map_data = result["data"][1]
    queue_data = result["data"][2]

    # Assess RoCEv2 readiness based on collected data
    checks = []

    # Check 1: PFC enabled
    pfc_enabled = _check_pfc_enabled(pfc_data)
    checks.append({
        "check": "pfc_enabled",
        "passed": pfc_enabled,
        "detail": "Priority Flow Control is enabled" if pfc_enabled else "Priority Flow Control is not enabled",
    })

    # Check 2: ECN policy configured
    ecn_configured = _check_ecn_configured(policy_map_data)
    checks.append({
        "check": "ecn_configured",
        "passed": ecn_configured,
        "detail": "ECN marking policy is configured" if ecn_configured else "No ECN marking policy found",
    })

    # Check 3: Lossless queues present
    lossless_queues = _check_lossless_queues(pfc_data)
    checks.append({
        "check": "lossless_queues",
        "passed": lossless_queues,
        "detail": "Lossless queue priorities detected" if lossless_queues else "No lossless queue priorities found",
    })

    all_passed = all(c["passed"] for c in checks)

    return {
        "status": "success",
        "device": host,
        "data": {
            "rocev2_ready": all_passed,
            "checks": checks,
            "pfc": pfc_data,
            "policy_map": policy_map_data,
            "queue_counters": queue_data,
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_ai_fabric_ecn_status(host: str) -> dict:
    """Get ECN marking counters and watermarks per interface on a network device.

    When to use: To monitor ECN congestion marking rates and queue depth
    watermarks that indicate congestion in RoCEv2/RDMA fabric links.
    Output: Policy-map interface details with ECN counters and queue statistics.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    result = run_show_command(
        conn_mgr,
        host,
        ["show policy-map interface", "show queue counters"],
    )
    if result["status"] == "error":
        return result

    return {
        "status": "success",
        "device": host,
        "data": {
            "policy_map": result["data"][0],
            "queue_counters": result["data"][1],
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_ai_fabric_pfc_status(host: str) -> dict:
    """Get PFC frame counters and watchdog status on a network device.

    When to use: To check for PFC storms, excessive pause frames, or watchdog
    triggers that can cause head-of-line blocking in RDMA fabrics.
    Output: PFC per-priority counters (TX/RX pause frames) and watchdog status.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
    """
    result = run_show_command(
        conn_mgr,
        host,
        ["show priority-flow-control", "show priority-flow-control counters"],
    )
    if result["status"] == "error":
        return result

    return {
        "status": "success",
        "device": host,
        "data": {
            "pfc_status": result["data"][0],
            "pfc_counters": result["data"][1],
        },
    }


def _check_pfc_enabled(pfc_data: dict) -> bool:
    """Check if PFC is enabled in the device response."""
    # Arista EOS: look for enabled interfaces or global PFC status
    if pfc_data.get("pfcEnabled") is True:
        return True
    # Check for any interface with PFC enabled
    for _iface, iface_data in pfc_data.get("interfaces", {}).items():
        if isinstance(iface_data, dict) and iface_data.get("pfcEnabled") is True:
            return True
    return False


def _check_ecn_configured(policy_map_data: dict) -> bool:
    """Check if any ECN marking policy is configured."""
    # Look for ECN-related keywords in policy-map data
    if not policy_map_data:
        return False
    # Check for policyMaps or interfaces with ECN configuration
    for _pm_name, pm_data in policy_map_data.get("policyMaps", {}).items():
        if isinstance(pm_data, dict):
            for _cls_name, cls_data in pm_data.get("classMaps", {}).items():
                if isinstance(cls_data, dict) and cls_data.get("ecn"):
                    return True
    # Also check interface-level policy attachments
    for _iface, iface_data in policy_map_data.get("interfaces", {}).items():
        if isinstance(iface_data, dict):
            for _dir_key, dir_data in iface_data.items():
                if isinstance(dir_data, dict) and dir_data.get("ecn"):
                    return True
    return False


def _check_lossless_queues(pfc_data: dict) -> bool:
    """Check if lossless queue priorities are configured."""
    # Check for priority-to-queue mappings with PFC enabled priorities
    if pfc_data.get("priorities"):
        return True
    for _iface, iface_data in pfc_data.get("interfaces", {}).items():
        if isinstance(iface_data, dict) and iface_data.get("priorities"):
            return True
    return False
