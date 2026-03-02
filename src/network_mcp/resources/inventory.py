"""MCP Resources for device inventory and configuration data.

Includes both static resources and URI-templated resources for deep device navigation.
"""

import json

from network_mcp.helpers import run_show_command, validate_cli_param, validate_host, validate_interface_name
from network_mcp.sanitizer import config_sanitizer
from network_mcp.server import conn_mgr, mcp


@mcp.resource("net://{host}/version")
def device_version(host: str) -> str:
    """EOS version and device model information."""
    host_err = validate_host(host)
    if host_err:
        return json.dumps({"error": host_err})
    result = run_show_command(conn_mgr, host, ["show version"])
    if result["status"] == "error":
        return json.dumps({"error": result["error"]})
    return json.dumps(result["data"][0], indent=2)


@mcp.resource("net://{host}/running-config")
def running_config(host: str) -> str:
    """Full running configuration of the device (credentials sanitized)."""
    host_err = validate_host(host)
    if host_err:
        return json.dumps({"error": host_err})
    result = run_show_command(conn_mgr, host, ["show running-config"], encoding="text")
    if result["status"] == "error":
        return json.dumps({"error": result["error"]})
    config_text = result["data"][0].get("output", "")
    config_text = config_sanitizer.sanitize(config_text)
    return config_text


@mcp.resource("net://inventory")
def device_inventory() -> str:
    """List of all devices in the inventory."""
    devices = []
    for name, creds in conn_mgr.get_inventory().items():
        devices.append(
            {
                "name": name,
                "host": creds.host,
                "transport": creds.transport,
            }
        )
    return json.dumps(devices, indent=2)


@mcp.resource("net://{host}/health")
def device_health_summary(host: str) -> str:
    """Quick health summary: CPU, memory, uptime, and interface counts."""
    host_err = validate_host(host)
    if host_err:
        return json.dumps({"error": host_err})
    result = run_show_command(conn_mgr, host, ["show version", "show interfaces status"])
    if result["status"] == "error":
        return json.dumps({"error": result["error"]})

    version = result["data"][0]
    interfaces = result["data"][1].get("interfaceStatuses", {})

    mem_total = version.get("memTotal", 0)
    mem_free = version.get("memFree", 0)
    mem_used_pct = round((mem_total - mem_free) / mem_total * 100, 1) if mem_total else 0

    up_count = sum(1 for i in interfaces.values() if i.get("linkStatus") == "connected")
    down_count = sum(1 for i in interfaces.values() if i.get("linkStatus") != "connected")

    return json.dumps(
        {
            "hostname": version.get("hostname"),
            "model": version.get("modelName"),
            "version": version.get("version"),
            "uptime_seconds": version.get("uptime"),
            "memory_used_percent": mem_used_pct,
            "interfaces_up": up_count,
            "interfaces_down": down_count,
        },
        indent=2,
    )


@mcp.resource("net://{host}/interfaces/summary")
def interfaces_summary(host: str) -> str:
    """Interface summary with link status counts and error interfaces."""
    host_err = validate_host(host)
    if host_err:
        return json.dumps({"error": host_err})
    result = run_show_command(conn_mgr, host, ["show interfaces status", "show interfaces counters errors"])
    if result["status"] == "error":
        return json.dumps({"error": result["error"]})

    statuses = result["data"][0].get("interfaceStatuses", {})
    errors = result["data"][1].get("interfaceErrorCounters", {})

    # Find interfaces with non-zero errors
    error_interfaces = []
    for intf, counters in errors.items():
        total_errors = sum(v for v in counters.values() if isinstance(v, (int, float)))
        if total_errors > 0:
            error_interfaces.append({"interface": intf, "counters": counters})

    return json.dumps(
        {
            "total_interfaces": len(statuses),
            "connected": sum(1 for i in statuses.values() if i.get("linkStatus") == "connected"),
            "not_connected": sum(1 for i in statuses.values() if i.get("linkStatus") != "connected"),
            "interfaces_with_errors": error_interfaces[:20],  # Limit to avoid huge output
        },
        indent=2,
    )


# --- V3 Resource Templates (additive) ---


@mcp.resource("net://devices")
def list_devices() -> str:
    """Richer device list with roles, tags, and groups (extends net://inventory)."""
    devices = []
    for name, creds in conn_mgr.get_inventory().items():
        devices.append(
            {
                "name": name,
                "host": creds.host,
                "transport": creds.transport,
                "platform": creds.platform,
                "role": creds.role,
                "groups": creds.groups,
                "tags": creds.tags,
                "mlag_peer": creds.mlag_peer,
            }
        )
    return json.dumps(devices, indent=2)


@mcp.resource("net://{host}/running-config/{section}")
def running_config_section(host: str, section: str) -> str:
    """Per-section running configuration (e.g., 'router bgp', 'interface', 'vlan')."""
    host_err = validate_host(host)
    if host_err:
        return json.dumps({"error": host_err})
    section_err = validate_cli_param(section, "section")
    if section_err:
        return json.dumps({"error": section_err})

    result = run_show_command(conn_mgr, host, [f"show running-config section {section}"], encoding="text")
    if result["status"] == "error":
        return json.dumps({"error": result["error"]})
    config_text = result["data"][0].get("output", "")
    config_text = config_sanitizer.sanitize(config_text)
    return config_text


@mcp.resource("net://{host}/interfaces/{interface}")
def interface_detail(host: str, interface: str) -> str:
    """Single interface deep-dive: status, counters, and configuration."""
    host_err = validate_host(host)
    if host_err:
        return json.dumps({"error": host_err})
    intf_err = validate_interface_name(interface)
    if intf_err:
        return json.dumps({"error": intf_err})

    result = run_show_command(conn_mgr, host, [f"show interfaces {interface}"])
    if result["status"] == "error":
        return json.dumps({"error": result["error"]})
    interfaces = result["data"][0].get("interfaces", {})
    intf_data = interfaces.get(interface, {})
    return json.dumps(intf_data, indent=2)


@mcp.resource("net://{host}/bgp/summary")
def device_bgp_summary(host: str) -> str:
    """BGP peer summary for a device."""
    host_err = validate_host(host)
    if host_err:
        return json.dumps({"error": host_err})

    result = run_show_command(conn_mgr, host, ["show ip bgp summary"])
    if result["status"] == "error":
        return json.dumps({"error": result["error"]})
    vrfs = result["data"][0].get("vrfs", {})
    default_vrf = vrfs.get("default", {})
    return json.dumps(
        {
            "router_id": default_vrf.get("routerId"),
            "local_asn": default_vrf.get("asn"),
            "peers": default_vrf.get("peers", {}),
        },
        indent=2,
    )


@mcp.resource("net://{host}/vlans")
def device_vlans(host: str) -> str:
    """VLAN table for a device."""
    host_err = validate_host(host)
    if host_err:
        return json.dumps({"error": host_err})

    result = run_show_command(conn_mgr, host, ["show vlan"])
    if result["status"] == "error":
        return json.dumps({"error": result["error"]})
    return json.dumps(result["data"][0].get("vlans", {}), indent=2)
