"""Ansible integration — playbook generation and inventory export.

Generates Ansible playbooks for common network tasks and exports
the network-mcp inventory as Ansible inventory YAML. No external
dependencies required (uses stdlib ``yaml``).
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from network_mcp.helpers import READ_ONLY
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp.ansible")

# ---------------------------------------------------------------------------
# Platform → Ansible collection mapping
# ---------------------------------------------------------------------------

_PLATFORM_COLLECTION: dict[str, str] = {
    "eos": "arista.eos",
    "iosxe": "cisco.ios",
    "nxos": "cisco.nxos",
    "junos": "junipernetworks.junos",
}

_PLATFORM_NETWORK_OS: dict[str, str] = {
    "eos": "arista.eos.eos",
    "iosxe": "cisco.ios.ios",
    "nxos": "cisco.nxos.nxos",
    "junos": "junipernetworks.junos.junos",
}

# Supported task types mapped to generator functions (populated below)
_TASK_GENERATORS: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Playbook generators
# ---------------------------------------------------------------------------


def _gen_vlan_playbook(hosts: str, platform: str) -> dict:
    """Generate a VLAN provisioning playbook."""
    collection = _PLATFORM_COLLECTION.get(platform, platform)
    return {
        "name": "Provision VLANs",
        "hosts": hosts,
        "gather_facts": False,
        "collections": [collection],
        "tasks": [
            {
                "name": "Create VLAN",
                f"{collection}.vlans": {
                    "config": [
                        {
                            "vlan_id": "{{ vlan_id }}",
                            "name": "{{ vlan_name }}",
                            "state": "active",
                        }
                    ],
                    "state": "merged",
                },
            }
        ],
    }


def _gen_bgp_playbook(hosts: str, platform: str) -> dict:
    """Generate a BGP neighbor configuration playbook."""
    collection = _PLATFORM_COLLECTION.get(platform, platform)
    return {
        "name": "Configure BGP Neighbor",
        "hosts": hosts,
        "gather_facts": False,
        "collections": [collection],
        "tasks": [
            {
                "name": "Configure BGP neighbor",
                f"{collection}.bgp_global": {
                    "config": {
                        "as_number": "{{ bgp_asn }}",
                        "neighbor": [
                            {
                                "neighbor_address": "{{ neighbor_ip }}",
                                "remote_as": "{{ remote_asn }}",
                                "description": "{{ neighbor_description | default('Configured by Ansible') }}",
                            }
                        ],
                    },
                    "state": "merged",
                },
            }
        ],
    }


def _gen_interface_playbook(hosts: str, platform: str) -> dict:
    """Generate an interface configuration playbook."""
    collection = _PLATFORM_COLLECTION.get(platform, platform)
    return {
        "name": "Configure Interface",
        "hosts": hosts,
        "gather_facts": False,
        "collections": [collection],
        "tasks": [
            {
                "name": "Configure interface settings",
                f"{collection}.interfaces": {
                    "config": [
                        {
                            "name": "{{ interface_name }}",
                            "description": "{{ interface_description | default('Configured by Ansible') }}",
                            "speed": "{{ interface_speed | default(omit) }}",
                            "enabled": "{{ interface_enabled | default(true) }}",
                        }
                    ],
                    "state": "merged",
                },
            }
        ],
    }


def _gen_backup_playbook(hosts: str, platform: str) -> dict:
    """Generate a running configuration backup playbook."""
    collection = _PLATFORM_COLLECTION.get(platform, platform)

    # Map platform to the correct cli_command module
    cli_module = f"{collection}.eos_command" if platform == "eos" else f"{collection}.cli_command"
    if platform == "junos":
        cli_module = f"{collection}.junos_command"

    return {
        "name": "Backup Running Configuration",
        "hosts": hosts,
        "gather_facts": False,
        "collections": [collection],
        "tasks": [
            {
                "name": "Gather running configuration",
                cli_module: {
                    "commands": ["show running-config"],
                },
                "register": "config_output",
            },
            {
                "name": "Save configuration to file",
                "ansible.builtin.copy": {
                    "content": "{{ config_output.stdout[0] }}",
                    "dest": "{{ backup_dir | default('./backups') }}/{{ inventory_hostname }}_config.txt",
                },
            },
        ],
    }


# Register generators
_TASK_GENERATORS = {
    "vlan_provision": _gen_vlan_playbook,
    "bgp_neighbor": _gen_bgp_playbook,
    "interface_config": _gen_interface_playbook,
    "backup_config": _gen_backup_playbook,
}


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def net_generate_playbook(
    task: str,
    hosts: str = "all",
    platform: str = "eos",
) -> dict[str, Any]:
    """Generate an Ansible playbook from a task description.

    Supported tasks: vlan_provision, bgp_neighbor, interface_config, backup_config.
    Maps platforms to Ansible collections (eos, iosxe, nxos, junos).

    Args:
        task: Task type to generate (vlan_provision, bgp_neighbor, interface_config, backup_config).
        hosts: Ansible hosts pattern (default: "all").
        platform: Target platform (eos, iosxe, nxos, junos).
    """
    generator = _TASK_GENERATORS.get(task)
    if generator is None:
        return {
            "status": "error",
            "error": f"Unknown task '{task}'. Supported tasks: {', '.join(sorted(_TASK_GENERATORS))}",
        }

    try:
        play = generator(hosts, platform)
        playbook_yaml = yaml.dump([play], default_flow_style=False, sort_keys=False)
        return {
            "status": "success",
            "task": task,
            "platform": platform,
            "hosts": hosts,
            "playbook": playbook_yaml,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"Failed to generate playbook: {e}",
        }


@mcp.tool(annotations=READ_ONLY)
def net_inventory_to_ansible() -> dict[str, Any]:
    """Export network-mcp inventory as Ansible inventory YAML.

    Reads the current device inventory and generates a valid Ansible
    inventory with hosts grouped by their configured groups. Each host
    includes ansible_host and ansible_network_os variables.
    """
    try:
        inventory = conn_mgr.get_inventory()
    except Exception as e:
        return {
            "status": "error",
            "error": f"Failed to read inventory: {e}",
        }

    if not inventory:
        return {
            "status": "error",
            "error": "No devices found in inventory",
        }

    # Build Ansible inventory structure
    all_hosts: dict[str, dict] = {}
    groups: dict[str, list[str]] = {}

    for name, creds in inventory.items():
        network_os = _PLATFORM_NETWORK_OS.get(creds.platform, creds.platform)
        all_hosts[name] = {
            "ansible_host": creds.host,
            "ansible_network_os": network_os,
        }

        # Collect group memberships
        for group in creds.groups:
            groups.setdefault(group, []).append(name)

    # Build the inventory dict
    ansible_inv: dict[str, Any] = {
        "all": {
            "hosts": all_hosts,
        },
    }

    # Add group children if any groups exist
    if groups:
        children: dict[str, dict] = {}
        for group_name, members in sorted(groups.items()):
            children[group_name] = {
                "hosts": {member: None for member in sorted(members)},
            }
        ansible_inv["all"]["children"] = children

    inventory_yaml = yaml.dump(ansible_inv, default_flow_style=False, sort_keys=False)

    return {
        "status": "success",
        "device_count": len(all_hosts),
        "group_count": len(groups),
        "inventory": inventory_yaml,
    }
