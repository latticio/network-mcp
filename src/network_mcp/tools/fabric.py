"""Multi-device fabric-wide tools for EOS-managed networks."""

import logging
from typing import Any

from mcp.server.fastmcp import Context

from network_mcp.executor import execute_on_devices, resolve_hosts
from network_mcp.helpers import READ_ONLY, run_show_command, validate_cli_param
from network_mcp.sanitizer import config_sanitizer
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")

MAX_DEVICES = 200


def _extract_device_data(successes: dict[str, Any]) -> dict[str, Any]:
    """Extract per-device data from multi-device operation results."""
    device_data: dict[str, Any] = {}
    for host, dev_result in successes.items():
        if isinstance(dev_result, dict) and "data" in dev_result:
            device_data[host] = dev_result["data"]
        else:
            device_data[host] = dev_result
    return device_data


# --- Internal operations called by the executor ---


def _health_operation(conn_mgr_: Any, host: str) -> dict:
    """Collect health data from a single device."""
    result = run_show_command(
        conn_mgr_,
        host,
        [
            "show version",
            "show environment temperature",
            "show environment cooling",
            "show environment power",
        ],
    )
    if result["status"] == "error":
        return result

    version = result["data"][0]
    mem_total = version.get("memTotal", 0)
    mem_free = version.get("memFree", 0)
    mem_used_pct = round((mem_total - mem_free) / mem_total * 100, 1) if mem_total else 0

    return {
        "status": "success",
        "device": host,
        "data": {
            "hostname": version.get("hostname"),
            "model": version.get("modelName"),
            "eos_version": version.get("version"),
            "uptime_seconds": version.get("uptime"),
            "memory_used_percent": mem_used_pct,
            "temperature": result["data"][1].get("tempSensors", result["data"][1].get("systemStatus", {})),
            "cooling": result["data"][2].get("fanTraySlots", result["data"][2].get("powerSupplySlots", {})),
            "power": result["data"][3].get("powerSupplies", {}),
        },
    }


def _bgp_operation(conn_mgr_: Any, host: str) -> dict:
    """Collect BGP summary from a single device."""
    result = run_show_command(conn_mgr_, host, ["show ip bgp summary"])
    if result["status"] == "error":
        return result

    bgp_data = result["data"][0]
    vrfs = bgp_data.get("vrfs", {})

    established = 0
    down = 0
    peers_info = {}

    for vrf_name, vrf_data in vrfs.items():
        for peer_addr, peer_info in vrf_data.get("peers", {}).items():
            state = peer_info.get("peerState", "")
            if state == "Established":
                established += 1
            else:
                down += 1
            peers_info[peer_addr] = {
                "vrf": vrf_name,
                "state": state,
                "asn": peer_info.get("asn"),
                "prefixes_received": peer_info.get("prefixReceived", 0),
            }

    return {
        "status": "success",
        "device": host,
        "data": {
            "router_id": vrfs.get("default", {}).get("routerId"),
            "local_asn": vrfs.get("default", {}).get("asn"),
            "established": established,
            "down": down,
            "peers": peers_info,
        },
    }


def _mlag_operation(conn_mgr_: Any, host: str) -> dict:
    """Collect MLAG status from a single device."""
    result = run_show_command(conn_mgr_, host, ["show mlag"])
    if result["status"] == "error":
        return result

    mlag = result["data"][0]
    return {
        "status": "success",
        "device": host,
        "data": {
            "domain_id": mlag.get("domainId"),
            "state": mlag.get("state"),
            "neg_status": mlag.get("negStatus"),
            "peer_link": mlag.get("peerLink"),
            "peer_address": mlag.get("peerAddress"),
            "mlag_ports": mlag.get("mlagPorts", {}),
        },
    }


def _config_operation(conn_mgr_: Any, host: str, section: str | None = None) -> dict:
    """Collect running config (or section) from a single device."""
    if section:
        section_err = validate_cli_param(section, "section")
        if section_err:
            return {"status": "error", "device": host, "error": section_err}
        cmd = f"show running-config section {section}"
    else:
        cmd = "show running-config"

    result = run_show_command(conn_mgr_, host, [cmd], encoding="text")
    if result["status"] == "error":
        return result

    config_text = result["data"][0].get("output", "")
    config_text = config_sanitizer.sanitize(config_text)
    return {
        "status": "success",
        "device": host,
        "data": {"config": config_text},
    }


# --- MCP Tools ---


@mcp.tool(annotations=READ_ONLY)
def eos_list_inventory(
    role: str | None = None,
    group: str | None = None,
    tag: str | None = None,
) -> dict:
    """List devices in the inventory, optionally filtered by role, group, or tag.

    Args:
        role: Filter by device role (e.g. spine, leaf, border-leaf).
        group: Filter by group membership (e.g. dc1, pod1).
        tag: Filter by tag (e.g. production, evpn).
    """
    devices = []
    for name, creds in conn_mgr.get_inventory().items():
        if role and creds.role != role:
            continue
        if group and group not in creds.groups:
            continue
        if tag and tag not in creds.tags:
            continue
        devices.append(
            {
                "name": name,
                "host": creds.host,
                "transport": creds.transport,
                "role": creds.role,
                "groups": creds.groups,
                "tags": creds.tags,
                "mlag_peer": creds.mlag_peer,
            }
        )

    return {
        "status": "success",
        "total": len(devices),
        "filters": {
            "role": role,
            "group": group,
            "tag": tag,
        },
        "devices": devices,
    }


@mcp.tool(annotations=READ_ONLY)
async def eos_fabric_health_summary(targets: str = "all", ctx: Context | None = None) -> dict:
    """Get health summary across multiple devices: version, memory, temperature, fans, power.

    Sends progress notifications as each device completes when the client supports it.

    Args:
        targets: Devices to query — "all", "group:<name>", "tag:<value>",
                 "role:<value>", or comma-separated hostnames.
        ctx: MCP Context (injected by FastMCP) for progress notifications.
    """
    hosts = resolve_hosts(conn_mgr, targets)
    if not hosts:
        return {
            "status": "error",
            "error": f"No devices matched target '{targets}'",
        }
    if len(hosts) > MAX_DEVICES:
        return {"status": "error", "error": f"Too many devices ({len(hosts)}). Maximum is {MAX_DEVICES}."}

    result = await execute_on_devices(conn_mgr, hosts, _health_operation, ctx=ctx)
    output = result.to_response(action="fabric_health")
    output["results"] = _extract_device_data(result.successes)
    return output


@mcp.tool(annotations=READ_ONLY)
async def eos_fabric_bgp_status(targets: str = "all", ctx: Context | None = None) -> dict:
    """Get BGP status across multiple devices with aggregate established/down counts.

    Sends progress notifications as each device completes when the client supports it.

    Args:
        targets: Devices to query — "all", "group:<name>", "tag:<value>",
                 "role:<value>", or comma-separated hostnames.
        ctx: MCP Context (injected by FastMCP) for progress notifications.
    """
    hosts = resolve_hosts(conn_mgr, targets)
    if not hosts:
        return {
            "status": "error",
            "error": f"No devices matched target '{targets}'",
        }
    if len(hosts) > MAX_DEVICES:
        return {"status": "error", "error": f"Too many devices ({len(hosts)}). Maximum is {MAX_DEVICES}."}

    result = await execute_on_devices(conn_mgr, hosts, _bgp_operation, ctx=ctx)
    output = result.to_response(action="fabric_bgp_status")

    # Build per-device data and aggregate counts
    device_data = {}
    total_established = 0
    total_down = 0

    for host, dev_result in result.successes.items():
        if isinstance(dev_result, dict) and "data" in dev_result:
            data = dev_result["data"]
            device_data[host] = data
            total_established += data.get("established", 0)
            total_down += data.get("down", 0)
        else:
            device_data[host] = dev_result

    output["results"] = device_data
    output["summary"]["total_established"] = total_established
    output["summary"]["total_down"] = total_down
    return output


@mcp.tool(annotations=READ_ONLY)
async def eos_fabric_mlag_status(targets: str = "all", ctx: Context | None = None) -> dict:
    """Get MLAG status across multiple devices with aggregate health.

    Sends progress notifications as each device completes when the client supports it.

    Args:
        targets: Devices to query — "all", "group:<name>", "tag:<value>",
                 "role:<value>", or comma-separated hostnames.
        ctx: MCP Context (injected by FastMCP) for progress notifications.
    """
    hosts = resolve_hosts(conn_mgr, targets)
    if not hosts:
        return {
            "status": "error",
            "error": f"No devices matched target '{targets}'",
        }
    if len(hosts) > MAX_DEVICES:
        return {"status": "error", "error": f"Too many devices ({len(hosts)}). Maximum is {MAX_DEVICES}."}

    result = await execute_on_devices(conn_mgr, hosts, _mlag_operation, ctx=ctx)
    output = result.to_response(action="fabric_mlag_status")

    # Build per-device data and aggregate
    device_data = {}
    active_count = 0
    inactive_count = 0

    for host, dev_result in result.successes.items():
        if isinstance(dev_result, dict) and "data" in dev_result:
            data = dev_result["data"]
            device_data[host] = data
            if data.get("state") == "active":
                active_count += 1
            else:
                inactive_count += 1
        else:
            device_data[host] = dev_result

    output["results"] = device_data
    output["summary"]["active"] = active_count
    output["summary"]["inactive"] = inactive_count
    return output


@mcp.tool(annotations=READ_ONLY)
async def eos_compare_configs(devices: list[str], section: str | None = None, ctx: Context | None = None) -> dict:
    """Get running config from multiple devices for side-by-side comparison.

    Sends progress notifications as each device completes when the client supports it.

    Args:
        devices: List of device hostnames, IPs, or inventory names.
        section: Optional config section to compare (e.g. "router bgp", "interface Ethernet1").
        ctx: MCP Context (injected by FastMCP) for progress notifications.
    """
    if not devices:
        return {"status": "error", "error": "At least one device must be specified"}
    if len(devices) > MAX_DEVICES:
        return {"status": "error", "error": f"Too many devices ({len(devices)}). Maximum is {MAX_DEVICES}."}

    if section:
        section_err = validate_cli_param(section, "section")
        if section_err:
            return {"status": "error", "error": section_err}

    result = await execute_on_devices(conn_mgr, devices, _config_operation, section, ctx=ctx)
    output = result.to_response(action="compare_configs")

    # Extract config text per device
    device_configs = {}
    for host, dev_result in result.successes.items():
        if isinstance(dev_result, dict) and "data" in dev_result:
            device_configs[host] = dev_result["data"].get("config", "")
        else:
            device_configs[host] = dev_result

    output["results"] = device_configs
    if section:
        output["section"] = section
    return output


# --- MCP Tasks: Async Fabric Operations ---


@mcp.tool(annotations=READ_ONLY)
async def eos_fabric_health_async(targets: str = "all", ctx: Context | None = None) -> dict:
    """Run fabric health check as an MCP Task for large fabrics (experimental).

    When the client supports MCP Tasks, this tool returns immediately with a task
    handle while health checks run in the background. The client can poll for
    progress and results. Falls back to synchronous execution when tasks are not
    supported.

    Args:
        targets: Devices to query — "all", "group:<name>", "tag:<value>",
                 "role:<value>", or comma-separated hostnames.
        ctx: MCP Context (injected by FastMCP).
    """
    hosts = resolve_hosts(conn_mgr, targets)
    if not hosts:
        return {"status": "error", "error": f"No devices matched target '{targets}'"}
    if len(hosts) > MAX_DEVICES:
        return {"status": "error", "error": f"Too many devices ({len(hosts)}). Maximum is {MAX_DEVICES}."}

    # Check if the client supports MCP Tasks (experimental)
    use_tasks = False
    if ctx is not None:
        try:
            experimental = ctx.request_context.experimental
            if experimental is not None and hasattr(experimental, "is_task") and experimental.is_task:
                use_tasks = True
        except Exception:
            logger.debug("Could not check MCP Tasks support", exc_info=True)

    if use_tasks:
        # Run as an MCP Task for async execution
        async def _task_work(task_ctx):
            """Background task that runs health checks with progress updates."""
            from mcp.types import CallToolResult, TextContent

            await task_ctx.update_status(f"Starting health check on {len(hosts)} device(s)...")

            result = await execute_on_devices(conn_mgr, hosts, _health_operation)
            output = result.to_response(action="fabric_health_async")
            output["results"] = _extract_device_data(result.successes)

            await task_ctx.update_status(f"Completed: {result.success_count}/{result.total} devices healthy")

            import json

            result_text = json.dumps(output, indent=2, default=str)
            tool_result = CallToolResult(content=[TextContent(type="text", text=result_text)])
            await task_ctx.complete(tool_result)

        try:
            experimental = ctx.request_context.experimental  # type: ignore[union-attr]
            return await experimental.run_task(_task_work)  # type: ignore[no-any-return]
        except Exception as e:
            logger.debug("MCP Task creation failed, falling back to sync: %s", e)

    # Synchronous fallback — same logic as eos_fabric_health_summary
    result = await execute_on_devices(conn_mgr, hosts, _health_operation)
    output = result.to_response(action="fabric_health_async")
    output["results"] = _extract_device_data(result.successes)
    return output
