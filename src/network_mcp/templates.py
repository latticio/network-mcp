"""Vendor-agnostic config template engine.

Maps intent-based operations (e.g. 'create_vlan', 'set_interface_description')
to vendor-specific CLI commands for all supported platforms (eos, iosxe, nxos, junos).

Uses simple Python string formatting — no Jinja2 or external template dependency.

Usage::

    from network_mcp.templates import render_commands, list_intents

    # Get vendor-specific commands for a VLAN create
    commands = render_commands("create_vlan", {"vlan_id": 100, "name": "SERVERS"}, "eos")
    # => ["vlan 100", "name SERVERS"]

    commands = render_commands("create_vlan", {"vlan_id": 100, "name": "SERVERS"}, "junos")
    # => ["set vlans SERVERS vlan-id 100"]
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("network-mcp")

SUPPORTED_PLATFORMS = frozenset({"eos", "iosxe", "nxos", "junos"})


class TemplateError(Exception):
    """Raised when template rendering fails (unknown intent, missing params, unsupported platform)."""


class ConfigTemplate:
    """A single intent template that maps parameters to vendor-specific command lists.

    Each platform's renderer is a callable that takes a params dict and returns
    a list of CLI command strings.
    """

    __slots__ = ("intent", "description", "required_params", "optional_params", "_renderers")

    def __init__(
        self,
        intent: str,
        description: str,
        required_params: list[str],
        optional_params: list[str] | None = None,
    ) -> None:
        self.intent = intent
        self.description = description
        self.required_params = required_params
        self.optional_params = optional_params or []
        self._renderers: dict[str, Any] = {}

    def register(self, platform: str, renderer: Any) -> ConfigTemplate:
        """Register a renderer function for a platform. Returns self for chaining."""
        if platform not in SUPPORTED_PLATFORMS:
            raise TemplateError(f"Unknown platform '{platform}'. Supported: {sorted(SUPPORTED_PLATFORMS)}")
        self._renderers[platform] = renderer
        return self

    def render(self, params: dict[str, Any], platform: str) -> list[str]:
        """Render vendor-specific commands for the given platform and parameters."""
        if platform not in SUPPORTED_PLATFORMS:
            raise TemplateError(f"Unsupported platform '{platform}'. Supported: {sorted(SUPPORTED_PLATFORMS)}")
        if platform not in self._renderers:
            raise TemplateError(f"Intent '{self.intent}' has no template for platform '{platform}'")

        missing = [p for p in self.required_params if p not in params]
        if missing:
            raise TemplateError(f"Missing required parameters for '{self.intent}': {', '.join(missing)}")

        return self._renderers[platform](params)


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ConfigTemplate] = {}


def _register(template: ConfigTemplate) -> ConfigTemplate:
    """Add a template to the global registry."""
    _REGISTRY[template.intent] = template
    return template


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_commands(intent: str, params: dict[str, Any], platform: str) -> list[str]:
    """Render vendor-specific CLI commands for a given intent.

    Args:
        intent: The configuration intent name (e.g. 'create_vlan').
        params: Parameter dict required by the intent template.
        platform: Target platform ('eos', 'iosxe', 'nxos', 'junos').

    Returns:
        List of CLI command strings ready for ``run_config_command`` or ``driver.run_config()``.

    Raises:
        TemplateError: If the intent is unknown, platform unsupported, or required params missing.
    """
    template = _REGISTRY.get(intent)
    if template is None:
        raise TemplateError(f"Unknown intent '{intent}'. Available: {sorted(_REGISTRY.keys())}")
    return template.render(params, platform)


def list_intents() -> list[dict[str, Any]]:
    """Return metadata for all registered intents.

    Returns:
        List of dicts with keys: intent, description, required_params, optional_params, platforms.
    """
    return [
        {
            "intent": t.intent,
            "description": t.description,
            "required_params": t.required_params,
            "optional_params": t.optional_params,
            "platforms": sorted(t._renderers.keys()),
        }
        for t in sorted(_REGISTRY.values(), key=lambda t: t.intent)
    ]


def get_intent(intent: str) -> ConfigTemplate | None:
    """Return the ConfigTemplate for a given intent name, or None."""
    return _REGISTRY.get(intent)


# ---------------------------------------------------------------------------
# Helper: CIDR prefix-length to dotted-decimal mask (for IOS-XE)
# ---------------------------------------------------------------------------


def _cidr_to_mask(prefix_len: int) -> str:
    """Convert a CIDR prefix length to a dotted-decimal subnet mask."""
    bits = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return f"{(bits >> 24) & 0xFF}.{(bits >> 16) & 0xFF}.{(bits >> 8) & 0xFF}.{bits & 0xFF}"


# ===================================================================
# Intent templates
# ===================================================================

# --- create_vlan ---

_register(
    ConfigTemplate(
        intent="create_vlan",
        description="Create a VLAN with an ID and name",
        required_params=["vlan_id", "name"],
    )
    .register("eos", lambda p: [f"vlan {p['vlan_id']}", f"name {p['name']}"])
    .register("iosxe", lambda p: [f"vlan {p['vlan_id']}", f"name {p['name']}"])
    .register("nxos", lambda p: [f"vlan {p['vlan_id']}", f"name {p['name']}"])
    .register("junos", lambda p: [f"set vlans {p['name']} vlan-id {p['vlan_id']}"])
)

# --- delete_vlan ---

_register(
    ConfigTemplate(
        intent="delete_vlan",
        description="Delete a VLAN by ID",
        required_params=["vlan_id"],
    )
    .register("eos", lambda p: [f"no vlan {p['vlan_id']}"])
    .register("iosxe", lambda p: [f"no vlan {p['vlan_id']}"])
    .register("nxos", lambda p: [f"no vlan {p['vlan_id']}"])
    .register("junos", lambda p: [f"delete vlans vlan-id {p['vlan_id']}"])
)

# --- rename_vlan ---

_register(
    ConfigTemplate(
        intent="rename_vlan",
        description="Rename an existing VLAN",
        required_params=["vlan_id", "name"],
    )
    .register("eos", lambda p: [f"vlan {p['vlan_id']}", f"name {p['name']}"])
    .register("iosxe", lambda p: [f"vlan {p['vlan_id']}", f"name {p['name']}"])
    .register("nxos", lambda p: [f"vlan {p['vlan_id']}", f"name {p['name']}"])
    .register("junos", lambda p: [f"set vlans {p['name']} vlan-id {p['vlan_id']}"])
)

# --- assign_interface_vlan ---

_register(
    ConfigTemplate(
        intent="assign_interface_vlan",
        description="Assign an access VLAN to an interface",
        required_params=["interface", "vlan_id"],
    )
    .register(
        "eos",
        lambda p: [f"interface {p['interface']}", "switchport mode access", f"switchport access vlan {p['vlan_id']}"],
    )
    .register(
        "iosxe",
        lambda p: [f"interface {p['interface']}", "switchport mode access", f"switchport access vlan {p['vlan_id']}"],
    )
    .register(
        "nxos",
        lambda p: [f"interface {p['interface']}", "switchport mode access", f"switchport access vlan {p['vlan_id']}"],
    )
    .register(
        "junos",
        lambda p: [
            f"set interfaces {p['interface']} unit 0 family ethernet-switching vlan members vlan{p['vlan_id']}"
        ],
    )
)

# --- set_interface_description ---

_register(
    ConfigTemplate(
        intent="set_interface_description",
        description="Set the description on an interface",
        required_params=["interface", "description"],
    )
    .register("eos", lambda p: [f"interface {p['interface']}", f"description {p['description']}"])
    .register("iosxe", lambda p: [f"interface {p['interface']}", f"description {p['description']}"])
    .register("nxos", lambda p: [f"interface {p['interface']}", f"description {p['description']}"])
    .register("junos", lambda p: [f"set interfaces {p['interface']} description \"{p['description']}\""])
)

# --- shutdown_interface ---

_register(
    ConfigTemplate(
        intent="shutdown_interface",
        description="Administratively shut down an interface",
        required_params=["interface"],
    )
    .register("eos", lambda p: [f"interface {p['interface']}", "shutdown"])
    .register("iosxe", lambda p: [f"interface {p['interface']}", "shutdown"])
    .register("nxos", lambda p: [f"interface {p['interface']}", "shutdown"])
    .register("junos", lambda p: [f"set interfaces {p['interface']} disable"])
)

# --- enable_interface ---

_register(
    ConfigTemplate(
        intent="enable_interface",
        description="Administratively enable (no shutdown) an interface",
        required_params=["interface"],
    )
    .register("eos", lambda p: [f"interface {p['interface']}", "no shutdown"])
    .register("iosxe", lambda p: [f"interface {p['interface']}", "no shutdown"])
    .register("nxos", lambda p: [f"interface {p['interface']}", "no shutdown"])
    .register("junos", lambda p: [f"delete interfaces {p['interface']} disable"])
)


# --- add_static_route ---


def _static_route_junos(p: dict[str, Any]) -> list[str]:
    vrf = p.get("vrf", "default")
    if vrf != "default":
        cmd = f"set routing-instances {vrf} routing-options static route {p['prefix']} next-hop {p['next_hop']}"
    else:
        cmd = f"set routing-options static route {p['prefix']} next-hop {p['next_hop']}"
    ad = p.get("admin_distance", 1)
    if ad != 1:
        cmd += f" preference {ad}"
    return [cmd]


def _static_route_iosxe(p: dict[str, Any]) -> list[str]:
    parts = p["prefix"].split("/")
    network = parts[0]
    mask = _cidr_to_mask(int(parts[1]))
    vrf = p.get("vrf", "default")
    if vrf != "default":
        cmd = f"ip route vrf {vrf} {network} {mask} {p['next_hop']}"
    else:
        cmd = f"ip route {network} {mask} {p['next_hop']}"
    ad = p.get("admin_distance", 1)
    if ad != 1:
        cmd += f" {ad}"
    return [cmd]


def _static_route_eos_nxos(p: dict[str, Any]) -> list[str]:
    vrf = p.get("vrf", "default")
    if vrf != "default":
        cmd = f"ip route vrf {vrf} {p['prefix']} {p['next_hop']}"
    else:
        cmd = f"ip route {p['prefix']} {p['next_hop']}"
    ad = p.get("admin_distance", 1)
    if ad != 1:
        cmd += f" {ad}"
    return [cmd]


_register(
    ConfigTemplate(
        intent="add_static_route",
        description="Add an IPv4 static route",
        required_params=["prefix", "next_hop"],
        optional_params=["admin_distance", "vrf"],
    )
    .register("eos", _static_route_eos_nxos)
    .register("iosxe", _static_route_iosxe)
    .register("nxos", _static_route_eos_nxos)
    .register("junos", _static_route_junos)
)


# --- add_bgp_neighbor ---


def _bgp_neighbor_junos(p: dict[str, Any]) -> list[str]:
    group = f"AS{p['remote_as']}"
    cmds = [f"set protocols bgp group {group} neighbor {p['neighbor']} peer-as {p['remote_as']}"]
    desc = p.get("description", "")
    if desc:
        cmds.append(f'set protocols bgp group {group} neighbor {p["neighbor"]} description "{desc}"')
    return cmds


def _bgp_neighbor_nxos(p: dict[str, Any]) -> list[str]:
    cmds = [f"router bgp {p['local_as']}", f"neighbor {p['neighbor']}", f"remote-as {p['remote_as']}"]
    desc = p.get("description", "")
    if desc:
        cmds.append(f"description {desc}")
    return cmds


def _bgp_neighbor_eos_iosxe(p: dict[str, Any]) -> list[str]:
    cmds = [f"router bgp {p['local_as']}", f"neighbor {p['neighbor']} remote-as {p['remote_as']}"]
    desc = p.get("description", "")
    if desc:
        cmds.append(f"neighbor {p['neighbor']} description {desc}")
    return cmds


_register(
    ConfigTemplate(
        intent="add_bgp_neighbor",
        description="Add a BGP neighbor/peer",
        required_params=["local_as", "neighbor", "remote_as"],
        optional_params=["description"],
    )
    .register("eos", _bgp_neighbor_eos_iosxe)
    .register("iosxe", _bgp_neighbor_eos_iosxe)
    .register("nxos", _bgp_neighbor_nxos)
    .register("junos", _bgp_neighbor_junos)
)

# --- set_hostname ---

_register(
    ConfigTemplate(
        intent="set_hostname",
        description="Set the device hostname",
        required_params=["hostname"],
    )
    .register("eos", lambda p: [f"hostname {p['hostname']}"])
    .register("iosxe", lambda p: [f"hostname {p['hostname']}"])
    .register("nxos", lambda p: [f"hostname {p['hostname']}"])
    .register("junos", lambda p: [f"set system host-name {p['hostname']}"])
)

# --- set_ntp_server ---

_register(
    ConfigTemplate(
        intent="set_ntp_server",
        description="Configure an NTP server",
        required_params=["server"],
    )
    .register("eos", lambda p: [f"ntp server {p['server']}"])
    .register("iosxe", lambda p: [f"ntp server {p['server']}"])
    .register("nxos", lambda p: [f"ntp server {p['server']}"])
    .register("junos", lambda p: [f"set system ntp server {p['server']}"])
)

# --- set_dns_server ---

_register(
    ConfigTemplate(
        intent="set_dns_server",
        description="Configure a DNS name-server",
        required_params=["server"],
    )
    .register("eos", lambda p: [f"ip name-server {p['server']}"])
    .register("iosxe", lambda p: [f"ip name-server {p['server']}"])
    .register("nxos", lambda p: [f"ip name-server {p['server']}"])
    .register("junos", lambda p: [f"set system name-server {p['server']}"])
)

# --- set_banner ---

_register(
    ConfigTemplate(
        intent="set_banner",
        description="Set the login banner (MOTD)",
        required_params=["message"],
    )
    .register("eos", lambda p: [f"banner motd\n{p['message']}\nEOF"])
    .register("iosxe", lambda p: [f"banner motd ^{p['message']}^"])
    .register("nxos", lambda p: [f"banner motd @{p['message']}@"])
    .register("junos", lambda p: [f'set system login message "{p["message"]}"'])
)

# --- set_interface_mtu ---

_register(
    ConfigTemplate(
        intent="set_interface_mtu",
        description="Set the MTU on an interface",
        required_params=["interface", "mtu"],
    )
    .register("eos", lambda p: [f"interface {p['interface']}", f"mtu {p['mtu']}"])
    .register("iosxe", lambda p: [f"interface {p['interface']}", f"mtu {p['mtu']}"])
    .register("nxos", lambda p: [f"interface {p['interface']}", f"mtu {p['mtu']}"])
    .register("junos", lambda p: [f"set interfaces {p['interface']} mtu {p['mtu']}"])
)

# --- set_interface_ip ---

_register(
    ConfigTemplate(
        intent="set_interface_ip",
        description="Assign an IP address to an interface",
        required_params=["interface", "ip_address"],
    )
    .register("eos", lambda p: [f"interface {p['interface']}", f"ip address {p['ip_address']}"])
    .register("iosxe", lambda p: [f"interface {p['interface']}", f"ip address {p['ip_address']}"])
    .register("nxos", lambda p: [f"interface {p['interface']}", "no switchport", f"ip address {p['ip_address']}"])
    .register("junos", lambda p: [f"set interfaces {p['interface']} unit 0 family inet address {p['ip_address']}"])
)

# --- set_snmp_community ---

_register(
    ConfigTemplate(
        intent="set_snmp_community",
        description="Configure an SNMP community string",
        required_params=["community", "access"],
    )
    .register("eos", lambda p: [f"snmp-server community {p['community']} {p['access']}"])
    .register("iosxe", lambda p: [f"snmp-server community {p['community']} {p['access'].upper()}"])
    .register("nxos", lambda p: [f"snmp-server community {p['community']} {p['access']}"])
    .register("junos", lambda p: [f"set snmp community {p['community']} authorization {p['access']}-only"])
)

# --- set_logging_server ---

_register(
    ConfigTemplate(
        intent="set_logging_server",
        description="Configure a remote syslog server",
        required_params=["server"],
    )
    .register("eos", lambda p: [f"logging host {p['server']}"])
    .register("iosxe", lambda p: [f"logging host {p['server']}"])
    .register("nxos", lambda p: [f"logging server {p['server']}"])
    .register("junos", lambda p: [f"set system syslog host {p['server']} any any"])
)
