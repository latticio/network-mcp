"""High-level workflow tools that orchestrate multiple operations for diagnosis and comparison.

Workflow tools transform this server from a tool collection into an intelligent assistant.
They always bypass cache (cache_ttl=0) to ensure fresh diagnostic data.

For interactive step-by-step investigations, use the corresponding prompts instead.
"""

import datetime
import difflib
import json
import logging
import os
import threading
import time
import uuid

from pydantic import BaseModel, Field

from network_mcp.config import NetworkSettings
from network_mcp.executor import execute_on_devices, resolve_hosts
from network_mcp.helpers import (
    READ_ONLY,
    run_show_command,
    validate_host,
    validate_interface_name,
    validate_ip_or_hostname,
)
from network_mcp.schemas import BgpDiagnosticOutput, FabricHealthOutput
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


# --- WorkflowResult Builder ---


def _make_workflow_result(
    *,
    status: str,
    device: str,
    workflow: str,
    start_time: float,
    severity: str = "healthy",
    summary: str = "",
    findings: list | None = None,
    raw_data: dict | None = None,
    sub_operations: list | None = None,
) -> dict:
    """Build a standardized WorkflowResult dict.

    Args:
        status: "success", "partial", or "error".
        device: Target device hostname.
        workflow: Workflow tool name.
        start_time: monotonic timestamp from workflow start.
        severity: "critical", "warning", or "healthy".
        summary: One-line human-readable diagnosis summary.
        findings: List of finding dicts with component/status/expected/detail.
        raw_data: Raw command outputs for LLM follow-up.
        sub_operations: List of sub-operation dicts with command/status/duration_ms.
    """
    return {
        "status": status,
        "device": device,
        "workflow": workflow,
        "duration_ms": int((time.monotonic() - start_time) * 1000),
        "diagnosis": {
            "severity": severity,
            "summary": summary,
            "findings": findings or [],
        },
        "raw_data": raw_data or {},
        "sub_operations": sub_operations or [],
    }


def _run_sub_op(
    host: str,
    commands: list[str],
    sub_ops: list,
    raw_data: dict,
    key: str,
    encoding: str = "json",
) -> dict | None:
    """Run a sub-operation, record it, store raw data. Returns data on success, None on failure."""
    start = time.monotonic()
    result = run_show_command(conn_mgr, host, commands, encoding=encoding, cache_ttl=0)
    duration_ms = int((time.monotonic() - start) * 1000)
    cmd_str = ", ".join(commands)
    if result["status"] == "success":
        sub_ops.append({"command": cmd_str, "status": "success", "duration_ms": duration_ms})
        raw_data[key] = result["data"]
        data: dict = result["data"]
        return data
    else:
        sub_ops.append(
            {
                "command": cmd_str,
                "status": "error",
                "duration_ms": duration_ms,
                "error": result.get("error", ""),
            }
        )
        return None


# --- BGP Session Diagnosis ---


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def eos_diagnose_bgp_session(host: str, neighbor_ip: str) -> BgpDiagnosticOutput:
    """Diagnose a BGP session end-to-end: peer state, route reachability, interface health, and prefix counts.

    Runs multiple show commands to build a comprehensive diagnosis with severity, findings,
    and recommended actions. Always uses fresh data (bypasses cache).

    For interactive step-by-step investigation, use the bgp_health_check prompt instead.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        neighbor_ip: IP address of the BGP neighbor to diagnose.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}  # type: ignore[return-value]
    ip_err = validate_ip_or_hostname(neighbor_ip, "neighbor_ip")
    if ip_err:
        return {"status": "error", "device": host, "error": ip_err}  # type: ignore[return-value]

    start_time = time.monotonic()
    sub_ops: list[dict] = []
    raw_data: dict = {}
    findings: list[dict] = []
    has_partial = False

    # Sub-op 1: BGP summary
    bgp_summary_data = _run_sub_op(host, ["show ip bgp summary"], sub_ops, raw_data, "bgp_summary")
    if bgp_summary_data is None:
        has_partial = True

    # Find peer in summary
    peer_info = None
    if bgp_summary_data:
        peers = bgp_summary_data[0].get("vrfs", {}).get("default", {}).get("peers", {})
        peer_info = peers.get(neighbor_ip)

    if bgp_summary_data and peer_info is None:
        return _make_workflow_result(  # type: ignore[return-value]
            status="success",
            device=host,
            workflow="diagnose_bgp_session",
            start_time=start_time,
            severity="critical",
            summary=f"BGP peer {neighbor_ip} not found in BGP configuration.",
            findings=[
                {
                    "component": f"bgp_peer_{neighbor_ip}",
                    "status": "not_configured",
                    "expected": "Configured",
                    "detail": f"Peer {neighbor_ip} does not exist in 'show ip bgp summary'.",
                    "possible_causes": [
                        "Peer IP address is incorrect",
                        "BGP neighbor not configured on this device",
                        "Peer configured in a non-default VRF",
                    ],
                    "recommended_actions": [
                        "Verify the neighbor IP address",
                        "Check BGP configuration: 'show running-config section router bgp'",
                        "Check if peer is in a VRF: 'show ip bgp summary vrf all'",
                    ],
                }
            ],
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    # Sub-op 2: BGP neighbor detail
    bgp_neighbor_data = _run_sub_op(host, [f"show ip bgp neighbors {neighbor_ip}"], sub_ops, raw_data, "bgp_neighbors")
    if bgp_neighbor_data is None:
        has_partial = True

    # Sub-op 3: Route to peer
    route_data = _run_sub_op(host, [f"show ip route {neighbor_ip}"], sub_ops, raw_data, "ip_route")
    if route_data is None:
        has_partial = True

    # Determine peer state and build diagnosis
    peer_state = peer_info.get("peerState", "Unknown") if peer_info else "Unknown"
    prefix_received = peer_info.get("prefixReceived", 0) if peer_info else 0
    peer_asn = peer_info.get("asn", "Unknown") if peer_info else "Unknown"
    local_asn = ""
    if bgp_summary_data:
        local_asn = bgp_summary_data[0].get("vrfs", {}).get("default", {}).get("asn", "")

    # Check for route to peer
    has_route = False
    if route_data:
        vrfs = route_data[0].get("vrfs", {})
        default_vrf = vrfs.get("default", {})
        routes = default_vrf.get("routes", {})
        has_route = len(routes) > 0

    # Sub-op 4: Interface check (if we know the connected interface from neighbor detail)
    connected_intf = None
    if bgp_neighbor_data:
        neighbor_detail = bgp_neighbor_data[0].get("vrfBgpNeighbors", {}).get("default", {}).get("peerList", [])
        if neighbor_detail:
            connected_intf = neighbor_detail[0].get("localInterface", "")

    if connected_intf and connected_intf.startswith(("Ethernet", "Loopback", "Vlan", "Port-Channel", "Management")):
        intf_data = _run_sub_op(host, [f"show interfaces {connected_intf}"], sub_ops, raw_data, "interface")
        if intf_data is None:
            has_partial = True
    else:
        intf_data = None

    # Build diagnosis based on peer state
    if peer_state == "Idle":
        severity = "critical"
        summary = f"BGP peer {neighbor_ip} (AS {peer_asn}) is Idle — session not attempting to connect."
        finding = {
            "component": f"bgp_peer_{neighbor_ip}",
            "status": "Idle",
            "expected": "Established",
            "detail": f"Peer {neighbor_ip} (AS {peer_asn}) is in Idle state.",
            "possible_causes": [
                "Interface to peer is down",
                "AS number mismatch in configuration",
                "BGP neighbor administratively shut down",
                "ACL blocking TCP port 179",
            ],
            "recommended_actions": [
                "Check interface status to peer",
                f"Verify AS configuration: local AS {local_asn}, remote AS {peer_asn}",
                "Check for 'neighbor shutdown' in BGP config",
                "Check IP access-lists for TCP/179 blocks",
            ],
        }
        # Enrich with interface status
        if intf_data:
            intf_info = list(intf_data[0].get("interfaces", {}).values())
            if intf_info and intf_info[0].get("lineProtocolStatus") != "up":
                finding["possible_causes"].insert(0, f"Connected interface {connected_intf} is down")  # type: ignore[attr-defined]
                finding["recommended_actions"].insert(0, f"Bring up interface {connected_intf}")  # type: ignore[attr-defined]
        if not has_route:
            finding["possible_causes"].append("No route to peer IP address")  # type: ignore[attr-defined]
            finding["recommended_actions"].append(f"Add a route to {neighbor_ip}")  # type: ignore[attr-defined]
        findings.append(finding)

    elif peer_state == "Active":
        severity = "critical"
        summary = f"BGP peer {neighbor_ip} (AS {peer_asn}) is Active — TCP connection failing."
        finding = {
            "component": f"bgp_peer_{neighbor_ip}",
            "status": "Active",
            "expected": "Established",
            "detail": f"Peer {neighbor_ip} (AS {peer_asn}) is in Active state. TCP connection attempts are failing.",
            "possible_causes": [
                "Remote peer is not configured or not reachable",
                "TCP port 179 blocked by ACL or firewall",
                "No route to peer",
                "Peer-side BGP process not running",
            ],
            "recommended_actions": [
                f"Verify route to peer: 'show ip route {neighbor_ip}'",
                f"Test reachability: 'ping {neighbor_ip}'",
                "Check ACLs on both sides for TCP/179",
                "Verify BGP config on remote peer",
            ],
        }
        if not has_route:
            finding["possible_causes"].insert(0, f"No route to {neighbor_ip} in routing table")  # type: ignore[attr-defined]
        findings.append(finding)

    elif peer_state == "Established":
        if prefix_received == 0:
            severity = "warning"
            summary = f"BGP peer {neighbor_ip} (AS {peer_asn}) is Established but receiving 0 prefixes."
            findings.append(
                {
                    "component": f"bgp_peer_{neighbor_ip}",
                    "status": "Established (0 prefixes)",
                    "expected": "Established with prefixes",
                    "detail": f"Session is up but no prefixes received from {neighbor_ip}.",
                    "possible_causes": [
                        "Outbound route-map on peer filtering all routes",
                        "Inbound route-map or prefix-list filtering all routes",
                        "Peer has no routes to advertise",
                        "Address family not activated on peer",
                    ],
                    "recommended_actions": [
                        "Check inbound route-map: 'show route-map'",
                        "Check inbound prefix-list: 'show ip prefix-list'",
                        "Verify address family activation on both sides",
                        "Check peer's outbound policy",
                    ],
                }
            )
        else:
            severity = "healthy"
            summary = f"BGP peer {neighbor_ip} (AS {peer_asn}) is Established with {prefix_received} prefixes received."
            findings.append(
                {
                    "component": f"bgp_peer_{neighbor_ip}",
                    "status": "Established",
                    "expected": "Established",
                    "detail": f"Session is healthy. Receiving {prefix_received} prefixes from AS {peer_asn}.",
                    "possible_causes": [],
                    "recommended_actions": [],
                }
            )

    elif peer_state in ("OpenSent", "OpenConfirm", "Connect"):
        severity = "warning"
        summary = f"BGP peer {neighbor_ip} (AS {peer_asn}) is in {peer_state} — session negotiating."
        findings.append(
            {
                "component": f"bgp_peer_{neighbor_ip}",
                "status": peer_state,
                "expected": "Established",
                "detail": f"Peer is in {peer_state} state. Session is negotiating but not yet established.",
                "possible_causes": [
                    "Session is in the process of coming up",
                    "BGP capability mismatch",
                    "Authentication (MD5) mismatch",
                ],
                "recommended_actions": [
                    "Wait a few seconds and re-check",
                    "Check for authentication configuration mismatch",
                    "Review BGP neighbor logs",
                ],
            }
        )

    else:
        severity = "warning"
        summary = f"BGP peer {neighbor_ip} (AS {peer_asn}) is in unexpected state: {peer_state}."
        findings.append(
            {
                "component": f"bgp_peer_{neighbor_ip}",
                "status": peer_state,
                "expected": "Established",
                "detail": f"Peer is in {peer_state} state.",
                "possible_causes": ["Unknown state — may be transitional"],
                "recommended_actions": ["Check BGP neighbor detail for more information"],
            }
        )

    status = "partial" if has_partial else "success"
    return _make_workflow_result(  # type: ignore[return-value]
        status=status,
        device=host,
        workflow="diagnose_bgp_session",
        start_time=start_time,
        severity=severity,
        summary=summary,
        findings=findings,
        raw_data=raw_data,
        sub_operations=sub_ops,
    )


# --- Interface Troubleshooting ---


@mcp.tool(annotations=READ_ONLY)
def eos_troubleshoot_interface_issues(host: str, interface: str) -> dict:
    """Troubleshoot an interface end-to-end: link state, error counters, transceiver levels, and status.

    Runs multiple show commands to build a comprehensive diagnosis with severity, findings,
    and recommended actions. Always uses fresh data (bypasses cache).

    For interactive step-by-step investigation, use the troubleshoot_interface prompt instead.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        interface: Interface name to troubleshoot (e.g., 'Ethernet1', 'Port-Channel10').
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    intf_err = validate_interface_name(interface)
    if intf_err:
        return {"status": "error", "device": host, "error": intf_err}

    start_time = time.monotonic()
    sub_ops: list[dict] = []
    raw_data: dict = {}
    findings: list[dict] = []
    has_partial = False

    # Sub-op 1: Interface detail
    intf_data = _run_sub_op(host, [f"show interfaces {interface}"], sub_ops, raw_data, "interface_detail")
    if intf_data is None:
        has_partial = True

    # Sub-op 2: Interface counters errors
    error_cmd = f"show interfaces {interface} counters errors"
    error_data = _run_sub_op(host, [error_cmd], sub_ops, raw_data, "interface_errors")
    if error_data is None:
        has_partial = True

    # Sub-op 3: Transceiver info
    xcvr_data = _run_sub_op(host, [f"show interfaces {interface} transceiver"], sub_ops, raw_data, "transceiver")
    if xcvr_data is None:
        has_partial = True

    # Sub-op 4: Interface status
    status_data = _run_sub_op(host, [f"show interfaces {interface} status"], sub_ops, raw_data, "interface_status")
    if status_data is None:
        has_partial = True

    # If we have no interface data at all, return error
    if intf_data is None and error_data is None and status_data is None:
        return _make_workflow_result(
            status="error",
            device=host,
            workflow="troubleshoot_interface_issues",
            start_time=start_time,
            severity="critical",
            summary=f"Unable to retrieve any data for interface {interface}.",
            findings=[
                {
                    "component": interface,
                    "status": "unknown",
                    "expected": "data_available",
                    "detail": "All sub-operations failed. Device may be unreachable or interface may not exist.",
                    "possible_causes": ["Device unreachable", "Interface does not exist"],
                    "recommended_actions": [
                        "Verify device connectivity",
                        f"Check interface name: 'show interfaces {interface}'",
                    ],
                }
            ],
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    # Analyze interface detail
    intf_info = None
    if intf_data:
        interfaces = intf_data[0].get("interfaces", {})
        intf_info = interfaces.get(interface)

    if intf_info is None and intf_data:
        return _make_workflow_result(
            status="success",
            device=host,
            workflow="troubleshoot_interface_issues",
            start_time=start_time,
            severity="critical",
            summary=f"Interface {interface} not found on device.",
            findings=[
                {
                    "component": interface,
                    "status": "not_found",
                    "expected": "present",
                    "detail": f"Interface {interface} does not exist on this device.",
                    "possible_causes": ["Interface name is incorrect", "Interface not provisioned"],
                    "recommended_actions": ["Check available interfaces: 'show interfaces status'"],
                }
            ],
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    # Check admin/link status
    line_status = intf_info.get("lineProtocolStatus", "unknown") if intf_info else "unknown"
    intf_status = intf_info.get("interfaceStatus", "unknown") if intf_info else "unknown"

    # Admin down check
    if intf_status == "disabled" or (intf_info and intf_info.get("interfaceStatus") == "disabled"):
        findings.append(
            {
                "component": interface,
                "status": "adminDown",
                "expected": "up",
                "detail": f"Interface {interface} is administratively disabled (shutdown).",
                "possible_causes": ["Interface has been manually shut down", "Maintenance mode"],
                "recommended_actions": [
                    f"Enable interface: 'no shutdown' under 'interface {interface}'",
                    "Verify if shutdown was intentional",
                ],
            }
        )
        return _make_workflow_result(
            status="partial" if has_partial else "success",
            device=host,
            workflow="troubleshoot_interface_issues",
            start_time=start_time,
            severity="critical",
            summary=f"Interface {interface} is administratively disabled.",
            findings=findings,
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    # Link down check
    if line_status != "up":
        finding = {
            "component": interface,
            "status": f"linkDown ({line_status})",
            "expected": "up",
            "detail": f"Interface {interface} link protocol is {line_status}.",
            "possible_causes": [
                "Cable disconnected or faulty",
                "Remote side is down",
                "Speed/duplex mismatch",
            ],
            "recommended_actions": [
                "Check physical cable connection",
                "Verify remote device interface status",
                "Check speed/duplex settings",
            ],
        }

        # Check transceiver for optical issues
        if xcvr_data:
            xcvr_interfaces = xcvr_data[0].get("interfaces", {})
            xcvr_info = xcvr_interfaces.get(interface, {})
            rx_power = xcvr_info.get("rxPower")
            if rx_power is not None and isinstance(rx_power, (int, float)) and rx_power < -25.0:
                finding["possible_causes"].insert(0, f"Very low Rx optical power: {rx_power} dBm")  # type: ignore[attr-defined]
                finding["recommended_actions"].insert(0, "Check fiber optic cable and clean connectors")  # type: ignore[attr-defined]

        findings.append(finding)
        return _make_workflow_result(
            status="partial" if has_partial else "success",
            device=host,
            workflow="troubleshoot_interface_issues",
            start_time=start_time,
            severity="critical",
            summary=f"Interface {interface} link is down ({line_status}).",
            findings=findings,
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    # Interface is up — check for errors
    severity = "healthy"
    summary_parts = []

    # Check error counters
    if error_data:
        error_counters = error_data[0].get("interfaceErrorCounters", {}).get(interface, {})
        crc_errors = error_counters.get("frameTooLongs", 0) + error_counters.get("fcsErrors", 0)
        input_errors = error_counters.get("inputErrors", 0)
        output_errors = error_counters.get("outputErrors", 0)
        align_errors = error_counters.get("alignmentErrors", 0)

        total_counters = intf_info.get("interfaceCounters", {}) if intf_info else {}
        in_packets = (
            total_counters.get("inUcastPkts", 0)
            + total_counters.get("inMulticastPkts", 0)
            + total_counters.get("inBroadcastPkts", 0)
        )

        if crc_errors > 0:
            # Check if CRC errors are significant (> 0.1% of total input)
            error_rate = (crc_errors / in_packets * 100) if in_packets > 0 else 100
            if error_rate > 0.1 or crc_errors > 100:
                severity = "warning"
                summary_parts.append(f"{crc_errors} CRC/FCS errors")
                findings.append(
                    {
                        "component": f"{interface}_errors",
                        "status": f"{crc_errors} CRC/FCS errors ({error_rate:.2f}% of traffic)",
                        "expected": "0 errors",
                        "detail": "Possible physical layer issue: cable, optic, or speed mismatch.",
                        "possible_causes": [
                            "Bad cable or fiber optic",
                            "Dirty fiber connectors",
                            "Speed mismatch between endpoints",
                            "Faulty transceiver",
                        ],
                        "recommended_actions": [
                            "Replace cable or clean fiber connectors",
                            "Check speed settings on both ends",
                            "Replace transceiver if optical",
                            "Check interface counters trend over time",
                        ],
                    }
                )

        if input_errors > 0 and in_packets > 0:
            input_error_rate = input_errors / in_packets * 100
            if input_error_rate > 0.1 or input_errors > 100:
                severity = "warning"
                summary_parts.append(f"{input_errors} input errors")
                findings.append(
                    {
                        "component": f"{interface}_input_errors",
                        "status": f"{input_errors} input errors ({input_error_rate:.2f}% of traffic)",
                        "expected": "0 errors",
                        "detail": "Possible duplex mismatch or congestion.",
                        "possible_causes": [
                            "Duplex mismatch",
                            "Congestion causing drops",
                            "MTU mismatch",
                        ],
                        "recommended_actions": [
                            "Check duplex settings on both ends",
                            "Verify MTU configuration",
                            "Monitor traffic utilization",
                        ],
                    }
                )

        if align_errors > 100:
            severity = "warning"
            summary_parts.append(f"{align_errors} alignment errors")
            findings.append(
                {
                    "component": f"{interface}_alignment",
                    "status": f"{align_errors} alignment errors",
                    "expected": "0 errors",
                    "detail": "Alignment errors indicate physical layer problems.",
                    "possible_causes": ["Bad cable", "Speed/duplex mismatch"],
                    "recommended_actions": ["Replace cable", "Check speed/duplex"],
                }
            )

        if output_errors > 100:
            if severity == "healthy":
                severity = "warning"
            summary_parts.append(f"{output_errors} output errors")
            findings.append(
                {
                    "component": f"{interface}_output_errors",
                    "status": f"{output_errors} output errors",
                    "expected": "0 errors",
                    "detail": "Output errors may indicate congestion or buffer issues.",
                    "possible_causes": ["Interface congestion", "Buffer exhaustion"],
                    "recommended_actions": ["Check interface utilization", "Review QoS policy"],
                }
            )

    # Check transceiver power levels
    if xcvr_data:
        xcvr_interfaces = xcvr_data[0].get("interfaces", {})
        xcvr_info = xcvr_interfaces.get(interface, {})
        rx_power = xcvr_info.get("rxPower")
        if rx_power is not None and isinstance(rx_power, (int, float)) and rx_power < -20.0:
            if severity == "healthy":
                severity = "warning"
            summary_parts.append(f"low Rx power ({rx_power} dBm)")
            findings.append(
                {
                    "component": f"{interface}_transceiver",
                    "status": f"Rx power: {rx_power} dBm (low)",
                    "expected": "> -20.0 dBm",
                    "detail": "Low receive optical power may cause intermittent errors.",
                    "possible_causes": [
                        "Dirty fiber connectors",
                        "Long fiber distance",
                        "Degraded transceiver",
                    ],
                    "recommended_actions": [
                        "Clean fiber connectors",
                        "Check fiber distance against transceiver spec",
                        "Replace transceiver if cleaning doesn't help",
                    ],
                }
            )

    if severity == "healthy":
        summary = f"Interface {interface} is up and healthy. No errors detected."
        findings.append(
            {
                "component": interface,
                "status": "up",
                "expected": "up",
                "detail": f"Interface {interface} is operating normally.",
                "possible_causes": [],
                "recommended_actions": [],
            }
        )
    else:
        summary = f"Interface {interface} is up with issues: {'; '.join(summary_parts)}."

    status = "partial" if has_partial else "success"
    return _make_workflow_result(
        status=status,
        device=host,
        workflow="troubleshoot_interface_issues",
        start_time=start_time,
        severity=severity,
        summary=summary,
        findings=findings,
        raw_data=raw_data,
        sub_operations=sub_ops,
    )


# --- Config Comparison ---


@mcp.tool(annotations=READ_ONLY)
def eos_compare_device_configs(host1: str, host2: str, sections: list[str] | None = None) -> dict:
    """Compare running configurations between two Arista EOS devices, optionally filtered by sections.

    Fetches running-config (or specific sections) from both devices and produces a section-aware
    diff showing additions, removals, and changes. Always uses fresh data (bypasses cache).

    Args:
        host1: Hostname, IP address, or inventory name of the first Arista EOS device.
        host2: Hostname, IP address, or inventory name of the second Arista EOS device.
        sections: Optional list of config sections to compare (e.g., ['router bgp', 'interface']).
                  If not specified, compares the full running-config.
    """
    host1_err = validate_host(host1)
    if host1_err:
        return {"status": "error", "device": host1, "error": host1_err}
    host2_err = validate_host(host2)
    if host2_err:
        return {"status": "error", "device": host2, "error": host2_err}

    start_time = time.monotonic()
    sub_ops: list[dict] = []
    raw_data: dict = {}
    findings: list[dict] = []
    has_partial = False

    # Build commands based on sections
    if sections:
        commands = [f"show running-config section {s}" for s in sections]
    else:
        commands = ["show running-config"]

    def _fetch_config(target_host, config_lines):
        """Fetch config from a host, appending lines and recording sub-ops."""
        nonlocal has_partial
        for cmd in commands:
            start = time.monotonic()
            result = run_show_command(conn_mgr, target_host, [cmd], encoding="text", cache_ttl=0)
            dur = int((time.monotonic() - start) * 1000)
            cmd_label = f"{cmd} ({target_host})"
            if result["status"] == "success":
                sub_ops.append(
                    {
                        "command": cmd_label,
                        "status": "success",
                        "duration_ms": dur,
                    }
                )
                data_item = result["data"][0]
                if isinstance(data_item, dict):
                    output_text = data_item.get("output", "")
                else:
                    output_text = str(data_item)
                config_lines.extend(output_text.splitlines())
                raw_key = f"config_{target_host}"
                raw_data.setdefault(raw_key, []).append(output_text)
            else:
                sub_ops.append(
                    {
                        "command": cmd_label,
                        "status": "error",
                        "duration_ms": dur,
                        "error": result.get("error", ""),
                    }
                )
                has_partial = True

    # Fetch config from both hosts
    config1_lines: list[str] = []
    _fetch_config(host1, config1_lines)
    config2_lines: list[str] = []
    _fetch_config(host2, config2_lines)

    # If both configs failed, return error
    if not config1_lines and not config2_lines:
        return _make_workflow_result(
            status="error",
            device=f"{host1},{host2}",
            workflow="compare_device_configs",
            start_time=start_time,
            severity="critical",
            summary="Unable to retrieve configuration from either device.",
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    # Generate unified diff
    diff_lines = list(
        difflib.unified_diff(
            config1_lines,
            config2_lines,
            fromfile=host1,
            tofile=host2,
            lineterm="",
        )
    )

    # Count additions, removals, changes
    additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removals = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    if additions == 0 and removals == 0:
        severity = "healthy"
        section_desc = f" (sections: {', '.join(sections)})" if sections else ""
        summary = f"Configurations are identical between {host1} and {host2}{section_desc}."
        findings.append(
            {
                "component": "config_diff",
                "status": "identical",
                "expected": "identical",
                "detail": "No differences found between device configurations.",
                "possible_causes": [],
                "recommended_actions": [],
            }
        )
    else:
        severity = "warning"
        section_desc = f" (sections: {', '.join(sections)})" if sections else ""
        summary = f"Found {additions} additions and {removals} removals between {host1} and {host2}{section_desc}."
        findings.append(
            {
                "component": "config_diff",
                "status": f"{additions} additions, {removals} removals",
                "expected": "identical",
                "detail": "\n".join(diff_lines[:100]),  # Limit diff output
                "possible_causes": [
                    "Intentional configuration differences between devices",
                    "Configuration drift",
                    "Incomplete change deployment",
                ],
                "recommended_actions": [
                    "Review the diff to determine if differences are intentional",
                    "If unintentional, align configurations",
                    "Consider using config sessions for atomic changes",
                ],
            }
        )
        if len(diff_lines) > 100:
            findings[0]["detail"] += f"\n... ({len(diff_lines) - 100} more lines truncated)"

    status = "partial" if has_partial else "success"
    return _make_workflow_result(
        status=status,
        device=f"{host1},{host2}",
        workflow="compare_device_configs",
        start_time=start_time,
        severity=severity,
        summary=summary,
        findings=findings,
        raw_data=raw_data,
        sub_operations=sub_ops,
    )


# --- Fabric Health Validation ---


def _per_device_health_check(conn_mgr_ref, host):
    """Per-device health check operation for execute_on_devices.

    Runs show version + show ip bgp summary + show mlag and returns structured results.
    """
    results = {}
    errors = []

    # show version
    ver = run_show_command(conn_mgr_ref, host, ["show version"], cache_ttl=0)
    if ver["status"] == "success":
        results["version"] = ver["data"][0]
    else:
        errors.append(f"show version: {ver.get('error', 'unknown')}")

    # show ip bgp summary
    bgp = run_show_command(conn_mgr_ref, host, ["show ip bgp summary"], cache_ttl=0)
    if bgp["status"] == "success":
        results["bgp_summary"] = bgp["data"][0]
    else:
        errors.append(f"show ip bgp summary: {bgp.get('error', 'unknown')}")

    # show mlag
    mlag = run_show_command(conn_mgr_ref, host, ["show mlag"], cache_ttl=0)
    if mlag["status"] == "success":
        results["mlag"] = mlag["data"][0]
    else:
        errors.append(f"show mlag: {mlag.get('error', 'unknown')}")

    if not results and errors:
        return {"status": "error", "device": host, "error": "; ".join(errors)}

    return {"status": "success", "data": results, "errors": errors}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def eos_validate_fabric_health(role: str = "all") -> FabricHealthOutput:
    """Validate fabric health across all inventory devices (or filtered by role).

    Runs show version, show ip bgp summary, and show mlag on each device concurrently.
    Scores overall health as (healthy_checks / total_checks) * 100 and flags
    critical issues (BGP peers down, MLAG errors) and warnings (config-sanity, 0 prefixes).

    For interactive step-by-step investigation, use the network_audit prompt instead.

    Args:
        role: Device role filter. Use 'all' for all inventory devices, or a role name
              like 'spine', 'leaf' to filter. Supports executor target syntax:
              'role:spine', 'tag:dc1', 'group:prod', or comma-separated hostnames.
    """
    start_time = time.monotonic()

    # Resolve target hosts
    if role == "all":
        targets = "all"
    elif ":" in role or "," in role:
        targets = role
    else:
        targets = f"role:{role}"

    hosts = resolve_hosts(conn_mgr, targets)

    if not hosts:
        return _make_workflow_result(  # type: ignore[return-value]
            status="error",
            device="fabric",
            workflow="validate_fabric_health",
            start_time=start_time,
            severity="critical",
            summary="No devices found in inventory matching the specified role/filter.",
            findings=[
                {
                    "component": "inventory",
                    "status": "empty",
                    "expected": "devices_present",
                    "detail": f"No devices match target '{role}'. Check inventory file and role assignments.",
                    "possible_causes": [
                        "No inventory file configured (NET_INVENTORY_FILE)",
                        f"No devices with role '{role}' in inventory",
                    ],
                    "recommended_actions": [
                        "Verify NET_INVENTORY_FILE is set and contains devices",
                        "Check device role assignments in devices.yaml",
                    ],
                }
            ],
        )

    # Run health checks concurrently across devices
    multi_result = await execute_on_devices(conn_mgr, hosts, _per_device_health_check)

    # Analyze results
    findings = []
    total_checks = 0
    healthy_checks = 0
    raw_data = {}
    sub_ops = []

    for host_name in hosts:
        if host_name in multi_result.successes:
            device_data = multi_result.successes[host_name]
            data = device_data.get("data", {})
            device_errors = device_data.get("errors", [])
            raw_data[host_name] = data

            sub_ops.append({"command": f"health_check ({host_name})", "status": "success", "duration_ms": 0})

            # Check BGP peers
            bgp_data = data.get("bgp_summary", {})
            peers = bgp_data.get("vrfs", {}).get("default", {}).get("peers", {})
            for peer_ip, peer_info in peers.items():
                total_checks += 1
                peer_state = peer_info.get("peerState", "Unknown")
                prefix_received = peer_info.get("prefixReceived", 0)
                peer_asn = peer_info.get("asn", "Unknown")

                if peer_state == "Established" and prefix_received > 0:
                    healthy_checks += 1
                elif peer_state == "Established" and prefix_received == 0:
                    findings.append(
                        {
                            "component": f"{host_name}/bgp_peer_{peer_ip}",
                            "status": "Established (0 prefixes)",
                            "expected": "Established with prefixes",
                            "detail": f"BGP peer {peer_ip} (AS {peer_asn}) on {host_name} receiving 0 prefixes.",
                            "possible_causes": ["Route-map filtering all routes", "Peer has no routes to advertise"],
                            "recommended_actions": ["Check route-maps and prefix-lists"],
                        }
                    )
                else:
                    findings.append(
                        {
                            "component": f"{host_name}/bgp_peer_{peer_ip}",
                            "status": peer_state,
                            "expected": "Established",
                            "detail": f"BGP peer {peer_ip} (AS {peer_asn}) on {host_name} is {peer_state}.",
                            "possible_causes": ["Peer unreachable", "Configuration mismatch", "Interface down"],
                            "recommended_actions": [f"Run eos_diagnose_bgp_session on {host_name} for peer {peer_ip}"],
                        }
                    )

            # Check MLAG
            mlag_data = data.get("mlag", {})
            mlag_state = mlag_data.get("state", "")
            if mlag_state and mlag_state != "disabled":
                total_checks += 1
                config_sanity = mlag_data.get("configSanity", "consistent")
                neg_status = mlag_data.get("negStatus", "")
                inactive_ports = mlag_data.get("mlagPorts", {}).get("Inactive", 0)

                if neg_status == "connected" and config_sanity == "consistent" and inactive_ports == 0:
                    healthy_checks += 1
                elif config_sanity != "consistent":
                    findings.append(
                        {
                            "component": f"{host_name}/mlag",
                            "status": f"config-sanity: {config_sanity}",
                            "expected": "consistent",
                            "detail": f"MLAG config-sanity on {host_name} is {config_sanity}.",
                            "possible_causes": ["MLAG configuration mismatch between peers"],
                            "recommended_actions": ["Run 'show mlag config-sanity' for details"],
                        }
                    )
                elif neg_status != "connected":
                    findings.append(
                        {
                            "component": f"{host_name}/mlag",
                            "status": f"negStatus: {neg_status}",
                            "expected": "connected",
                            "detail": f"MLAG on {host_name} peer link is {neg_status}.",
                            "possible_causes": ["Peer link down", "MLAG peer unreachable"],
                            "recommended_actions": ["Check peer link port-channel", "Verify MLAG peer connectivity"],
                        }
                    )
                elif inactive_ports > 0:
                    findings.append(
                        {
                            "component": f"{host_name}/mlag",
                            "status": f"{inactive_ports} inactive ports",
                            "expected": "0 inactive ports",
                            "detail": f"MLAG on {host_name} has {inactive_ports} inactive port(s).",
                            "possible_causes": ["MLAG member interface down", "Port-channel misconfiguration"],
                            "recommended_actions": ["Check MLAG interface status"],
                        }
                    )

            # Check version (always a pass if we got data)
            if data.get("version"):
                total_checks += 1
                healthy_checks += 1

            # Note device-level sub-op errors
            for err in device_errors:
                findings.append(
                    {
                        "component": f"{host_name}/sub_op",
                        "status": "partial_failure",
                        "expected": "success",
                        "detail": f"Sub-operation on {host_name} failed: {err}",
                        "possible_causes": ["Command not supported", "Permission denied"],
                        "recommended_actions": ["Check device capabilities"],
                    }
                )

        elif host_name in multi_result.errors:
            error_msg = multi_result.errors[host_name]
            sub_ops.append(
                {"command": f"health_check ({host_name})", "status": "error", "duration_ms": 0, "error": error_msg}
            )
            total_checks += 1  # Count the device itself as a check
            findings.append(
                {
                    "component": f"{host_name}/device",
                    "status": "unreachable",
                    "expected": "reachable",
                    "detail": f"Device {host_name} failed: {error_msg}",
                    "possible_causes": ["Device unreachable", "Authentication failure", "eAPI not enabled"],
                    "recommended_actions": [
                        f"Verify connectivity to {host_name}",
                        "Check eAPI is enabled on device",
                    ],
                }
            )

    # Calculate score and determine severity
    score = (healthy_checks / total_checks * 100) if total_checks > 0 else 0

    # Determine severity based on findings
    has_critical = any(
        "bgp_peer" in f["component"] and f["status"] not in ("Established (0 prefixes)",)
        for f in findings
        if f["status"] != "partial_failure"
    )
    has_mlag_error = any(
        "mlag" in f["component"]
        and f["status"] not in ("partial_failure",)
        and "config-sanity" not in f.get("status", "")
        for f in findings
    )
    has_mlag_sanity = any("config-sanity" in f.get("status", "") for f in findings)
    has_device_unreachable = any(f["status"] == "unreachable" for f in findings)

    if has_critical or has_mlag_error or has_device_unreachable:
        severity = "critical"
    elif has_mlag_sanity or any(f["status"] == "Established (0 prefixes)" for f in findings):
        severity = "warning"
    else:
        severity = "healthy"

    summary = (
        f"Fabric health score: {score:.0f}% ({healthy_checks}/{total_checks} checks passed) "
        f"across {len(hosts)} device(s)."
    )
    if findings:
        summary += f" Found {len(findings)} issue(s)."

    # Determine status
    if multi_result.error_count == len(hosts):
        status = "error"
    elif multi_result.error_count > 0:
        status = "partial"
    else:
        status = "success"

    return _make_workflow_result(  # type: ignore[return-value]
        status=status,
        device="fabric",
        workflow="validate_fabric_health",
        start_time=start_time,
        severity=severity,
        summary=summary,
        findings=findings,
        raw_data=raw_data,
        sub_operations=sub_ops,
    )


# --- Snapshot Storage ---


class _SnapshotStore:
    """In-memory snapshot storage with LRU eviction, TTL expiry, and optional file persistence."""

    MAX_SNAPSHOTS = 50

    def __init__(self) -> None:
        self._snapshots: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._settings: NetworkSettings | None = None

    def _get_settings(self) -> NetworkSettings:
        if self._settings is None:
            self._settings = NetworkSettings()
        return self._settings

    def _evict_expired(self) -> None:
        """Remove expired snapshots. Called under lock."""
        ttl = self._get_settings().net_snapshot_ttl
        now = time.time()
        expired = [sid for sid, snap in self._snapshots.items() if now - snap["_created_at"] > ttl]
        for sid in expired:
            self._snapshots.pop(sid, None)

    def _evict_lru(self) -> None:
        """Evict oldest snapshot if over capacity. Called under lock."""
        while len(self._snapshots) >= self.MAX_SNAPSHOTS:
            oldest_id = next(iter(self._snapshots))
            self._snapshots.pop(oldest_id)

    def store(self, snapshot_id: str, data: dict) -> None:
        """Store a snapshot with metadata."""
        with self._lock:
            self._evict_expired()
            self._evict_lru()
            data["_created_at"] = time.time()
            self._snapshots[snapshot_id] = data

        # Optional file persistence
        snapshot_dir = self._get_settings().net_snapshot_dir
        if snapshot_dir:
            try:
                os.makedirs(snapshot_dir, exist_ok=True)
                filepath = os.path.join(snapshot_dir, f"{snapshot_id}.json")
                with open(filepath, "w") as f:
                    json.dump(data, f, indent=2, default=str)
            except Exception:
                logger.warning("Failed to persist snapshot %s to disk", snapshot_id, exc_info=True)

    def get(self, snapshot_id: str) -> dict | None:
        """Retrieve a snapshot by ID. Returns None if not found or expired."""
        with self._lock:
            snap = self._snapshots.get(snapshot_id)
            if snap is not None:
                ttl = self._get_settings().net_snapshot_ttl
                if time.time() - snap["_created_at"] > ttl:
                    self._snapshots.pop(snapshot_id, None)
                    return None
                return snap

        # Try loading from disk if not in memory
        snapshot_dir = self._get_settings().net_snapshot_dir
        if snapshot_dir:
            filepath = os.path.join(snapshot_dir, f"{snapshot_id}.json")
            if os.path.exists(filepath):
                try:
                    with open(filepath) as f:
                        data = json.load(f)
                    ttl = self._get_settings().net_snapshot_ttl
                    created_at = data.get("_created_at", 0)
                    if time.time() - created_at > ttl:
                        return None
                    with self._lock:
                        self._snapshots[snapshot_id] = data
                    result_data: dict = data
                    return result_data
                except Exception:
                    logger.warning("Failed to load snapshot %s from disk", snapshot_id, exc_info=True)
        return None

    def clear(self) -> None:
        """Clear all snapshots (for testing)."""
        with self._lock:
            self._snapshots.clear()


# Module-level singleton
_snapshot_store = _SnapshotStore()

# Commands used for pre/post change snapshots
_SNAPSHOT_COMMANDS = [
    ("show version", "version"),
    ("show ip bgp summary", "bgp_summary"),
    ("show interfaces status", "interfaces_status"),
    ("show mlag", "mlag"),
    ("show ip route summary", "route_summary"),
    ("show vlan", "vlans"),
]


# --- Pre-Change Snapshot ---


@mcp.tool(annotations=READ_ONLY)
def eos_pre_change_snapshot(host: str) -> dict:
    """Capture a pre-change state snapshot of a device for later comparison with post_change_validation.

    Collects: show version, show ip bgp summary, show interfaces status, show mlag,
    show ip route summary, show vlan. Stores the snapshot in memory (with optional file
    persistence via NET_SNAPSHOT_DIR). Returns a snapshot_id for use with eos_post_change_validation.

    Snapshots expire after NET_SNAPSHOT_TTL seconds (default 3600). Max 50 snapshots stored.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    start_time = time.monotonic()
    sub_ops: list[dict] = []
    raw_data: dict = {}
    snapshot_data: dict = {}
    components: list[str] = []
    has_partial = False

    for cmd, key in _SNAPSHOT_COMMANDS:
        data = _run_sub_op(host, [cmd], sub_ops, raw_data, key)
        if data is not None:
            snapshot_data[key] = data
            components.append(key)
        else:
            has_partial = True

    if not snapshot_data:
        return _make_workflow_result(
            status="error",
            device=host,
            workflow="pre_change_snapshot",
            start_time=start_time,
            severity="critical",
            summary=f"Failed to capture any data from {host}.",
            findings=[
                {
                    "component": "snapshot",
                    "status": "failed",
                    "expected": "data_captured",
                    "detail": "All show commands failed. Device may be unreachable.",
                    "possible_causes": ["Device unreachable", "Authentication failure"],
                    "recommended_actions": ["Verify device connectivity"],
                }
            ],
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    snapshot_id = str(uuid.uuid4())
    captured_at = datetime.datetime.now(tz=datetime.UTC).isoformat()
    snapshot_record = {
        "snapshot_id": snapshot_id,
        "host": host,
        "captured_at": captured_at,
        "components": components,
        "data": snapshot_data,
    }
    _snapshot_store.store(snapshot_id, snapshot_record)

    status = "partial" if has_partial else "success"
    return _make_workflow_result(
        status=status,
        device=host,
        workflow="pre_change_snapshot",
        start_time=start_time,
        severity="healthy",
        summary=f"Snapshot captured successfully. ID: {snapshot_id}",
        findings=[
            {
                "component": "snapshot",
                "status": "captured",
                "expected": "captured",
                "detail": f"Captured {len(components)} components: {', '.join(components)}.",
                "possible_causes": [],
                "recommended_actions": [
                    f"Use eos_post_change_validation(host='{host}', snapshot_id='{snapshot_id}') after making changes."
                ],
            }
        ],
        raw_data={
            "snapshot_id": snapshot_id,
            "captured_at": captured_at,
            "components": components,
        },
        sub_operations=sub_ops,
    )


# --- Post-Change Validation ---


def _compare_bgp_peers(pre_peers: dict, post_peers: dict) -> tuple[list, list, list]:
    """Compare BGP peer states between pre and post snapshots."""
    changes = []
    regressions = []
    improvements = []

    all_peers = set(list(pre_peers.keys()) + list(post_peers.keys()))
    for peer_ip in all_peers:
        pre_info = pre_peers.get(peer_ip)
        post_info = post_peers.get(peer_ip)

        if pre_info and not post_info:
            regressions.append(
                {
                    "component": f"bgp_peer_{peer_ip}",
                    "type": "peer_lost",
                    "detail": f"BGP peer {peer_ip} was present before but is now gone.",
                    "pre_state": pre_info.get("peerState", "Unknown"),
                    "post_state": "absent",
                }
            )
        elif not pre_info and post_info:
            improvements.append(
                {
                    "component": f"bgp_peer_{peer_ip}",
                    "type": "peer_added",
                    "detail": f"New BGP peer {peer_ip} appeared after changes.",
                    "pre_state": "absent",
                    "post_state": post_info.get("peerState", "Unknown"),
                }
            )
        elif pre_info and post_info:
            pre_state = pre_info.get("peerState", "Unknown")
            post_state = post_info.get("peerState", "Unknown")
            if pre_state != post_state:
                entry = {
                    "component": f"bgp_peer_{peer_ip}",
                    "type": "state_change",
                    "detail": f"BGP peer {peer_ip} changed from {pre_state} to {post_state}.",
                    "pre_state": pre_state,
                    "post_state": post_state,
                }
                if pre_state == "Established" and post_state != "Established":
                    regressions.append(entry)
                elif pre_state != "Established" and post_state == "Established":
                    improvements.append(entry)
                else:
                    changes.append(entry)

    return changes, regressions, improvements


def _compare_interfaces(pre_intfs: dict, post_intfs: dict) -> tuple[list, list, list]:
    """Compare interface status between pre and post snapshots."""
    changes = []
    regressions = []
    improvements = []

    pre_statuses = pre_intfs.get("interfaceStatuses", {})
    post_statuses = post_intfs.get("interfaceStatuses", {})

    all_intfs = set(list(pre_statuses.keys()) + list(post_statuses.keys()))
    for intf in all_intfs:
        pre_info = pre_statuses.get(intf)
        post_info = post_statuses.get(intf)

        if pre_info and not post_info:
            changes.append(
                {
                    "component": f"interface_{intf}",
                    "type": "interface_removed",
                    "detail": f"Interface {intf} no longer present.",
                    "pre_state": pre_info.get("linkStatus", "unknown"),
                    "post_state": "absent",
                }
            )
        elif not pre_info and post_info:
            changes.append(
                {
                    "component": f"interface_{intf}",
                    "type": "interface_added",
                    "detail": f"New interface {intf} appeared.",
                    "pre_state": "absent",
                    "post_state": post_info.get("linkStatus", "unknown"),
                }
            )
        elif pre_info and post_info:
            pre_link = pre_info.get("linkStatus", "unknown")
            post_link = post_info.get("linkStatus", "unknown")
            if pre_link != post_link:
                entry = {
                    "component": f"interface_{intf}",
                    "type": "link_change",
                    "detail": f"Interface {intf} link changed from {pre_link} to {post_link}.",
                    "pre_state": pre_link,
                    "post_state": post_link,
                }
                if pre_link == "connected" and post_link != "connected":
                    regressions.append(entry)
                elif pre_link != "connected" and post_link == "connected":
                    improvements.append(entry)
                else:
                    changes.append(entry)

    return changes, regressions, improvements


def _compare_mlag(pre_mlag: dict, post_mlag: dict) -> tuple[list, list, list]:
    """Compare MLAG state between pre and post snapshots."""
    changes = []
    regressions = []
    improvements = []

    pre_state = pre_mlag.get("state", "")
    post_state = post_mlag.get("state", "")
    pre_neg = pre_mlag.get("negStatus", "")
    post_neg = post_mlag.get("negStatus", "")
    pre_sanity = pre_mlag.get("configSanity", "consistent")
    post_sanity = post_mlag.get("configSanity", "consistent")

    if pre_neg != post_neg:
        entry = {
            "component": "mlag_peer",
            "type": "neg_status_change",
            "detail": f"MLAG peer link changed from {pre_neg} to {post_neg}.",
            "pre_state": pre_neg,
            "post_state": post_neg,
        }
        if pre_neg == "connected" and post_neg != "connected":
            regressions.append(entry)
        elif pre_neg != "connected" and post_neg == "connected":
            improvements.append(entry)
        else:
            changes.append(entry)

    if pre_sanity != post_sanity:
        entry = {
            "component": "mlag_config_sanity",
            "type": "sanity_change",
            "detail": f"MLAG config-sanity changed from {pre_sanity} to {post_sanity}.",
            "pre_state": pre_sanity,
            "post_state": post_sanity,
        }
        if pre_sanity == "consistent" and post_sanity != "consistent":
            regressions.append(entry)
        elif pre_sanity != "consistent" and post_sanity == "consistent":
            improvements.append(entry)
        else:
            changes.append(entry)

    if pre_state != post_state:
        changes.append(
            {
                "component": "mlag_state",
                "type": "state_change",
                "detail": f"MLAG state changed from {pre_state} to {post_state}.",
                "pre_state": pre_state,
                "post_state": post_state,
            }
        )

    return changes, regressions, improvements


@mcp.tool(annotations=READ_ONLY)
def eos_post_change_validation(host: str, snapshot_id: str) -> dict:
    """Validate current device state against a pre-change snapshot to detect regressions and improvements.

    Runs the same commands as eos_pre_change_snapshot and compares results against the stored snapshot.
    Reports: changes (neutral differences), regressions (things that got worse), and
    improvements (things that got better).

    Regressions include: BGP peers lost, interfaces down that were up, MLAG errors introduced.
    Improvements include: new BGP peers established, error counters cleared, MLAG issues resolved.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        snapshot_id: UUID from a previous eos_pre_change_snapshot call.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    # Validate snapshot_id format
    cli_err = validate_host(snapshot_id)  # reuse for basic char validation
    if cli_err:
        return {"status": "error", "device": host, "error": f"Invalid snapshot_id: {cli_err}"}

    # Retrieve snapshot
    snapshot = _snapshot_store.get(snapshot_id)
    if snapshot is None:
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Snapshot '{snapshot_id}' not found or has expired. "
                "Snapshots expire after NET_SNAPSHOT_TTL seconds (default 3600). "
                "Ensure you call eos_pre_change_snapshot before making changes, "
                "and eos_post_change_validation within the same server session."
            ),
        }

    # Verify snapshot host matches
    snap_host = snapshot.get("host", "")
    if snap_host != host:
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Snapshot '{snapshot_id}' was captured for device '{snap_host}', "
                f"but you requested validation for '{host}'. Use the correct host."
            ),
        }

    start_time = time.monotonic()
    sub_ops: list[dict] = []
    raw_data: dict = {}
    current_data: dict = {}
    has_partial = False

    # Run same commands to get current state
    for cmd, key in _SNAPSHOT_COMMANDS:
        data = _run_sub_op(host, [cmd], sub_ops, raw_data, key)
        if data is not None:
            current_data[key] = data
        else:
            has_partial = True

    if not current_data:
        return _make_workflow_result(
            status="error",
            device=host,
            workflow="post_change_validation",
            start_time=start_time,
            severity="critical",
            summary=f"Failed to capture current state from {host} for comparison.",
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    # Compare pre vs post
    pre_data = snapshot.get("data", {})
    all_changes = []
    all_regressions = []
    all_improvements = []

    # Compare BGP
    pre_bgp = pre_data.get("bgp_summary", [{}])
    post_bgp = current_data.get("bgp_summary", [{}])
    if pre_bgp and post_bgp:
        pre_peers = pre_bgp[0].get("vrfs", {}).get("default", {}).get("peers", {})
        post_peers = post_bgp[0].get("vrfs", {}).get("default", {}).get("peers", {})
        c, r, i = _compare_bgp_peers(pre_peers, post_peers)
        all_changes.extend(c)
        all_regressions.extend(r)
        all_improvements.extend(i)

    # Compare interfaces
    pre_intfs = pre_data.get("interfaces_status", [{}])
    post_intfs = current_data.get("interfaces_status", [{}])
    if pre_intfs and post_intfs:
        c, r, i = _compare_interfaces(pre_intfs[0] if pre_intfs else {}, post_intfs[0] if post_intfs else {})
        all_changes.extend(c)
        all_regressions.extend(r)
        all_improvements.extend(i)

    # Compare MLAG
    pre_mlag = pre_data.get("mlag", [{}])
    post_mlag = current_data.get("mlag", [{}])
    if pre_mlag and post_mlag:
        c, r, i = _compare_mlag(pre_mlag[0] if pre_mlag else {}, post_mlag[0] if post_mlag else {})
        all_changes.extend(c)
        all_regressions.extend(r)
        all_improvements.extend(i)

    # Build findings from regressions, changes, and improvements
    findings = []
    for reg in all_regressions:
        findings.append(
            {
                "component": reg["component"],
                "status": f"REGRESSION: {reg['post_state']}",
                "expected": reg["pre_state"],
                "detail": reg["detail"],
                "possible_causes": ["Change introduced a regression"],
                "recommended_actions": ["Investigate the change that caused this regression", "Consider rollback"],
            }
        )
    for chg in all_changes:
        findings.append(
            {
                "component": chg["component"],
                "status": f"CHANGED: {chg['post_state']}",
                "expected": chg["pre_state"],
                "detail": chg["detail"],
                "possible_causes": ["Expected result of the change"],
                "recommended_actions": ["Verify this change was intentional"],
            }
        )
    for imp in all_improvements:
        findings.append(
            {
                "component": imp["component"],
                "status": f"IMPROVED: {imp['post_state']}",
                "expected": imp["pre_state"],
                "detail": imp["detail"],
                "possible_causes": ["Change resolved a previous issue"],
                "recommended_actions": [],
            }
        )

    # Determine severity
    if all_regressions:
        severity = "critical"
    elif all_changes:
        severity = "warning"
    else:
        severity = "healthy"

    # Build summary
    parts = []
    if all_regressions:
        parts.append(f"{len(all_regressions)} regression(s)")
    if all_changes:
        parts.append(f"{len(all_changes)} change(s)")
    if all_improvements:
        parts.append(f"{len(all_improvements)} improvement(s)")

    if not parts:
        summary = f"No changes detected on {host} compared to snapshot."
    else:
        summary = f"Post-change validation on {host}: {', '.join(parts)}."

    status = "partial" if has_partial else "success"
    return _make_workflow_result(
        status=status,
        device=host,
        workflow="post_change_validation",
        start_time=start_time,
        severity=severity,
        summary=summary,
        findings=findings,
        raw_data={
            "changes": all_changes,
            "regressions": all_regressions,
            "improvements": all_improvements,
            "pre_snapshot_id": snapshot_id,
            "pre_captured_at": snapshot.get("captured_at", ""),
        },
        sub_operations=sub_ops,
    )


# --- Interactive Troubleshooting with Elicitation ---


class TroubleshootAreaChoice(BaseModel):
    """Elicitation schema for selecting a troubleshooting area."""

    area: str = Field(description="Area to investigate: bgp, interfaces, health, or config")


# Diagnostic commands for each area
_DIAG_COMMANDS: dict[str, list[tuple[str, str, str]]] = {
    "bgp": [
        ("show ip bgp summary", "json", "bgp_summary"),
        ("show ip bgp neighbors", "json", "bgp_neighbors"),
        ("show ip route summary", "json", "route_summary"),
    ],
    "interfaces": [
        ("show interfaces status", "json", "interfaces_status"),
        ("show interfaces counters errors", "json", "interface_errors"),
        ("show lldp neighbors", "json", "lldp_neighbors"),
    ],
    "health": [
        ("show version", "json", "version"),
        ("show processes top once", "json", "cpu"),
        ("show environment temperature", "json", "temperature"),
        ("show ntp status", "json", "ntp_status"),
    ],
    "config": [
        ("show running-config diffs", "text", "config_diffs"),
        ("show logging last 20", "text", "recent_logs"),
        ("show reload cause", "json", "reload_cause"),
    ],
}


@mcp.tool(annotations=READ_ONLY)
async def eos_interactive_troubleshoot(host: str, ctx=None) -> dict:
    """Interactive troubleshooting session for an Arista EOS device.

    Guides you through diagnostics step by step, asking which area to
    investigate. Uses MCP elicitation to gather input when a Context is
    available. Falls back to running a general health check if the client
    does not support elicitation or no context is provided.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        ctx: MCP Context (injected by FastMCP when available).
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    start_time = time.monotonic()
    sub_ops: list[dict] = []
    raw_data: dict = {}

    # Step 1: Fetch initial device info
    version_data = _run_sub_op(host, ["show version"], sub_ops, raw_data, "version")
    if version_data is None:
        return _make_workflow_result(
            status="error",
            device=host,
            workflow="interactive_troubleshoot",
            start_time=start_time,
            severity="critical",
            summary=f"Cannot reach device {host} — show version failed.",
            findings=[
                {
                    "component": "device",
                    "status": "unreachable",
                    "expected": "reachable",
                    "detail": "Initial show version command failed.",
                    "possible_causes": ["Device unreachable", "Authentication failure", "eAPI not enabled"],
                    "recommended_actions": ["Verify device connectivity", "Check eAPI configuration"],
                }
            ],
            raw_data=raw_data,
            sub_operations=sub_ops,
        )

    # Step 2: Ask user which area to investigate (via elicitation)
    area = "health"  # Default fallback
    elicitation_status = "skipped"

    if ctx is not None and hasattr(ctx, "elicit"):
        try:
            result = await ctx.elicit(
                message=(
                    f"Connected to {host}. Which area would you like to investigate?\n"
                    "Options: bgp, interfaces, health, config"
                ),
                schema=TroubleshootAreaChoice,
            )
            if result.action == "accept" and result.data is not None:
                chosen = result.data.area.strip().lower()
                if chosen in _DIAG_COMMANDS:
                    area = chosen
                    elicitation_status = "accepted"
                else:
                    elicitation_status = f"accepted (invalid area '{chosen}', using health)"
            elif result.action == "decline":
                elicitation_status = "declined"
            else:
                elicitation_status = "cancelled"
        except Exception as e:
            elicitation_status = f"error ({e})"
            logger.debug(f"Elicitation failed for interactive_troubleshoot: {e}")

    # Step 3: Run diagnostic commands for selected area
    findings: list[dict] = []
    for cmd, encoding, key in _DIAG_COMMANDS[area]:
        _run_sub_op(host, [cmd], sub_ops, raw_data, key, encoding=encoding)

    # Step 4: Build area-specific findings
    if area == "bgp":
        bgp_summary = raw_data.get("bgp_summary")
        if bgp_summary:
            peers = bgp_summary[0].get("vrfs", {}).get("default", {}).get("peers", {})
            for peer_ip, peer_info in peers.items():
                state = peer_info.get("peerState", "Unknown")
                pfx = peer_info.get("prefixReceived", 0)
                if state != "Established":
                    findings.append(
                        {
                            "component": f"bgp_peer_{peer_ip}",
                            "status": state,
                            "expected": "Established",
                            "detail": f"BGP peer {peer_ip} (AS {peer_info.get('asn', '?')}) is {state}.",
                            "possible_causes": ["Peer unreachable", "Configuration mismatch"],
                            "recommended_actions": [
                                f"Run eos_diagnose_bgp_session(host='{host}', neighbor_ip='{peer_ip}')"
                            ],
                        }
                    )
                elif pfx == 0:
                    findings.append(
                        {
                            "component": f"bgp_peer_{peer_ip}",
                            "status": "Established (0 prefixes)",
                            "expected": "Established with prefixes",
                            "detail": f"Peer {peer_ip} is up but receiving 0 prefixes.",
                            "possible_causes": ["Route-map filtering", "No routes advertised"],
                            "recommended_actions": ["Check route-maps and prefix-lists"],
                        }
                    )

    elif area == "interfaces":
        intf_errors = raw_data.get("interface_errors")
        if intf_errors:
            counters = intf_errors[0].get("interfaceErrorCounters", {})
            for intf, errs in counters.items():
                total_errs = sum(v for v in errs.values() if isinstance(v, int))
                if total_errs > 100:
                    findings.append(
                        {
                            "component": f"interface_{intf}",
                            "status": f"{total_errs} errors",
                            "expected": "0 errors",
                            "detail": f"Interface {intf} has {total_errs} cumulative errors.",
                            "possible_causes": ["Cable issue", "Speed/duplex mismatch"],
                            "recommended_actions": [
                                f"Run eos_troubleshoot_interface_issues(host='{host}', interface='{intf}')"
                            ],
                        }
                    )

    elif area == "health":
        ver = raw_data.get("version")
        if ver:
            v = ver[0] if isinstance(ver, list) else ver
            uptime = v.get("uptime", 0)
            if uptime < 300:
                findings.append(
                    {
                        "component": "uptime",
                        "status": f"{uptime}s",
                        "expected": ">300s",
                        "detail": f"Device recently restarted (uptime: {uptime}s).",
                        "possible_causes": ["Recent reload", "Power event", "Crash"],
                        "recommended_actions": ["Check reload cause: 'show reload cause'"],
                    }
                )

    elif area == "config":
        diffs = raw_data.get("config_diffs")
        if diffs:
            d = diffs[0] if isinstance(diffs, list) else diffs
            output = d.get("output", "") if isinstance(d, dict) else str(d)
            if output.strip():
                findings.append(
                    {
                        "component": "running_vs_startup",
                        "status": "differences_found",
                        "expected": "identical",
                        "detail": "Running config differs from startup config.",
                        "possible_causes": ["Unsaved configuration changes"],
                        "recommended_actions": ["Review diffs and save if appropriate: 'write memory'"],
                    }
                )

    if not findings:
        findings.append(
            {
                "component": area,
                "status": "healthy",
                "expected": "healthy",
                "detail": f"No issues detected in {area} area.",
                "possible_causes": [],
                "recommended_actions": [],
            }
        )

    severity = "healthy"
    if any(f["status"] not in ("healthy", "Established") for f in findings):
        severity = "warning"
    if any(
        "unreachable" in f.get("status", "")
        or (
            "bgp_peer" in f.get("component", "")
            and f["status"] not in ("Established", "Established (0 prefixes)", "healthy")
        )
        for f in findings
    ):
        severity = "critical"

    summary = f"Interactive troubleshoot on {host} ({area}): {len(findings)} finding(s)."

    return _make_workflow_result(
        status="success",
        device=host,
        workflow="interactive_troubleshoot",
        start_time=start_time,
        severity=severity,
        summary=summary,
        findings=findings,
        raw_data={**raw_data, "area": area, "elicitation_status": elicitation_status},
        sub_operations=sub_ops,
    )
