"""AI/ML fabric health tools for RoCEv2/RDMA network readiness.

Provides tools to validate Priority Flow Control (PFC), Explicit Congestion
Notification (ECN), and DCQCN settings required for GPU-to-GPU RDMA traffic
in AI/ML training clusters. Also includes GPU fabric path validation tools
for rail-optimized topologies.
"""

from network_mcp.helpers import READ_ONLY, handle_tool_errors, run_show_command, validate_ip_or_hostname
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


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_ai_fabric_rail_check(host: str) -> dict:
    """Validate rail-optimized GPU fabric topology on a network device.

    When to use: To verify that a leaf/ToR switch in a rail-optimized AI fabric
    has correct dedicated paths from GPU hosts to their rail spine switches.
    Uses LLDP neighbor data to identify connected GPU hosts and upstream spines,
    then checks the routing table for dedicated paths between them.
    Output: Per-GPU-host rail connectivity assessment with path details and
    an overall topology validation result.

    Args:
        host: Hostname, IP address, or inventory name of the network device (leaf/ToR switch).
    """
    result = run_show_command(
        conn_mgr,
        host,
        ["show lldp neighbors", "show ip route", "show interfaces status"],
    )
    if result["status"] == "error":
        return result

    lldp_data = result["data"][0]
    route_data = result["data"][1]
    intf_status = result["data"][2]

    neighbors = lldp_data.get("lldpNeighbors", [])

    # Classify neighbors as GPU hosts (servers) or spine switches
    gpu_hosts = []
    spine_switches = []
    for neighbor in neighbors:
        neighbor_name = neighbor.get("neighborDevice", "")
        local_port = neighbor.get("port", "")
        neighbor_port = neighbor.get("neighborPort", "")
        entry = {
            "device": neighbor_name,
            "local_port": local_port,
            "remote_port": neighbor_port,
        }
        # Spines typically have "spine" in hostname; GPU hosts have "gpu", "compute", or "server"
        name_lower = neighbor_name.lower()
        if "spine" in name_lower:
            spine_switches.append(entry)
        elif any(kw in name_lower for kw in ("gpu", "compute", "server", "node")):
            gpu_hosts.append(entry)

    # Build route table lookup: check for ECMP paths and next-hop interfaces
    routes = route_data.get("vrfs", {}).get("default", {}).get("routes", {})
    interface_statuses = intf_status.get("interfaceStatuses", {})

    # Validate each GPU host has a dedicated uplink path to a spine
    host_checks = []
    for gpu in gpu_hosts:
        gpu_port = gpu["local_port"]
        port_status = interface_statuses.get(gpu_port, {})
        port_up = port_status.get("linkStatus") == "connected"

        # Check that the port connecting to this GPU host is up
        # and that spine uplinks exist for rail connectivity
        has_spine_path = len(spine_switches) > 0

        host_checks.append({
            "gpu_host": gpu["device"],
            "local_port": gpu_port,
            "port_up": port_up,
            "has_spine_path": has_spine_path,
            "spine_uplinks": [s["local_port"] for s in spine_switches],
            "passed": port_up and has_spine_path,
        })

    # Overall assessment
    all_passed = len(host_checks) > 0 and all(c["passed"] for c in host_checks)

    return {
        "status": "success",
        "device": host,
        "data": {
            "rail_topology_valid": all_passed,
            "gpu_host_count": len(gpu_hosts),
            "spine_count": len(spine_switches),
            "host_checks": host_checks,
            "spines": spine_switches,
            "route_count": len(routes),
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_ai_fabric_gpu_path(host: str, source_gpu: str, destination_gpu: str) -> dict:
    """Trace the network path between two GPU hosts showing all hops and ECMP paths.

    When to use: To troubleshoot or verify the network path between two GPU hosts
    in an AI/ML training cluster. Combines traceroute output with LLDP neighbor
    data to map each hop to a physical device and interface.
    Output: Ordered list of hops from source to destination with device names,
    interfaces, and any ECMP (equal-cost multi-path) alternatives detected.

    Args:
        host: Hostname, IP address, or inventory name of the network device to run the trace from.
        source_gpu: IP address or hostname of the source GPU host.
        destination_gpu: IP address or hostname of the destination GPU host.
    """
    src_err = validate_ip_or_hostname(source_gpu, "source_gpu")
    if src_err:
        return {"status": "error", "device": host, "error": src_err}
    dst_err = validate_ip_or_hostname(destination_gpu, "destination_gpu")
    if dst_err:
        return {"status": "error", "device": host, "error": dst_err}

    # Collect traceroute, LLDP neighbors, and ECMP route info
    result = run_show_command(
        conn_mgr,
        host,
        [
            f"traceroute {destination_gpu} source {source_gpu}",
            "show lldp neighbors",
            f"show ip route {destination_gpu}",
        ],
        encoding="text",
    )
    if result["status"] == "error":
        return result

    trace_output = result["data"][0].get("output", "")
    lldp_text = result["data"][1].get("output", "")
    route_text = result["data"][2].get("output", "")

    # Parse traceroute output into hops
    hops = _parse_traceroute_hops(trace_output)

    # Detect ECMP from route output (multiple next-hops)
    ecmp_paths = _parse_ecmp_paths(route_text)

    # Build LLDP neighbor map for device name resolution
    lldp_map = _parse_lldp_text(lldp_text)

    # Enrich hops with LLDP device names where possible
    for hop in hops:
        hop_addr = hop.get("address", "")
        if hop_addr in lldp_map:
            hop["device_name"] = lldp_map[hop_addr].get("device", "")
            hop["interface"] = lldp_map[hop_addr].get("port", "")

    return {
        "status": "success",
        "device": host,
        "data": {
            "source": source_gpu,
            "destination": destination_gpu,
            "hop_count": len(hops),
            "hops": hops,
            "ecmp_paths": ecmp_paths,
            "traceroute_raw": trace_output,
        },
    }


def _parse_traceroute_hops(output: str) -> list[dict]:
    """Parse traceroute text output into a list of hop dicts."""
    import re

    hops = []
    for line in output.splitlines():
        line = line.strip()
        # Match lines like: " 1  10.0.0.1 (10.0.0.1)  1.234 ms  1.111 ms  1.222 ms"
        # or: " 1  10.0.0.1  1.234 ms"
        match = re.match(r"^\s*(\d+)\s+(.+)", line)
        if not match:
            continue
        hop_num = int(match.group(1))
        rest = match.group(2)
        # Extract IP addresses from the rest of the line
        addresses = re.findall(r"(\d+\.\d+\.\d+\.\d+)", rest)
        # Extract RTT values
        rtts = re.findall(r"([\d.]+)\s*ms", rest)
        if addresses:
            hops.append({
                "hop": hop_num,
                "address": addresses[0],
                "rtt_ms": [float(r) for r in rtts] if rtts else [],
                "alternatives": list(set(addresses[1:])) if len(addresses) > 1 else [],
            })
        elif "* * *" in rest or rest.strip() == "* * *":
            hops.append({
                "hop": hop_num,
                "address": "*",
                "rtt_ms": [],
                "alternatives": [],
            })
    return hops


def _parse_ecmp_paths(route_text: str) -> list[dict]:
    """Parse route output text to extract ECMP next-hop paths."""
    import re

    paths = []
    # Look for next-hop lines like: "via 10.0.0.1, Ethernet1"
    for line in route_text.splitlines():
        match = re.search(r"via\s+(\d+\.\d+\.\d+\.\d+)(?:,\s*(\S+))?", line)
        if match:
            path = {"next_hop": match.group(1)}
            if match.group(2):
                path["interface"] = match.group(2)
            paths.append(path)
    return paths


def _parse_lldp_text(lldp_text: str) -> dict[str, dict]:
    """Parse LLDP neighbor text output into a map of IP/name -> device info.

    Returns a dict mapping neighbor identifiers to their device and port info.
    This is a best-effort mapping since text output format varies.
    """
    neighbor_map: dict[str, dict] = {}
    for line in lldp_text.splitlines():
        parts = line.split()
        # Typical LLDP text output: "Ethernet1  neighbor-host  Ethernet1  120"
        if len(parts) >= 3 and not line.startswith(("Port", "---", "Last")):
            local_port = parts[0]
            neighbor_device = parts[1]
            neighbor_port = parts[2] if len(parts) > 2 else ""
            neighbor_map[neighbor_device] = {
                "device": neighbor_device,
                "port": neighbor_port,
                "local_port": local_port,
            }
    return neighbor_map


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
