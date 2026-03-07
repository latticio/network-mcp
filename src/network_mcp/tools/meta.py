"""Progressive discovery meta-tools for tool category browsing and dynamic loading.

These tools help LLMs discover relevant tools without needing all 134+ tool
definitions in context. When NET_PROGRESSIVE_DISCOVERY=true, only meta-tools
and workflow tools are registered at startup. Use net_load_tool_category()
to activate additional tool categories on demand.

When NET_PROGRESSIVE_DISCOVERY=false (default), all tools are registered at
startup and these meta-tools still work as a discovery/documentation aid.
"""

import importlib
import inspect
import logging
from typing import TYPE_CHECKING

from network_mcp.helpers import READ_ONLY
from network_mcp.server import mcp

if TYPE_CHECKING:
    from network_mcp.config import NetworkSettings

logger = logging.getLogger("network-mcp")

# --- Settings Injection (avoids repeated deferred imports from server.py) ---

_settings: "NetworkSettings | None" = None


def _init_meta_settings(settings_instance: "NetworkSettings") -> None:
    """Inject the server's settings instance for consistent access."""
    global _settings
    _settings = settings_instance


# --- Category Registry ---

# Maps category name -> {module_name, description, tool_names}
# tool_names are populated lazily from introspection

TOOL_CATEGORIES: dict[str, dict] = {
    "device": {
        "module": "device",
        "description": "Device info, health, inventory, hardware capacity, drops, TerminAttr, cache, server tools",
    },
    "interfaces": {
        "module": "interfaces",
        "description": "Interface status, detail, counters, errors, transceivers, IP interfaces, configuration",
    },
    "vlans": {
        "module": "vlans",
        "description": "VLAN listing, detail, create, delete, rename, interface assignment",
    },
    "routing": {
        "module": "routing",
        "description": "BGP summary/neighbors/routes, OSPF, route table, ARP/NDP, prefix-lists, route-maps, VRRP, VARP",
    },
    "switching": {
        "module": "switching",
        "description": "MLAG status/config-sanity/interfaces, STP, MAC table, LLDP, port-channels, trunks",
    },
    "monitoring": {
        "module": "monitoring",
        "description": "CPU, memory, temperature, power, fans/cooling, NTP, logging, reload cause",
    },
    "config": {
        "module": "config_mgmt",
        "description": "Running/startup config, diffs, push config commands, save config",
    },
    "sessions": {
        "module": "sessions",
        "description": "Config sessions: create, add commands, diff, commit (with timer), abort",
    },
    "checkpoints": {
        "module": "checkpoints",
        "description": "Config checkpoints: create, list, rollback, delete",
    },
    "troubleshoot": {
        "module": "troubleshoot",
        "description": "Ping, traceroute, interface flap detection",
    },
    "fabric": {
        "module": "fabric",
        "description": "Multi-device inventory, fabric health summary, BGP/MLAG status, config comparison",
    },
    "validation": {
        "module": "validation",
        "description": "ANTA-based validation for BGP, MLAG, interfaces, system health",
    },
    "workflows": {
        "module": "workflows",
        "description": "High-level diagnostic and change management workflows: BGP diagnosis, interface "
        "troubleshooting, fabric health, config comparison, pre/post change validation",
    },
    "export": {
        "module": "export",
        "description": "Export and utility tools: export device data as Markdown, CSV, or JSON",
    },
    "compliance": {
        "module": "compliance",
        "description": "Security compliance and audit tools",
    },
    "ai_fabric": {
        "module": "ai_fabric",
        "description": "AI/ML fabric health: RoCEv2, ECN, PFC, GPU paths, lossy/lossless classification",
    },
    "gnmi": {
        "module": "gnmi",
        "description": "gNMI telemetry and configuration: get, set, subscribe, capabilities (requires pygnmi)",
    },
    "evpn_vxlan": {
        "module": "evpn_vxlan",
        "description": "VXLAN interfaces, VTEPs, VNIs, BGP EVPN routes, config sanity (optional module)",
    },
    "security": {
        "module": "security",
        "description": "IP access-lists, AAA, RADIUS, TACACS+, CoPP, eAPI status (optional module)",
    },
    "vrf": {
        "module": "vrf",
        "description": "VRF listing and detail (optional module)",
    },
    "bfd": {
        "module": "bfd",
        "description": "BFD peer status and detail (optional module)",
    },
    "event_monitor": {
        "module": "event_monitor",
        "description": "System events, MAC move events, route change events (optional module)",
    },
    "qos": {
        "module": "qos",
        "description": "QoS policy maps, class maps, interface QoS stats (optional module)",
    },
    "common": {
        "module": "common",
        "description": "Vendor-agnostic tools that work across all supported platforms",
        "tools": [
            "net_get_device_info",
            "net_get_interfaces",
            "net_get_interface_detail",
            "net_get_bgp_summary",
            "net_get_lldp_neighbors",
            "net_diagnose_bgp_session",
            "net_troubleshoot_interface",
            "net_validate_fabric",
        ],
    },
    "cloudvision": {
        "module": "cloudvision",
        "description": "CloudVision Portal integration: device inventory and events (requires EOS_CVP_URL)",
    },
    "cisco": {
        "module": "cisco",
        "description": "Cisco NX-OS and IOS-XE specific tools (vPC, FEX)",
        "tools": ["nxos_get_vpc_status", "nxos_get_fex_status"],
    },
}

# Track which categories have been dynamically loaded
_loaded_categories: set[str] = set()

# Tool group map: populated by annotate_tools_with_groups() after all modules load.
# Maps tool_name -> category_name (x-tool-group value).
_TOOL_GROUP_MAP: dict[str, str] = {}


def _get_tools_for_module(module_name: str) -> list[dict]:
    """Introspect a tool module to get tool names and descriptions.

    Returns list of dicts with 'name', 'description', and 'parameters' keys.
    Handles both single modules (e.g., 'device') and packages (e.g., 'common').
    """
    tools: list[dict] = []
    modules_to_scan = []
    try:
        mod = importlib.import_module(f"network_mcp.tools.{module_name}")
        if hasattr(mod, "__path__"):
            # Package — scan submodules
            import pkgutil

            for _importer, sub_name, _ispkg in pkgutil.iter_modules(mod.__path__):
                try:
                    sub_mod = importlib.import_module(f"network_mcp.tools.{module_name}.{sub_name}")
                    modules_to_scan.append(sub_mod)
                except (ImportError, ModuleNotFoundError):
                    pass
        else:
            modules_to_scan.append(mod)
    except (ImportError, ModuleNotFoundError):
        return tools

    for scan_mod in modules_to_scan:
        for name, obj in inspect.getmembers(scan_mod, inspect.isfunction):
            # Tools are functions with names starting with 'eos_', 'net_', 'nxos_', or 'iosxe_'
            if name.startswith(("eos_", "net_", "nxos_", "iosxe_", "junos_")):
                doc = inspect.getdoc(obj) or ""
                # Extract first line as description
                first_line = doc.split("\n")[0] if doc else ""

                # Get parameter info
                sig = inspect.signature(obj)
                params = []
                for pname, param in sig.parameters.items():
                    p = {
                        "name": pname,
                        "type": str(param.annotation.__name__)
                        if hasattr(param.annotation, "__name__")
                        else str(param.annotation),
                    }
                    if param.default is not inspect.Parameter.empty:
                        p["default"] = str(param.default)
                    params.append(p)

                tools.append(
                    {
                        "name": name,
                        "description": first_line,
                        "parameters": params,
                    }
                )
    return tools


def _get_tool_count_for_module(module_name: str) -> int:
    """Get the number of tools in a module by checking registered tools.

    Handles both single modules and packages (scans submodules).
    """
    try:
        mod = importlib.import_module(f"network_mcp.tools.{module_name}")
        if hasattr(mod, "__path__"):
            # Package — count across submodules
            import pkgutil

            count = 0
            for _importer, sub_name, _ispkg in pkgutil.iter_modules(mod.__path__):
                try:
                    sub_mod = importlib.import_module(f"network_mcp.tools.{module_name}.{sub_name}")
                    count += sum(
                        1
                        for name, _obj in inspect.getmembers(sub_mod, inspect.isfunction)
                        if name.startswith(("eos_", "net_", "nxos_", "iosxe_", "junos_"))
                    )
                except (ImportError, ModuleNotFoundError):
                    pass
            return count
        count = sum(
            1
            for name, _obj in inspect.getmembers(mod, inspect.isfunction)
            if name.startswith(("eos_", "net_", "nxos_", "iosxe_", "junos_"))
        )
        return count
    except (ImportError, ModuleNotFoundError):
        return 0


def _build_module_to_category_map() -> dict[str, str]:
    """Build a reverse mapping from module_name -> category_name.

    Used by annotate_tools_with_groups() to determine a tool's group.
    The first category that maps to a given module wins (dict ordering).
    """
    module_to_category: dict[str, str] = {}
    for cat_name, cat_info in TOOL_CATEGORIES.items():
        module = cat_info["module"]
        if module not in module_to_category:
            module_to_category[module] = cat_name
    return module_to_category


def annotate_tools_with_groups(mcp_instance) -> dict[str, str]:
    """Set x-tool-group annotation on all registered FastMCP tools.

    Iterates the internal tool registry, determines each tool's category from its
    function's __module__ path, and writes an 'x-tool-group' key into the tool's
    ToolAnnotations.model_extra dict. ToolAnnotations uses Pydantic extra='allow'
    so arbitrary keys are supported.

    Also populates the module-level _TOOL_GROUP_MAP for programmatic access via
    get_tool_group().

    Args:
        mcp_instance: The FastMCP instance whose tool registry should be annotated.

    Returns:
        dict mapping tool_name -> category_name for every tool that was annotated.
    """
    from mcp.types import ToolAnnotations

    module_to_category = _build_module_to_category_map()
    tool_group_map: dict[str, str] = {}

    try:
        internal_tools = mcp_instance._tool_manager._tools
    except AttributeError:
        logger.warning("Cannot access FastMCP internal tool registry for x-tool-group annotation")
        return tool_group_map

    for tool_name, tool_obj in internal_tools.items():
        try:
            fn = tool_obj.fn
            module_path = fn.__module__  # e.g., "network_mcp.tools.routing"

            # Strip the "network_mcp.tools." prefix to get relative module name
            prefix = "network_mcp.tools."
            if module_path.startswith(prefix):
                rel_module = module_path[len(prefix):]  # e.g., "routing" or "common.routing"
            else:
                rel_module = module_path

            # Match: try exact match first, then fall back to parent package name
            category = module_to_category.get(rel_module)
            if category is None:
                parts = rel_module.split(".")
                if len(parts) > 1:
                    category = module_to_category.get(parts[0])

            if category is None:
                # Mark meta-tools with their own group
                if module_path == "network_mcp.tools.meta":
                    category = "meta"
                else:
                    continue  # Skip tools we cannot categorise

            tool_group_map[tool_name] = category

            # Inject x-tool-group into the tool's ToolAnnotations.
            # IMPORTANT: annotations objects (READ_ONLY, WRITE_SAFE, DESTRUCTIVE) are
            # module-level singletons shared across tools. We must create a new
            # ToolAnnotations instance — not mutate the shared one — to avoid
            # overwriting the group on all tools that reference the same object.
            if tool_obj.annotations is None:
                tool_obj.annotations = ToolAnnotations(**{"x-tool-group": category})
            else:
                existing = tool_obj.annotations
                tool_obj.annotations = ToolAnnotations(
                    title=existing.title,
                    readOnlyHint=existing.readOnlyHint,
                    destructiveHint=existing.destructiveHint,
                    idempotentHint=existing.idempotentHint,
                    openWorldHint=existing.openWorldHint,
                    **{**existing.model_extra, "x-tool-group": category},
                )

        except Exception:
            logger.debug("Could not annotate tool %s with x-tool-group", tool_name, exc_info=True)

    _TOOL_GROUP_MAP.update(tool_group_map)
    logger.info("Annotated %d tools with x-tool-group metadata", len(tool_group_map))
    return tool_group_map


def get_tool_group(tool_name: str) -> str | None:
    """Return the x-tool-group category for a registered tool, or None if not annotated."""
    return _TOOL_GROUP_MAP.get(tool_name)


# --- Meta-Tools ---


@mcp.tool(annotations=READ_ONLY)
def eos_list_tool_categories() -> dict:
    """List available tool categories and their descriptions. Start here to discover what this server can do.

    Returns categories with tool counts and descriptions. Use eos_list_tools_in_category()
    to see detailed tool information, or eos_load_tool_category() to dynamically activate
    tools in a category (when progressive discovery is enabled).

    Args: (none)
    """
    categories = {}
    for cat_name, cat_info in TOOL_CATEGORIES.items():
        count = _get_tool_count_for_module(cat_info["module"])
        categories[cat_name] = {
            "description": cat_info["description"],
            "tool_count": count,
            "loaded": cat_name in _loaded_categories or not _is_progressive_discovery(),
        }
    return {
        "status": "success",
        "categories": categories,
        "total_categories": len(categories),
        "progressive_discovery": _is_progressive_discovery(),
    }


@mcp.tool(annotations=READ_ONLY)
def net_list_tool_categories() -> dict:
    """List available tool categories with tool counts and x-tool-group metadata.

    Returns all registered tool categories, their descriptions, tool counts, and
    whether each category is currently loaded. Use net_load_tool_category() to
    activate a category when progressive discovery is enabled.

    Args: (none)
    """
    categories = {}
    for cat_name, cat_info in TOOL_CATEGORIES.items():
        count = _get_tool_count_for_module(cat_info["module"])
        categories[cat_name] = {
            "description": cat_info["description"],
            "tool_count": count,
            "loaded": cat_name in _loaded_categories or not _is_progressive_discovery(),
            "x_tool_group": cat_name,
        }
    # Also count how many tools have been annotated per group
    group_counts: dict[str, int] = {}
    for group in _TOOL_GROUP_MAP.values():
        group_counts[group] = group_counts.get(group, 0) + 1

    return {
        "status": "success",
        "categories": categories,
        "total_categories": len(categories),
        "progressive_discovery": _is_progressive_discovery(),
        "annotated_tool_counts": group_counts,
    }


@mcp.tool(annotations=READ_ONLY)
def eos_list_tools_in_category(category: str) -> dict:
    """List all tools in a specific category with their descriptions and parameters.

    Use this to discover tool names before calling them. Returns tool names,
    descriptions, and parameter details for every tool in the category.

    Args:
        category: Category name (e.g., 'routing', 'interfaces', 'device'). Use
                  eos_list_tool_categories() to see available categories.
    """
    if category not in TOOL_CATEGORIES:
        available = ", ".join(sorted(TOOL_CATEGORIES.keys()))
        return {
            "status": "error",
            "error": f"Unknown category '{category}'. Available categories: {available}",
        }

    cat_info = TOOL_CATEGORIES[category]
    tools = _get_tools_for_module(cat_info["module"])

    return {
        "status": "success",
        "category": category,
        "description": cat_info["description"],
        "tools": tools,
        "tool_count": len(tools),
        "loaded": category in _loaded_categories or not _is_progressive_discovery(),
    }


@mcp.tool(annotations=READ_ONLY)
def eos_load_tool_category(category: str) -> dict:
    """Dynamically activate all tools in a category. After calling this, the tools become
    available for use. Only effective when NET_PROGRESSIVE_DISCOVERY=true.

    When progressive discovery is disabled (default), all tools are already loaded
    and this is a no-op that returns the tool list.

    Args:
        category: Category name to load (e.g., 'routing', 'interfaces'). Use
                  eos_list_tool_categories() to see available categories.
    """
    if category not in TOOL_CATEGORIES:
        available = ", ".join(sorted(TOOL_CATEGORIES.keys()))
        return {
            "status": "error",
            "error": f"Unknown category '{category}'. Available categories: {available}",
        }

    cat_info = TOOL_CATEGORIES[category]
    module_name = cat_info["module"]

    # If not in progressive discovery mode, tools are already loaded
    if not _is_progressive_discovery():
        tools = _get_tools_for_module(module_name)
        return {
            "status": "success",
            "message": f"Category '{category}' is already loaded (progressive discovery is disabled).",
            "tools": [t["name"] for t in tools],
        }

    # Already loaded?
    if category in _loaded_categories:
        tools = _get_tools_for_module(module_name)
        return {
            "status": "success",
            "message": f"Category '{category}' already loaded.",
            "tools": [t["name"] for t in tools],
        }

    # Dynamically import the module (which registers tools via @mcp.tool decorators)
    try:
        importlib.import_module(f"network_mcp.tools.{module_name}")
    except ImportError as e:
        return {
            "status": "error",
            "error": (
                f"Failed to load category '{category}' (module '{module_name}'): {e}. "
                "This may be an optional module that requires additional dependencies."
            ),
        }

    _loaded_categories.add(category)

    # Send notification that tool list has changed
    # Note: This is best-effort — if there's no active session context, it's a no-op
    try:
        _notify_tools_changed()
    except Exception:
        logger.debug("Could not send tools/list_changed notification", exc_info=True)

    tools = _get_tools_for_module(module_name)
    tool_names = [t["name"] for t in tools]
    logger.info("Dynamically loaded category '%s' with %d tools: %s", category, len(tool_names), ", ".join(tool_names))

    return {
        "status": "success",
        "message": f"Category '{category}' loaded successfully.",
        "tools": tool_names,
        "tool_count": len(tool_names),
    }


@mcp.tool(annotations=READ_ONLY)
def net_load_tool_category(category: str) -> dict:
    """Activate all tools in a category, making them available for use.

    In progressive discovery mode (NET_PROGRESSIVE_DISCOVERY=true), dynamically
    imports the category's tool module and registers its tools. In default mode,
    all tools are already loaded and this returns the tool list.

    Args:
        category: Category name to load (e.g., 'routing', 'interfaces', 'security').
                  Use net_list_tool_categories() to see all available categories.
    """
    if category not in TOOL_CATEGORIES:
        available = ", ".join(sorted(TOOL_CATEGORIES.keys()))
        return {
            "status": "error",
            "error": f"Unknown category '{category}'. Available categories: {available}",
        }

    cat_info = TOOL_CATEGORIES[category]
    module_name = cat_info["module"]

    if not _is_progressive_discovery():
        tools = _get_tools_for_module(module_name)
        return {
            "status": "success",
            "message": f"Category '{category}' is already loaded (progressive discovery is disabled).",
            "tools": [t["name"] for t in tools],
            "tool_count": len(tools),
            "x_tool_group": category,
        }

    if category in _loaded_categories:
        tools = _get_tools_for_module(module_name)
        return {
            "status": "success",
            "message": f"Category '{category}' already loaded.",
            "tools": [t["name"] for t in tools],
            "tool_count": len(tools),
            "x_tool_group": category,
        }

    try:
        importlib.import_module(f"network_mcp.tools.{module_name}")
    except ImportError as e:
        return {
            "status": "error",
            "error": (
                f"Failed to load category '{category}' (module '{module_name}'): {e}. "
                "This may be an optional module that requires additional dependencies."
            ),
        }

    _loaded_categories.add(category)

    try:
        _notify_tools_changed()
    except Exception:
        logger.debug("Could not send tools/list_changed notification", exc_info=True)

    tools = _get_tools_for_module(module_name)
    tool_names = [t["name"] for t in tools]
    logger.info("Dynamically loaded category '%s' with %d tools: %s", category, len(tool_names), ", ".join(tool_names))

    return {
        "status": "success",
        "message": f"Category '{category}' loaded successfully.",
        "tools": tool_names,
        "tool_count": len(tool_names),
        "x_tool_group": category,
    }


def _is_progressive_discovery() -> bool:
    """Check if progressive discovery is enabled. Uses injected settings if available."""
    if _settings is not None:
        return _settings.net_progressive_discovery
    try:
        from network_mcp.server import settings

        return settings.net_progressive_discovery
    except Exception:
        return False


def _notify_tools_changed():
    """Send tools/list_changed notification to connected clients.

    This is called after dynamically loading a tool category.
    The notification triggers clients to re-fetch the tool list.

    Note: FastMCP's session-based notification requires an active request context.
    Outside a request context, we can't send notifications directly.
    Clients will see the updated tool list on their next tools/list call.
    """
    logger.info("Tool list changed — clients should re-fetch tools/list")


@mcp.tool(annotations=READ_ONLY)
def net_list_plugins() -> dict:
    """List all installed network-mcp plugins.

    Shows discovered plugins across all entry point groups: drivers,
    compliance packs, and tool modules. Useful for verifying plugin
    installation and troubleshooting.
    """
    from network_mcp.plugins import get_plugin_summary

    return get_plugin_summary()


def get_category_names() -> list[str]:
    """Return list of all category names. Used by completions."""
    return sorted(TOOL_CATEGORIES.keys())
