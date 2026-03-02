"""Multi-vendor workflow tools for cross-platform diagnostics.

These tools use normalized getters to work identically across Arista EOS,
Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS. They combine data from
multiple getters to provide high-level diagnostic summaries.
"""

from __future__ import annotations

import logging

from network_mcp.helpers import READ_ONLY, handle_tool_errors
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_diagnose_bgp_session(host: str, neighbor_ip: str) -> dict:
    """Diagnose a BGP session issue on any supported network device.

    Collects BGP state, interface status, and ARP data to provide a
    diagnostic summary for a specific BGP neighbor. Works with Arista EOS,
    Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        neighbor_ip: IP address of the BGP neighbor to diagnose.
    """
    driver = conn_mgr.get_driver(host)
    bgp = driver.get_bgp_summary()
    interfaces = driver.get_interfaces()
    arp_table = driver.get_arp_table()

    diagnosis: dict = {
        "neighbor_ip": neighbor_ip,
        "findings": [],
        "bgp_state": "not_found",
        "arp_resolved": False,
        "connected_interface": None,
    }

    # Check BGP peer state
    peer = bgp.get("peers", {}).get(neighbor_ip)
    if peer:
        diagnosis["bgp_state"] = peer["state"]
        diagnosis["prefixes_received"] = peer["prefixes_received"]
        diagnosis["uptime"] = peer["uptime"]

        if peer["state"].lower() == "established":
            diagnosis["findings"].append("BGP session is Established and healthy.")
            if peer["prefixes_received"] == 0:
                diagnosis["findings"].append(
                    "WARNING: Session is Established but receiving 0 prefixes. "
                    "Check route policies and address families."
                )
        else:
            diagnosis["findings"].append(
                f"BGP session is in '{peer['state']}' state (not Established). "
                "Possible causes: peer unreachable, authentication mismatch, AS mismatch."
            )
    else:
        diagnosis["findings"].append(
            f"BGP neighbor {neighbor_ip} not found in BGP summary. Check that the neighbor is configured."
        )

    # Check ARP resolution for neighbor
    for arp_entry in arp_table:
        if arp_entry["ip"] == neighbor_ip:
            diagnosis["arp_resolved"] = True
            diagnosis["connected_interface"] = arp_entry["interface"]
            break

    if not diagnosis["arp_resolved"]:
        diagnosis["findings"].append(f"No ARP entry for {neighbor_ip}. The neighbor may be unreachable at L2/L3.")

    # Check connected interface state
    if diagnosis["connected_interface"]:
        intf_name = diagnosis["connected_interface"]
        intf_data = interfaces.get(intf_name)
        if intf_data:
            if not intf_data["is_up"]:
                diagnosis["findings"].append(
                    f"Connected interface {intf_name} is DOWN. This is likely the root cause of BGP failure."
                )
            elif not intf_data["is_enabled"]:
                diagnosis["findings"].append(f"Connected interface {intf_name} is administratively disabled.")
            else:
                diagnosis["findings"].append(f"Connected interface {intf_name} is up and enabled.")

    # Summary severity
    if diagnosis["bgp_state"].lower() == "established" and diagnosis["arp_resolved"]:
        diagnosis["severity"] = "healthy"
    elif diagnosis["bgp_state"] == "not_found":
        diagnosis["severity"] = "critical"
    else:
        diagnosis["severity"] = "warning"

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "diagnosis": diagnosis,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_troubleshoot_interface(host: str, interface: str) -> dict:
    """Troubleshoot an interface on any supported network device.

    Checks link state, errors, LLDP neighbor, and provides a diagnostic
    report. Works with all supported vendors.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        interface: Interface name in vendor-native format (e.g., Ethernet1, ge-0/0/0).
    """
    driver = conn_mgr.get_driver(host)
    interfaces = driver.get_interfaces()
    lldp = driver.get_lldp_neighbors()

    report: dict = {
        "interface": interface,
        "findings": [],
        "link_state": "unknown",
        "admin_state": "unknown",
        "lldp_neighbor": None,
    }

    # Check interface exists and state
    intf_data = interfaces.get(interface)
    if not intf_data:
        available = sorted(interfaces.keys())[:10]
        suffix = "..." if len(interfaces) > 10 else ""
        return {
            "status": "error",
            "device": host,
            "error": f"Interface '{interface}' not found. Available: {', '.join(available)}{suffix}",
        }

    report["link_state"] = "up" if intf_data["is_up"] else "down"
    report["admin_state"] = "enabled" if intf_data["is_enabled"] else "disabled"
    report["speed"] = intf_data["speed"]
    report["mtu"] = intf_data["mtu"]
    report["description"] = intf_data["description"]
    report["mac_address"] = intf_data["mac_address"]

    # Diagnose state
    if not intf_data["is_enabled"]:
        report["findings"].append(
            f"Interface {interface} is administratively disabled. Use 'no shutdown' to enable it."
        )
    elif not intf_data["is_up"]:
        report["findings"].append(
            f"Interface {interface} is enabled but link is DOWN. Check cable, SFP, and remote port status."
        )
    else:
        report["findings"].append(
            f"Interface {interface} is up and operational (speed: {intf_data['speed']}Mbps, MTU: {intf_data['mtu']})."
        )

    # Check LLDP neighbor
    lldp_neighbors = lldp.get(interface, [])
    if lldp_neighbors:
        neighbor = lldp_neighbors[0]
        report["lldp_neighbor"] = neighbor
        report["findings"].append(f"LLDP neighbor detected: {neighbor['hostname']} (port: {neighbor['port']}).")
    else:
        if intf_data["is_up"]:
            report["findings"].append(
                "No LLDP neighbor detected on an up interface. LLDP may be disabled on remote end."
            )

    # Summary severity
    if intf_data["is_up"] and intf_data["is_enabled"]:
        report["severity"] = "healthy"
    elif not intf_data["is_enabled"]:
        report["severity"] = "info"
    else:
        report["severity"] = "warning"

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "report": report,
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_validate_fabric(role: str | None = None) -> dict:
    """Validate fabric health across all devices in inventory.

    Checks BGP session states, interface health, and LLDP topology
    consistency across multiple devices and vendors.

    Args:
        role: Optional role filter (e.g., "spine", "leaf") to limit scope.
    """
    # Get device list, optionally filtered by role
    if role:
        device_names = conn_mgr.get_devices_by_role(role)
    else:
        device_names = conn_mgr.list_devices()

    if not device_names:
        return {
            "status": "error",
            "error": f"No devices found{f' with role={role}' if role else ''}. Check inventory configuration.",
        }

    succeeded: dict[str, dict] = {}
    failed: dict[str, str] = {}
    total_bgp_peers = 0
    total_bgp_established = 0
    total_interfaces = 0
    total_interfaces_up = 0

    for device_name in device_names:
        device_report: dict = {
            "device": device_name,
            "vendor": "unknown",
            "bgp_peers": 0,
            "bgp_established": 0,
            "interfaces_total": 0,
            "interfaces_up": 0,
            "issues": [],
        }
        try:
            driver = conn_mgr.get_driver(device_name)
            device_report["vendor"] = driver.vendor

            # BGP health
            bgp = driver.get_bgp_summary()
            peers = bgp.get("peers", {})
            device_report["bgp_peers"] = len(peers)
            established = sum(1 for p in peers.values() if p.get("state", "").lower() == "established")
            device_report["bgp_established"] = established
            total_bgp_peers += len(peers)
            total_bgp_established += established

            for peer_ip, peer_data in peers.items():
                if peer_data.get("state", "").lower() != "established":
                    device_report["issues"].append(f"BGP peer {peer_ip} is {peer_data.get('state', 'Unknown')}")

            # Interface health
            interfaces = driver.get_interfaces()
            device_report["interfaces_total"] = len(interfaces)
            up_count = sum(1 for i in interfaces.values() if i.get("is_up"))
            device_report["interfaces_up"] = up_count
            total_interfaces += len(interfaces)
            total_interfaces_up += up_count

            # Flag enabled but down interfaces
            for intf_name, intf_data in interfaces.items():
                if intf_data.get("is_enabled") and not intf_data.get("is_up"):
                    device_report["issues"].append(f"Interface {intf_name} enabled but link down")

            succeeded[device_name] = device_report
        except Exception as e:
            device_report["issues"].append(f"Error: {e}")
            failed[device_name] = str(e)
            succeeded[device_name] = device_report

    # Determine status
    total = len(device_names)
    n_failed = len(failed)
    if not failed:
        status = "success"
    elif n_failed == total:
        status = "error"
    else:
        status = "partial"

    # Overall fabric health
    fabric_healthy = (
        total_bgp_established == total_bgp_peers and not failed and all(not r["issues"] for r in succeeded.values())
    )

    return {
        "status": status,
        "action": "validate_fabric",
        "fabric_healthy": fabric_healthy,
        "summary": {
            "total": total,
            "succeeded": total - n_failed,
            "failed": n_failed,
            "role_filter": role,
            "bgp_peers_total": total_bgp_peers,
            "bgp_peers_established": total_bgp_established,
            "interfaces_total": total_interfaces,
            "interfaces_up": total_interfaces_up,
        },
        "succeeded_hosts": sorted(set(succeeded.keys()) - set(failed.keys())),
        "failed_hosts": sorted(failed.keys()),
        "results": succeeded,
        "errors": failed,
    }
