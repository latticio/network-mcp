"""Tests for MCP protocol integration — tool/resource/prompt registration and annotations."""

import asyncio

from network_mcp.helpers import DESTRUCTIVE, READ_ONLY, WRITE_SAFE
from network_mcp.server import mcp

# --- Annotation Constants ---


def test_read_only_constant():
    from mcp.types import ToolAnnotations

    assert isinstance(READ_ONLY, ToolAnnotations)
    assert READ_ONLY.readOnlyHint is True
    assert READ_ONLY.destructiveHint is False
    assert READ_ONLY.idempotentHint is True
    assert READ_ONLY.openWorldHint is False


def test_write_safe_constant():
    from mcp.types import ToolAnnotations

    assert isinstance(WRITE_SAFE, ToolAnnotations)
    assert WRITE_SAFE.readOnlyHint is False
    assert WRITE_SAFE.destructiveHint is False
    assert WRITE_SAFE.idempotentHint is True
    assert WRITE_SAFE.openWorldHint is True


def test_destructive_constant():
    from mcp.types import ToolAnnotations

    assert isinstance(DESTRUCTIVE, ToolAnnotations)
    assert DESTRUCTIVE.readOnlyHint is False
    assert DESTRUCTIVE.destructiveHint is True
    assert DESTRUCTIVE.idempotentHint is False
    assert DESTRUCTIVE.openWorldHint is True


# --- Tool Registration ---


def _get_tools():
    """Helper to get all registered tools from FastMCP."""
    return asyncio.run(mcp.list_tools())


def test_server_has_tools():
    """Verify tools are registered with the MCP server."""
    tools = _get_tools()
    assert len(tools) > 0


def test_expected_tool_count():
    """Verify the expected number of tools are registered (approx 72)."""
    tools = _get_tools()
    # We expect ~72 tools. Allow some tolerance for minor additions/removals.
    assert len(tools) >= 60, f"Expected at least 60 tools, got {len(tools)}"


def test_all_tools_have_descriptions():
    """Verify every registered tool has a non-empty description."""
    tools = _get_tools()
    for tool in tools:
        assert tool.description, f"Tool '{tool.name}' has no description"
        assert len(tool.description) > 10, f"Tool '{tool.name}' has a very short description"


def test_all_tools_have_annotations():
    """Verify every registered tool has tool annotations set with readOnlyHint."""
    tools = _get_tools()
    for tool in tools:
        assert tool.annotations is not None, f"Tool '{tool.name}' has no annotations"
        # ToolAnnotations uses camelCase attribute names
        assert tool.annotations.readOnlyHint is not None, f"Tool '{tool.name}' annotations missing readOnlyHint"


def test_read_only_tools_annotated_correctly():
    """Verify known read-only tools have readOnlyHint=True."""
    tools = _get_tools()
    tool_map = {t.name: t for t in tools}

    read_only_tools = [
        "eos_get_device_info",
        "eos_get_interfaces_status",
        "eos_get_vlans",
        "eos_get_bgp_summary",
        "eos_get_mlag_status",
        "eos_get_cpu_usage",
        "eos_get_running_config",
        "eos_ping_from_device",
        "eos_get_ip_access_lists",
        "eos_get_vxlan_interface",
        "eos_get_vrfs",
    ]

    for name in read_only_tools:
        assert name in tool_map, f"Expected tool '{name}' not found in registry"
        tool = tool_map[name]
        assert tool.annotations.readOnlyHint is True, f"Tool '{name}' should have readOnlyHint=True"
        assert tool.annotations.destructiveHint is False, f"Tool '{name}' should have destructiveHint=False"


def test_write_tools_annotated_correctly():
    """Verify known write tools have readOnlyHint=False."""
    tools = _get_tools()
    tool_map = {t.name: t for t in tools}

    write_tools = [
        "eos_create_vlan",
        "eos_delete_vlan",
        "eos_rename_vlan",
        "eos_assign_interface_vlan",
        "eos_configure_interface_description",
        "eos_shutdown_interface",
        "eos_no_shutdown_interface",
        "eos_push_config_commands",
        "eos_save_running_config",
    ]

    for name in write_tools:
        assert name in tool_map, f"Expected tool '{name}' not found in registry"
        tool = tool_map[name]
        assert tool.annotations.readOnlyHint is False, f"Write tool '{name}' should have readOnlyHint=False"


def test_destructive_tools_annotated_correctly():
    """Verify destructive tools have destructiveHint=True."""
    tools = _get_tools()
    tool_map = {t.name: t for t in tools}

    destructive_tools = [
        "eos_delete_vlan",
        "eos_shutdown_interface",
        "eos_push_config_commands",
    ]

    for name in destructive_tools:
        assert name in tool_map, f"Expected tool '{name}' not found in registry"
        tool = tool_map[name]
        assert tool.annotations.destructiveHint is True, f"Tool '{name}' should have destructiveHint=True"


def test_safe_write_tools_not_destructive():
    """Verify WRITE_SAFE tools have destructiveHint=False."""
    tools = _get_tools()
    tool_map = {t.name: t for t in tools}

    safe_write_tools = [
        "eos_rename_vlan",
        "eos_assign_interface_vlan",
        "eos_configure_interface_description",
        "eos_no_shutdown_interface",
        "eos_save_running_config",
    ]

    for name in safe_write_tools:
        assert name in tool_map, f"Expected tool '{name}' not found in registry"
        tool = tool_map[name]
        assert tool.annotations.destructiveHint is False, f"Safe write tool '{name}' should have destructiveHint=False"


# --- Tool Name Verification ---


def test_expected_tool_names_present():
    """Verify all expected tool names are registered."""
    tools = _get_tools()
    tool_names = {t.name for t in tools}

    expected_tools = [
        # device.py
        "eos_get_device_info",
        "eos_get_device_health",
        "eos_get_hardware_inventory",
        "eos_get_running_extensions",
        "eos_get_users",
        "eos_get_hardware_capacity",
        "eos_get_hardware_drops",
        "eos_get_terminattr_status",
        # interfaces.py
        "eos_get_interfaces_status",
        "eos_get_interface_detail",
        "eos_get_interface_counters",
        "eos_get_interface_errors",
        "eos_get_ip_interfaces",
        "eos_get_transceiver_info",
        "eos_configure_interface_description",
        "eos_shutdown_interface",
        "eos_no_shutdown_interface",
        # vlans.py
        "eos_get_vlans",
        "eos_get_vlan_detail",
        "eos_create_vlan",
        "eos_delete_vlan",
        "eos_rename_vlan",
        "eos_assign_interface_vlan",
        # routing.py
        "eos_get_bgp_summary",
        "eos_get_bgp_neighbors",
        "eos_get_bgp_routes",
        "eos_get_ospf_neighbors",
        "eos_get_ospf_interfaces",
        "eos_get_route_table",
        "eos_get_route_summary",
        "eos_get_arp_table",
        "eos_get_prefix_list",
        "eos_get_route_maps",
        "eos_get_vrrp_status",
        "eos_get_varp_status",
        # switching.py
        "eos_get_mlag_status",
        "eos_get_spanning_tree",
        "eos_get_mac_address_table",
        "eos_get_lldp_neighbors",
        "eos_get_lldp_neighbor_detail",
        "eos_get_port_channels",
        "eos_get_mlag_config_sanity",
        "eos_get_mlag_interfaces",
        "eos_get_trunk_interfaces",
        # monitoring.py
        "eos_get_cpu_usage",
        "eos_get_memory_usage",
        "eos_get_environment_temp",
        "eos_get_environment_power",
        "eos_get_environment_cooling",
        "eos_get_ntp_status",
        "eos_get_logging",
        "eos_get_reload_cause",
        # config_mgmt.py
        "eos_get_running_config",
        "eos_get_running_config_section",
        "eos_get_startup_config",
        "eos_diff_running_startup",
        "eos_push_config_commands",
        "eos_save_running_config",
        # troubleshoot.py
        "eos_ping_from_device",
        "eos_traceroute_from_device",
        "eos_check_interface_flaps",
        # security.py
        "eos_get_ip_access_lists",
        "eos_get_aaa_status",
        "eos_get_radius_status",
        "eos_get_tacacs_status",
        "eos_get_copp_policy",
        "eos_get_eapi_status",
        # evpn_vxlan.py
        "eos_get_vxlan_interface",
        "eos_get_vxlan_vtep",
        "eos_get_vxlan_vni",
        "eos_get_vxlan_address_table",
        "eos_get_vxlan_config_sanity",
        "eos_get_bgp_evpn_summary",
        "eos_get_bgp_evpn_instance",
        "eos_get_evpn_route_type_macip",
        "eos_get_evpn_route_type_imet",
        # vrf.py
        "eos_get_vrfs",
        "eos_get_vrf_detail",
        # fabric.py
        "eos_list_inventory",
        "eos_fabric_health_summary",
        "eos_fabric_bgp_status",
        "eos_fabric_mlag_status",
        "eos_compare_configs",
        # meta.py (progressive discovery)
        "eos_list_tool_categories",
        "eos_list_tools_in_category",
        "eos_load_tool_category",
    ]

    for name in expected_tools:
        assert name in tool_names, f"Expected tool '{name}' not registered"


# --- Host Parameter ---


# Tools that operate on multiple devices, inventory metadata, or server-level state
# and therefore don't require a single 'host' parameter.
_MULTI_DEVICE_TOOLS = {
    "eos_list_inventory",
    "eos_fabric_health_summary",
    "eos_fabric_bgp_status",
    "eos_fabric_mlag_status",
    "eos_compare_configs",
    "eos_compare_device_configs",
    "eos_cache_clear",
    "eos_cache_stats",
    "eos_server_health",
    "eos_server_metrics",
    "eos_validate_fabric_health",
    "eos_fabric_health_async",
    # Meta-tools (no host parameter)
    "eos_list_tool_categories",
    "eos_list_tools_in_category",
    "eos_load_tool_category",
    # Utility tools (no host parameter)
    "eos_export_report",
    # Audit export tool (no host parameter)
    "net_export_audit_log",
    # Compliance tools (no host parameter)
    "eos_list_compliance_checks",
    # Multi-vendor workflow tools (no host parameter)
    "net_validate_fabric",
    # Plugin introspection (no host parameter)
    "net_list_plugins",
    # Ansible integration tools (no host parameter)
    "net_generate_playbook",
    "net_inventory_to_ansible",
    # NetBox integration tools (no host parameter for query tools)
    "net_sync_from_netbox",
    "net_netbox_drift_check",
    "net_netbox_cable_audit",
    # Prometheus integration tools (no host parameter)
    "net_collect_prometheus_metrics",
    # Grafana dashboard generation (no host parameter)
    "net_get_grafana_dashboard",
    # Drift report (multi-device, takes hosts list)
    "net_drift_report",
}


def test_all_tools_accept_host_parameter():
    """Verify every single-device tool has 'host' as its first parameter."""
    tools = _get_tools()
    for tool in tools:
        if tool.name in _MULTI_DEVICE_TOOLS:
            continue
        schema = tool.inputSchema
        properties = schema.get("properties", {})
        assert "host" in properties, f"Tool '{tool.name}' missing 'host' parameter"


# --- Resource Registration ---


def _get_resources():
    """Helper to get all registered resources."""
    return asyncio.run(mcp.list_resources())


def _get_resource_templates():
    """Helper to get all registered resource templates."""
    return asyncio.run(mcp.list_resource_templates())


def test_server_has_resources():
    """Verify resources or resource templates are registered."""
    resources = _get_resources()
    templates = _get_resource_templates()
    total = len(resources) + len(templates)
    assert total > 0, "No resources or resource templates registered"


def test_expected_resource_count():
    """Verify the expected number of resources/templates are registered."""
    resources = _get_resources()
    templates = _get_resource_templates()
    total = len(resources) + len(templates)
    # We expect 10 resources (2 static + 8 templated) after V3 additions
    assert total >= 10, f"Expected at least 10 resources/templates, got {total}"


# --- Prompt Registration ---


def _get_prompts():
    """Helper to get all registered prompts."""
    return asyncio.run(mcp.list_prompts())


def test_server_has_prompts():
    """Verify prompts are registered with the MCP server."""
    prompts = _get_prompts()
    assert len(prompts) > 0


def test_expected_prompt_count():
    """Verify the expected number of prompts (8)."""
    prompts = _get_prompts()
    assert len(prompts) >= 8, f"Expected at least 8 prompts, got {len(prompts)}"


def test_expected_prompt_names_present():
    """Verify all expected prompt names are registered."""
    prompts = _get_prompts()
    prompt_names = {p.name for p in prompts}

    expected_prompts = [
        "troubleshoot_interface",
        "bgp_health_check",
        "pre_change_audit",
        "daily_health_check",
        "post_change_validation",
        "security_audit",
        "mlag_troubleshoot",
        "evpn_vxlan_health_check",
        "safe_config_change",
    ]

    for name in expected_prompts:
        assert name in prompt_names, f"Expected prompt '{name}' not registered"


def test_all_prompts_have_descriptions():
    """Verify every prompt has a description."""
    prompts = _get_prompts()
    for prompt in prompts:
        assert prompt.description, f"Prompt '{prompt.name}' has no description"
