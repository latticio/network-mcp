"""Mock responses for integration tools in demo mode.

Provides realistic sample output for NetBox, ServiceNow, Prometheus,
and Ansible integration tools when NET_DEMO_MODE=true so that users
can evaluate the full platform without external service dependencies.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# NetBox mock responses
# ---------------------------------------------------------------------------

NETBOX_SYNC_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "synced_count": 6,
    "devices": [
        {
            "name": "spine-01",
            "host": "10.0.0.1",
            "platform": "eos",
            "role": "spine",
            "site": "dc1",
            "serial": "SSJ17115308",
            "tags": ["production", "spine"],
        },
        {
            "name": "spine-02",
            "host": "10.0.0.2",
            "platform": "eos",
            "role": "spine",
            "site": "dc1",
            "serial": "SSJ17115309",
            "tags": ["production", "spine"],
        },
        {
            "name": "leaf-01",
            "host": "10.0.1.1",
            "platform": "eos",
            "role": "leaf",
            "site": "dc1",
            "serial": "SSJ18200100",
            "tags": ["production", "leaf", "mlag"],
        },
        {
            "name": "leaf-02",
            "host": "10.0.1.2",
            "platform": "eos",
            "role": "leaf",
            "site": "dc1",
            "serial": "SSJ18200101",
            "tags": ["production", "leaf", "mlag"],
        },
        {
            "name": "leaf-03",
            "host": "10.0.2.1",
            "platform": "eos",
            "role": "leaf",
            "site": "dc1",
            "serial": "SSJ19300200",
            "tags": ["production", "leaf", "mlag"],
        },
        {
            "name": "leaf-04",
            "host": "10.0.2.2",
            "platform": "eos",
            "role": "leaf",
            "site": "dc1",
            "serial": "SSJ19300201",
            "tags": ["production", "leaf", "mlag"],
        },
    ],
}

NETBOX_PUSH_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "action": "updated",
    "netbox_id": 42,
    "updated_fields": {"serial": "SSJ17115308"},
}

NETBOX_DRIFT_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "total_devices": 6,
    "devices_checked": 6,
    "drift_count": 1,
    "drifts": [
        {
            "device": "leaf-03",
            "severity": "warning",
            "field": "serial",
            "netbox": "SSJ19300199",
            "live": "SSJ19300200",
        },
    ],
}

NETBOX_CABLE_AUDIT_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "cables_checked": 16,
    "mismatch_count": 0,
    "mismatches": [],
}

# ---------------------------------------------------------------------------
# ServiceNow mock responses
# ---------------------------------------------------------------------------

SERVICENOW_CREATE_INCIDENT_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "incident_number": "INC0010042",
    "sys_id": "a1b2c3d4e5f6g7h8i9j0",
    "state": "New",
    "urgency": 2,
}

SERVICENOW_SYNC_CMDB_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "cmdb_sys_id": "cmdb0001a2b3c4d5e6f7",
    "action": "updated",
}

SERVICENOW_UPDATE_INCIDENT_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "incident_number": "INC0010042",
    "sys_id": "a1b2c3d4e5f6g7h8i9j0",
    "state": "2",
}

SERVICENOW_VALIDATE_CHANGE_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "phase": "pre",
    "change_sys_id": "chg0001a2b3c4d5e6f7",
    "snapshot": {
        "show version": {
            "modelName": "DCS-7280SR3-48YC8",
            "version": "4.32.1F",
        },
        "show ip bgp summary": {
            "vrfs": {
                "default": {
                    "peers": {
                        "10.0.0.2": {"peerState": "Established", "prefixReceived": 24},
                    }
                }
            }
        },
    },
}

# ---------------------------------------------------------------------------
# Prometheus mock responses
# ---------------------------------------------------------------------------

PROMETHEUS_METRICS_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "hosts_collected": 6,
    "hosts_with_errors": 0,
    "errors": None,
    "prometheus_text": (
        "# HELP network_device_cpu_percent Device CPU utilization percentage\n"
        "# TYPE network_device_cpu_percent gauge\n"
        'network_device_cpu_percent{host="spine-01",platform="eos"} 12.5\n'
        'network_device_cpu_percent{host="spine-02",platform="eos"} 14.2\n'
        'network_device_cpu_percent{host="leaf-01",platform="eos"} 8.1\n'
        'network_device_cpu_percent{host="leaf-02",platform="eos"} 7.9\n'
        'network_device_cpu_percent{host="leaf-03",platform="eos"} 9.3\n'
        'network_device_cpu_percent{host="leaf-04",platform="eos"} 10.1\n'
        "# HELP network_device_memory_percent Device memory utilization percentage\n"
        "# TYPE network_device_memory_percent gauge\n"
        'network_device_memory_percent{host="spine-01",platform="eos"} 33.5\n'
        'network_device_memory_percent{host="spine-02",platform="eos"} 33.9\n'
        'network_device_memory_percent{host="leaf-01",platform="eos"} 28.4\n'
        'network_device_memory_percent{host="leaf-02",platform="eos"} 29.1\n'
        'network_device_memory_percent{host="leaf-03",platform="eos"} 27.8\n'
        'network_device_memory_percent{host="leaf-04",platform="eos"} 28.3\n'
        "# HELP network_bgp_peers_established Number of established BGP peers\n"
        "# TYPE network_bgp_peers_established gauge\n"
        'network_bgp_peers_established{host="spine-01",vrf="default"} 4\n'
        'network_bgp_peers_established{host="spine-02",vrf="default"} 4\n'
        'network_bgp_peers_established{host="leaf-01",vrf="default"} 2\n'
        'network_bgp_peers_established{host="leaf-02",vrf="default"} 2\n'
        'network_bgp_peers_established{host="leaf-03",vrf="default"} 2\n'
        'network_bgp_peers_established{host="leaf-04",vrf="default"} 2\n'
        "# HELP network_device_uptime_seconds Device uptime in seconds\n"
        "# TYPE network_device_uptime_seconds gauge\n"
        'network_device_uptime_seconds{host="spine-01",platform="eos"} 8640000\n'
        'network_device_uptime_seconds{host="spine-02",platform="eos"} 8640000\n'
        'network_device_uptime_seconds{host="leaf-01",platform="eos"} 5184000\n'
        'network_device_uptime_seconds{host="leaf-02",platform="eos"} 5184000\n'
        'network_device_uptime_seconds{host="leaf-03",platform="eos"} 2592000\n'
        'network_device_uptime_seconds{host="leaf-04",platform="eos"} 2592000\n'
    ),
}

# ---------------------------------------------------------------------------
# Ansible mock responses
# ---------------------------------------------------------------------------

ANSIBLE_PLAYBOOK_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "task": "vlan_provision",
    "platform": "eos",
    "hosts": "all",
    "playbook": (
        "- name: Provision VLANs\n"
        "  hosts: all\n"
        "  gather_facts: false\n"
        "  collections:\n"
        "  - arista.eos\n"
        "  tasks:\n"
        "  - name: Create VLAN\n"
        "    arista.eos.vlans:\n"
        "      config:\n"
        "      - vlan_id: '{{ vlan_id }}'\n"
        "        name: '{{ vlan_name }}'\n"
        "        state: active\n"
        "      state: merged\n"
    ),
}

ANSIBLE_INVENTORY_RESPONSE: dict = {
    "status": "success",
    "demo_mode": True,
    "device_count": 6,
    "group_count": 4,
    "inventory": (
        "all:\n"
        "  hosts:\n"
        "    spine-01:\n"
        "      ansible_host: 10.0.0.1\n"
        "      ansible_network_os: arista.eos.eos\n"
        "    spine-02:\n"
        "      ansible_host: 10.0.0.2\n"
        "      ansible_network_os: arista.eos.eos\n"
        "    leaf-01:\n"
        "      ansible_host: 10.0.1.1\n"
        "      ansible_network_os: arista.eos.eos\n"
        "    leaf-02:\n"
        "      ansible_host: 10.0.1.2\n"
        "      ansible_network_os: arista.eos.eos\n"
        "    leaf-03:\n"
        "      ansible_host: 10.0.2.1\n"
        "      ansible_network_os: arista.eos.eos\n"
        "    leaf-04:\n"
        "      ansible_host: 10.0.2.2\n"
        "      ansible_network_os: arista.eos.eos\n"
        "  children:\n"
        "    dc1:\n"
        "      hosts:\n"
        "        spine-01:\n"
        "        spine-02:\n"
        "        leaf-01:\n"
        "        leaf-02:\n"
        "        leaf-03:\n"
        "        leaf-04:\n"
        "    spines:\n"
        "      hosts:\n"
        "        spine-01:\n"
        "        spine-02:\n"
        "    leaves:\n"
        "      hosts:\n"
        "        leaf-01:\n"
        "        leaf-02:\n"
        "        leaf-03:\n"
        "        leaf-04:\n"
        "    rack-01:\n"
        "      hosts:\n"
        "        leaf-01:\n"
        "        leaf-02:\n"
    ),
}
