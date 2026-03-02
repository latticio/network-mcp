# Tool Reference

> Auto-generated tool reference for network-mcp v6.0.0.

**239 tools** | **10 resources** | **12 prompts**

## Tool Namespaces

Tools are organized into five namespaces based on vendor scope:

| Prefix | Scope | Description |
|--------|-------|-------------|
| `net_*` | **Vendor-agnostic** | Works with any supported platform (EOS, IOS-XE, NX-OS, JunOS). Uses the `NetworkDriver` protocol to auto-select the correct driver based on the device's `platform` field in inventory. |
| `eos_*` | **Arista EOS** | EOS-optimized tools using pyeapi/eAPI. These provide the deepest feature coverage for Arista devices (EVPN/VXLAN, MLAG, config sessions, ANTA validation, etc.). |
| `nxos_*` | **Cisco NX-OS** | NX-OS-specific tools for features unique to Nexus platforms (vPC, FEX, feature list, system resources). |
| `iosxe_*` | **Cisco IOS-XE** | IOS-XE-specific tools for Cisco routers and switches (RESTCONF, platform info, SD-WAN). |
| `junos_*` | **Juniper JunOS** | JunOS-specific tools for Juniper devices (routing instances, firewall filters, commit history). |

**Which to use?** Start with `net_*` tools for multi-vendor environments. Use vendor-specific tools (`eos_*`, `nxos_*`, `iosxe_*`, `junos_*`) when you need platform-specific features or deeper integration.

## Vendor Support Matrix

| Feature Area | `net_*` | `eos_*` | `nxos_*` | `iosxe_*` | `junos_*` |
|-------------|---------|---------|----------|-----------|-----------|
| Device Info | Yes | Yes | Yes | Yes | Yes |
| Interfaces | Yes | Yes | Yes | Yes | Yes |
| BGP | Yes | Yes | Yes | Yes | Yes |
| OSPF | Yes | Yes | -- | -- | -- |
| VLANs | Yes | Yes | -- | -- | -- |
| MLAG / vPC | -- | Yes | Yes | -- | -- |
| EVPN/VXLAN | -- | Yes | -- | -- | -- |
| Config Sessions | -- | Yes | -- | -- | -- |
| ANTA Validation | -- | Yes | -- | -- | -- |
| gNMI | -- | Yes | -- | -- | -- |
| Compliance | Yes | Yes | Yes | Yes | -- |
| Firewall Filters | -- | -- | -- | -- | Yes |
| Commit History | -- | -- | -- | -- | Yes |
| SD-WAN | -- | -- | -- | Yes | -- |
| FEX | -- | -- | Yes | -- | -- |

## Tool Access Levels

| Level | Badge | Description |
|-------|-------|-------------|
| Read | `Read` | Read-only; does not modify device state |
| Write | `Write` | Modifies configuration; requires `NET_READ_ONLY=false` |
| Destructive | `Destructive` | May cause service impact; use with caution |

## Contents

- [Vendor-Agnostic Tools (`net_*`)](#vendor-agnostic-tools-net)
  - [Device](#net-device)
  - [Interfaces](#net-interfaces)
  - [Routing](#net-routing)
  - [Switching](#net-switching)
  - [VLANs](#net-vlans)
  - [Configuration](#net-configuration)
  - [VRF](#net-vrf)
  - [Security](#net-security)
  - [Monitoring](#net-monitoring)
  - [Troubleshooting](#net-troubleshooting)
  - [Workflows](#net-workflows)
  - [Change Management](#net-change-management)
  - [Drift Detection](#net-drift-detection)
- [Arista EOS Tools (`eos_*`)](#arista-eos-tools-eos)
- [Cisco NX-OS Tools (`nxos_*`)](#cisco-nx-os-tools-nxos)
- [Cisco IOS-XE Tools (`iosxe_*`)](#cisco-ios-xe-tools-iosxe)
- [Juniper JunOS Tools (`junos_*`)](#juniper-junos-tools-junos)
- [Integration Tools](#integration-tools)
- [Meta / Discovery](#meta--discovery)
- [Resources](#resources)
- [Prompts](#prompts)
- [OpenAPI Specification](#openapi-specification)
- [Deprecation Policy](#deprecation-policy)

---

## Vendor-Agnostic Tools (`net_*`)

Modules: `tools/common/` — 63 tools across 14 modules

These tools work with any platform implementing the `NetworkDriver` protocol (EOS, IOS-XE, NX-OS, JunOS). They automatically select the correct driver based on the device's `platform` field in the inventory.

### <a id="net-device"></a>Device

Module: `tools/common/device.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_device_info` | Get device information from any supported network device. | `host: str` | Read |

### <a id="net-interfaces"></a>Interfaces

Module: `tools/common/interfaces.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_interfaces` | Get interface status from any supported network device. | `host: str`, `fields: list[str] \| None` | Read |
| `net_get_interface_detail` | Get detailed information for a specific interface. | `host: str`, `interface: str` | Read |
| `net_get_interface_counters` | Get traffic counters for all interfaces. | `host: str`, `page: int` = 1, `page_size: int` = 50 | Read |
| `net_get_interface_errors` | Get error counters for all interfaces. | `host: str`, `page: int` = 1, `page_size: int` = 50 | Read |
| `net_get_ip_interfaces` | Get Layer 3 interfaces with IP addresses. | `host: str` | Read |
| `net_get_trunk_interfaces` | Get trunk port configuration. | `host: str` | Read |
| `net_get_transceiver_info` | Get transceiver / optics DOM data. | `host: str` | Read |
| `net_configure_interface_description` | [WRITE] Set interface description on any vendor device. | `host: str`, `interface: str`, `description: str` | Write |
| `net_shutdown_interface` | [WRITE] Administratively disable an interface. | `host: str`, `interface: str` | Destructive |
| `net_no_shutdown_interface` | [WRITE] Administratively enable an interface. | `host: str`, `interface: str` | Destructive |

### <a id="net-routing"></a>Routing

Module: `tools/common/routing.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_bgp_summary` | Get BGP summary from any supported network device. | `host: str`, `vrf: str` = 'default' | Read |
| `net_get_ospf_neighbors` | Get OSPF neighbor adjacencies. | `host: str`, `vrf: str` = 'default' | Read |
| `net_get_ospf_interfaces` | Get OSPF-enabled interfaces. | `host: str`, `vrf: str` = 'default' | Read |
| `net_get_route_table` | Get IP routing table with pagination and protocol filter. | `host: str`, `protocol: str` = '', `vrf: str` = 'default', `page: int` = 1, `page_size: int` = 100 | Read |
| `net_get_route_summary` | Get route count summary by protocol. | `host: str`, `vrf: str` = 'default' | Read |
| `net_get_arp_table` | Get ARP/NDP table with pagination. | `host: str`, `vrf: str` = 'default', `page: int` = 1, `page_size: int` = 100 | Read |
| `net_get_bgp_neighbors` | Get detailed BGP neighbor information. | `host: str`, `vrf: str` = 'default' | Read |
| `net_create_static_route` | [WRITE] Create a static route on any vendor device. | `host: str`, `prefix: str`, `next_hop: str`, `admin_distance: int` = 1, `vrf: str` = 'default' | Write |
| `net_configure_bgp_neighbor` | [WRITE] Add a BGP neighbor on any vendor device. | `host: str`, `local_as: int`, `neighbor: str`, `remote_as: int`, `description: str` = '' | Write |

### <a id="net-switching"></a>Switching

Module: `tools/common/switching.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_lldp_neighbors` | Get LLDP neighbor information. | `host: str` | Read |
| `net_get_mac_table` | Get MAC address table with pagination and filtering. | `host: str`, `vlan: int` = 0, `interface: str` = '', `page: int` = 1, `page_size: int` = 100 | Read |
| `net_get_stp_status` | Get spanning tree status. | `host: str` | Read |
| `net_get_port_channels` | Get LAG/port-channel status. | `host: str` | Read |
| `net_get_lldp_neighbor_detail` | Get detailed LLDP neighbor TLV data. | `host: str`, `interface: str` = '' | Read |

### <a id="net-vlans"></a>VLANs

Module: `tools/common/vlans.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_vlans` | Get all VLANs with pagination. | `host: str`, `page: int` = 1, `page_size: int` = 50 | Read |
| `net_get_vlan_detail` | Get detailed info for a specific VLAN. | `host: str`, `vlan_id: int` | Read |
| `net_create_vlan` | [WRITE] Create a VLAN on any supported network device. | `host: str`, `vlan_id: int`, `name: str` | Write |
| `net_delete_vlan` | [WRITE] Delete a VLAN from any supported network device. | `host: str`, `vlan_id: int` | Destructive |
| `net_rename_vlan` | [WRITE] Rename a VLAN on any supported network device. | `host: str`, `vlan_id: int`, `name: str` | Write |
| `net_assign_interface_vlan` | [WRITE] Assign an access VLAN to an interface. | `host: str`, `interface: str`, `vlan_id: int` | Write |

### <a id="net-configuration"></a>Configuration

Module: `tools/common/config.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_running_config` | Get full running configuration from any vendor device. | `host: str`, `sanitize: bool` = True | Read |
| `net_get_running_config_section` | Get a specific section of running configuration. | `host: str`, `section: str`, `sanitize: bool` = True | Read |
| `net_get_startup_config` | Get startup configuration from any vendor device. | `host: str`, `sanitize: bool` = True | Read |
| `net_compare_configs` | Compare running vs startup configuration. | `host: str` | Read |
| `net_save_config` | [WRITE] Save running configuration to startup. | `host: str` | Write |
| `net_push_config` | [WRITE] Push configuration commands to any vendor device. | `host: str`, `commands: list[str]` | Write |

### <a id="net-vrf"></a>VRF

Module: `tools/common/vrf.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_vrf_list` | Get list of VRFs configured on any vendor device. | `host: str` | Read |
| `net_get_vrf_detail` | Get detailed VRF information. | `host: str`, `vrf: str` | Read |

### <a id="net-security"></a>Security

Module: `tools/common/security.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_acls` | Get access control lists from any vendor device. | `host: str` | Read |
| `net_get_aaa_status` | Get AAA authentication/authorization configuration. | `host: str` | Read |
| `net_get_snmp_config` | Get SNMP configuration with secret redaction. | `host: str` | Read |
| `net_get_users` | Get active user sessions on any vendor device. | `host: str` | Read |
| `net_get_copp_policy` | Get Control Plane Policing policy. | `host: str` | Read |

### <a id="net-monitoring"></a>Monitoring

Module: `tools/common/monitoring.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_get_cpu_usage` | Get CPU utilization. | `host: str` | Read |
| `net_get_memory_usage` | Get memory utilization. | `host: str` | Read |
| `net_get_environment_temp` | Get temperature sensor readings. | `host: str` | Read |
| `net_get_environment_power` | Get power supply status. | `host: str` | Read |
| `net_get_environment_cooling` | Get fan/cooling status. | `host: str` | Read |
| `net_get_ntp_status` | Get NTP synchronization status. | `host: str` | Read |
| `net_get_device_health` | Comprehensive health check combining CPU, memory, temp, fans, power. | `host: str` | Read |
| `net_get_logging` | Get recent syslog messages. | `host: str`, `lines: int` = 50 | Read |

### <a id="net-troubleshooting"></a>Troubleshooting

Module: `tools/common/troubleshoot.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_ping` | Execute a ping from any vendor device. | `host: str`, `destination: str`, `count: int` = 5, `source: str` = '', `vrf: str` = 'default' | Read |
| `net_traceroute` | Execute a traceroute from any vendor device. | `host: str`, `destination: str`, `source: str` = '', `vrf: str` = 'default' | Read |
| `net_check_interface_flaps` | Check for interface flap events. | `host: str`, `hours: int` = 24 | Read |

### <a id="net-workflows"></a>Workflows

Module: `tools/common/workflows.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_diagnose_bgp_session` | Diagnose a BGP session issue on any vendor device. | `host: str`, `neighbor_ip: str` | Read |
| `net_troubleshoot_interface` | Troubleshoot an interface on any vendor device. | `host: str`, `interface: str` | Read |
| `net_validate_fabric` | Validate fabric health across all devices in inventory. | `role: str \| None` | Read |

### <a id="net-change-management"></a>Change Management

Module: `tools/common/changes.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_list_pending_changes` | List pending change requests awaiting approval or execution. | `host: str` = '' | Read |
| `net_get_change_request` | Get details of a specific change request by ID. | `host: str`, `change_id: str` | Read |
| `net_approve_change` | [WRITE] Approve a pending change request. | `host: str`, `change_id: str`, `approved_by: str` = 'admin' | Write |
| `net_rollback_change` | [WRITE] Roll back a completed or failed change. | `host: str`, `change_id: str` | Write |

### <a id="net-drift-detection"></a>Drift Detection

Module: `tools/common/drift.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_check_drift` | Check configuration drift on any vendor device against golden config. | `host: str`, `golden_source: str` = 'file' | Read |
| `net_drift_report` | Generate drift report across multiple devices. | `hosts: list[str] \| None` | Read |
| `net_remediate_drift` | [WRITE] Remediate configuration drift by pushing golden config sections. | `host: str`, `golden_source: str` = 'file', `severity: str` = 'critical' | Write |

---

## Arista EOS Tools (`eos_*`)

### Device Information

Module: `tools/device.py` (12 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_device_info` | Get version, model, serial number, and uptime from an Arista EOS device. | `host: str` | Read |
| `eos_get_device_health` | Get comprehensive health overview: CPU, memory, temperature, fans, power. | `host: str` | Read |
| `eos_get_hardware_inventory` | Get hardware inventory including modules, line cards, fans, PSUs. | `host: str` | Read |
| `eos_get_running_extensions` | Get installed EOS extensions and packages. | `host: str` | Read |
| `eos_get_users` | Get currently logged-in users and active sessions. | `host: str` | Read |
| `eos_get_hardware_capacity` | Get hardware forwarding table capacity and TCAM utilization. | `host: str` | Read |
| `eos_get_hardware_drops` | Get hardware drop counters from the forwarding ASIC. | `host: str` | Read |
| `eos_get_terminattr_status` | Get TerminAttr streaming telemetry agent status. | `host: str` | Read |
| `eos_cache_clear` | Clear cached command results (all or per-host). | `host: str \| None` = None | Read |
| `eos_cache_stats` | Get command cache statistics (hits, misses, size). | *(none)* | Read |
| `eos_server_health` | Get MCP server health status including circuit breaker states. | *(none)* | Read |
| `eos_server_metrics` | Get server metrics (tool calls, durations, cache stats). | `format: str` = 'json' | Read |

### Interfaces

Module: `tools/interfaces.py` (9 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_interfaces_status` | Get all interfaces with status, VLAN, speed, and type. | `host: str`, `fields: list[str] \| None`, `limit: int \| None`, `offset: int` | Read |
| `eos_get_interface_detail` | Get detailed statistics and configuration for a specific interface. | `host: str`, `interface: str` | Read |
| `eos_get_interface_counters` | Get packet and byte counters for all interfaces. | `host: str` | Read |
| `eos_get_interface_errors` | Get error counters (CRC, runts, giants) for all interfaces. | `host: str` | Read |
| `eos_get_ip_interfaces` | Get Layer 3 interfaces with IP addresses and status. | `host: str` | Read |
| `eos_get_transceiver_info` | Get transceiver/optic information (type, power levels, temp). | `host: str` | Read |
| `eos_configure_interface_description` | [WRITE] Set the description on an interface. | `host: str`, `interface: str`, `description: str` | Write |
| `eos_shutdown_interface` | [WRITE] Administratively disable an interface. | `host: str`, `interface: str` | Write |
| `eos_no_shutdown_interface` | [WRITE] Administratively enable an interface. | `host: str`, `interface: str` | Write |

### VLANs

Module: `tools/vlans.py` (6 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_vlans` | Get all VLANs with names and assigned ports. | `host: str`, `fields: list[str] \| None`, `limit: int \| None`, `offset: int` | Read |
| `eos_get_vlan_detail` | Get detailed information for a specific VLAN. | `host: str`, `vlan_id: int` | Read |
| `eos_create_vlan` | [WRITE] Create a new VLAN. | `host: str`, `vlan_id: int`, `name: str` | Write |
| `eos_delete_vlan` | [WRITE] Delete a VLAN. | `host: str`, `vlan_id: int` | Write |
| `eos_rename_vlan` | [WRITE] Rename an existing VLAN. | `host: str`, `vlan_id: int`, `name: str` | Write |
| `eos_assign_interface_vlan` | [WRITE] Assign an access VLAN to an interface. | `host: str`, `interface: str`, `vlan_id: int` | Write |

### Routing & Protocols

Module: `tools/routing.py` (15 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_bgp_summary` | Get BGP neighbor summary with peer states, prefix counts, uptime. | `host: str`, `address_family: str` = 'ipv4', `vrf: str` = 'default', `fields`, `limit`, `offset` | Read |
| `eos_get_bgp_neighbors` | Get detailed BGP neighbor information. | `host: str`, `address_family: str` = 'ipv4', `neighbor: str \| None`, `vrf: str` = 'default' | Read |
| `eos_get_bgp_routes` | Get the BGP routing table. | `host: str`, `address_family: str` = 'ipv4', `vrf: str` = 'default' | Read |
| `eos_get_ospf_neighbors` | Get OSPF adjacencies and neighbor states. | `host: str` | Read |
| `eos_get_ospf_interfaces` | Get OSPF-enabled interfaces and their configuration. | `host: str` | Read |
| `eos_get_route_table` | Get the full IP routing table. | `host: str`, `address_family: str` = 'ipv4', `vrf: str` = 'default', `fields`, `limit`, `offset` | Read |
| `eos_get_route_summary` | Get route count by protocol. | `host: str`, `address_family: str` = 'ipv4', `vrf: str` = 'default' | Read |
| `eos_get_arp_table` | Get ARP table (IPv4) or IPv6 Neighbor Discovery table. | `host: str`, `address_family: str` = 'ipv4', `vrf: str` = 'default' | Read |
| `eos_get_prefix_list` | Get configured IP prefix lists. | `host: str`, `name: str \| None` | Read |
| `eos_get_route_maps` | Get configured route maps. | `host: str`, `name: str \| None` | Read |
| `eos_get_vrrp_status` | Get VRRP (Virtual Router Redundancy Protocol) status. | `host: str` | Read |
| `eos_get_varp_status` | Get Virtual ARP (VARP) status. | `host: str` | Read |
| `eos_create_bgp_neighbor` | [WRITE] Create a BGP neighbor. | `host: str`, `asn: int`, `neighbor_ip: str`, `remote_asn: int`, `description: str \| None` | Write |
| `eos_delete_bgp_neighbor` | [WRITE] Delete a BGP neighbor. | `host: str`, `asn: int`, `neighbor_ip: str` | Write |
| `eos_enable_bgp_af` | [WRITE] Activate a BGP neighbor in an address family. | `host: str`, `asn: int`, `address_family: str`, `neighbor_ip: str` | Write |

### Switching & L2

Module: `tools/switching.py` (9 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_mlag_status` | Get MLAG domain status, peer link state, and port summary. | `host: str` | Read |
| `eos_get_spanning_tree` | Get spanning tree topology, root bridge, and port states. | `host: str` | Read |
| `eos_get_mac_address_table` | Get MAC address table entries with VLANs and ports. | `host: str`, `vlan: int \| None`, `fields`, `limit`, `offset` | Read |
| `eos_get_lldp_neighbors` | Get LLDP neighbor discovery information. | `host: str` | Read |
| `eos_get_lldp_neighbor_detail` | Get detailed LLDP neighbor info for a specific interface. | `host: str`, `interface: str` | Read |
| `eos_get_port_channels` | Get LAG/port-channel summary with member status. | `host: str` | Read |
| `eos_get_mlag_config_sanity` | Check MLAG configuration consistency between peers. | `host: str` | Read |
| `eos_get_mlag_interfaces` | Get per-interface MLAG status. | `host: str` | Read |
| `eos_get_trunk_interfaces` | Get trunk interfaces with allowed, native, and active VLANs. | `host: str` | Read |

### EVPN / VXLAN

Module: `tools/evpn_vxlan.py` (9 tools) — *Optional module*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_vxlan_interface` | Get VXLAN tunnel interface (Vxlan1) configuration and status. | `host: str` | Read |
| `eos_get_vxlan_vtep` | Get discovered remote VTEPs. | `host: str` | Read |
| `eos_get_vxlan_vni` | Get VNI to VLAN and VRF mappings. | `host: str` | Read |
| `eos_get_vxlan_address_table` | Get VXLAN remote MAC address table. | `host: str`, `vlan: int \| None` | Read |
| `eos_get_vxlan_config_sanity` | Run VXLAN configuration sanity check. | `host: str` | Read |
| `eos_get_bgp_evpn_summary` | Get BGP EVPN peer summary. | `host: str` | Read |
| `eos_get_bgp_evpn_instance` | Get BGP EVPN instance details (RD, RT, VNIs). | `host: str` | Read |
| `eos_get_evpn_route_type_macip` | Get EVPN Type-2 MAC/IP routes. | `host: str`, `vni: int \| None` | Read |
| `eos_get_evpn_route_type_imet` | Get EVPN Type-3 IMET routes. | `host: str` | Read |

### Monitoring & Environment

Module: `tools/monitoring.py` (8 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_cpu_usage` | Get CPU utilization and top processes. | `host: str` | Read |
| `eos_get_memory_usage` | Get memory utilization (total, free, used %). | `host: str` | Read |
| `eos_get_environment_temp` | Get temperature sensor readings. | `host: str` | Read |
| `eos_get_environment_power` | Get power supply status and readings. | `host: str` | Read |
| `eos_get_environment_cooling` | Get fan status and speed readings. | `host: str` | Read |
| `eos_get_ntp_status` | Get NTP synchronization status and peer associations. | `host: str` | Read |
| `eos_get_logging` | Get recent syslog messages. | `host: str`, `lines: int` = 50 | Read |
| `eos_get_reload_cause` | Get the last reload/reboot reason. | `host: str` | Read |

### Configuration Management

Module: `tools/config_mgmt.py` (7 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_running_config` | Get the full running configuration. | `host: str`, `sanitize: bool` = True | Read |
| `eos_get_running_config_section` | Get a specific section of running config. | `host: str`, `section: str`, `sanitize: bool` = True | Read |
| `eos_get_startup_config` | Get the startup configuration. | `host: str`, `sanitize: bool` = True | Read |
| `eos_diff_running_startup` | Get diff between running and startup configuration. | `host: str` | Read |
| `eos_detect_config_drift` | Detect configuration drift between running and startup configs. | `host: str` | Read |
| `eos_push_config_commands` | [WRITE] Push a list of configuration commands. | `host: str`, `commands: list[str]` | Destructive |
| `eos_save_running_config` | [WRITE] Save running configuration to startup. | `host: str` | Write |

### Config Sessions

Module: `tools/sessions.py` (8 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_create_config_session` | [WRITE] Create a named configuration session. | `host: str`, `session_name: str \| None` | Write |
| `eos_get_config_session_diff` | Show pending changes (diff) for a config session. | `host: str`, `session_name: str` | Read |
| `eos_add_to_config_session` | [WRITE] Stage config commands into a session. | `host: str`, `session_name: str`, `commands: list[str]` | Write |
| `eos_commit_config_session` | [WRITE] Commit a session, applying all changes immediately. | `host: str`, `session_name: str` | Destructive |
| `eos_commit_config_session_timer` | [WRITE] Commit with auto-rollback timer. | `host: str`, `session_name: str`, `timer: str` = '00:05:00' | Destructive |
| `eos_confirm_commit_timer` | [WRITE] Confirm a timer-based commit. | `host: str`, `session_name: str` | Write |
| `eos_abort_config_session` | [WRITE] Abort a session, discarding all changes. | `host: str`, `session_name: str` | Write |
| `eos_list_config_sessions` | List all configuration sessions. | `host: str` | Read |

### Config Checkpoints

Module: `tools/checkpoints.py` (4 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_create_checkpoint` | [WRITE] Save running config as a named checkpoint. | `host: str`, `checkpoint_name: str` | Write |
| `eos_list_checkpoints` | List all configuration checkpoints. | `host: str` | Read |
| `eos_rollback_to_checkpoint` | [DESTRUCTIVE] Restore running config from checkpoint. | `host: str`, `checkpoint_name: str` | Destructive |
| `eos_delete_config_checkpoint` | [WRITE] Delete a configuration checkpoint. | `host: str`, `checkpoint_name: str` | Destructive |

### Security & SNMP

Module: `tools/security.py` (10 tools) — *Optional module*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_ip_access_lists` | Get IP access lists (ACLs). | `host: str`, `name: str \| None` | Read |
| `eos_get_aaa_status` | Get AAA configuration status. | `host: str` | Read |
| `eos_get_radius_status` | Get RADIUS server configuration and status. | `host: str` | Read |
| `eos_get_tacacs_status` | Get TACACS+ server configuration and status. | `host: str` | Read |
| `eos_get_copp_policy` | Get Control Plane Policing (CoPP) policy. | `host: str` | Read |
| `eos_get_eapi_status` | Get eAPI management HTTP API status. | `host: str` | Read |
| `eos_apply_acl_to_interface` | [WRITE] Apply ACL to an interface. | `host: str`, `interface: str`, `acl_name: str`, `direction: str` | Write |
| `eos_remove_acl_from_interface` | [WRITE] Remove ACL from an interface. | `host: str`, `interface: str`, `acl_name: str`, `direction: str` | Write |
| `eos_get_snmp_config` | Get SNMP configuration (communities, users, groups). | `host: str` | Read |
| `eos_get_snmp_traps` | Get SNMP trap configuration and receivers. | `host: str` | Read |

### QoS

Module: `tools/qos.py` (3 tools) — *Optional module*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_policy_maps` | Get QoS policy-map configuration. | `host: str`, `name: str \| None` | Read |
| `eos_get_class_maps` | Get QoS class-map configuration. | `host: str`, `name: str \| None` | Read |
| `eos_get_traffic_policy` | Get traffic-policy configuration and counters. | `host: str`, `direction: str \| None` | Read |

### Troubleshooting

Module: `tools/troubleshoot.py` (3 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_ping_from_device` | Execute a ping from a network device to test reachability. | `host: str`, `destination: str`, `source: str \| None`, `count: int` = 5 | Read |
| `eos_traceroute_from_device` | Execute a traceroute from a network device. | `host: str`, `destination: str`, `source: str \| None` | Read |
| `eos_check_interface_flaps` | Check for recent interface flapping. | `host: str`, `interface: str \| None`, `log_lines: int` = 100 | Read |

### VRF

Module: `tools/vrf.py` (2 tools) — *Optional module*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_vrfs` | Get all VRFs on the device. | `host: str` | Read |
| `eos_get_vrf_detail` | Get detailed information for a specific VRF. | `host: str`, `name: str` | Read |

### BFD

Module: `tools/bfd.py` (2 tools) — *Optional module*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_bfd_peers` | Get all BFD sessions with status and intervals. | `host: str`, `vrf: str` = 'default' | Read |
| `eos_get_bfd_peer_detail` | Get detailed BFD session info with counters. | `host: str`, `peer: str`, `vrf: str` = 'default' | Read |

### Event Monitor

Module: `tools/event_monitor.py` (3 tools) — *Optional module*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_get_event_monitor` | Get recent system events from Event Monitor. | `host: str`, `event_type: str \| None`, `limit: int` = 100 | Read |
| `eos_get_mac_move_events` | Get MAC address move events. | `host: str`, `limit: int` = 50 | Read |
| `eos_get_route_change_events` | Get route change events. | `host: str`, `limit: int` = 50 | Read |

### Multi-Device Fabric

Module: `tools/fabric.py` (6 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_list_inventory` | List devices in inventory (filtered by role, group, tag). | `role: str \| None`, `group: str \| None`, `tag: str \| None` | Read |
| `eos_fabric_health_summary` | Get health summary across multiple devices. | `targets: str` = 'all' | Read |
| `eos_fabric_bgp_status` | Get BGP status across multiple devices with aggregate counts. | `targets: str` = 'all' | Read |
| `eos_fabric_mlag_status` | Get MLAG status across multiple devices. | `targets: str` = 'all' | Read |
| `eos_compare_configs` | Get running config from multiple devices for comparison. | `devices: list[str]`, `section: str \| None` | Read |
| `eos_fabric_health_async` | Run fabric health check as an MCP Task (async). | `targets: str` = 'all' | Read |

### ANTA Validation

Module: `tools/validation.py` (5 tools) — *Requires `anta` package*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_validate_bgp` | Run ANTA BGP validation tests. | `host: str` | Read |
| `eos_validate_mlag` | Run ANTA MLAG validation tests. | `host: str` | Read |
| `eos_validate_interfaces` | Run ANTA interface validation tests. | `host: str` | Read |
| `eos_validate_system` | Run ANTA system/hardware validation tests. | `host: str` | Read |
| `eos_run_anta_tests` | Run arbitrary ANTA test catalog. | `host: str`, `catalog_yaml: str` | Read |

### gNMI Telemetry

Module: `tools/gnmi.py` (5 tools) — *Requires `pygnmi` package*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_gnmi_get` | Retrieve operational/config data via gNMI Get. | `host: str`, `paths: list[str]` | Read |
| `eos_gnmi_subscribe_once` | One-shot gNMI subscription for telemetry snapshot. | `host: str`, `paths: list[str]` | Read |
| `eos_gnmi_capabilities` | Get gNMI capabilities from a network device. | `host: str` | Read |
| `eos_gnmi_subscribe_stream` | Stream gNMI telemetry data for a specified duration. | `host: str`, `paths: list[str]`, `duration: int` = 10 | Read |
| `eos_gnmi_set` | [WRITE] Modify device configuration via gNMI Set. | `host: str`, `updates: list[dict] \| None`, `deletes: list[str] \| None` | Write |

### Workflow Tools

Module: `tools/workflows.py` (7 tools)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_diagnose_bgp_session` | End-to-end BGP session diagnosis: peer state, route reachability, interface health. | `host: str`, `neighbor_ip: str` | Read |
| `eos_troubleshoot_interface_issues` | End-to-end interface troubleshooting: link state, errors, transceiver levels. | `host: str`, `interface: str` | Read |
| `eos_compare_device_configs` | Compare running configs between two devices with section-aware diff. | `host1: str`, `host2: str`, `sections: list[str] \| None` | Read |
| `eos_validate_fabric_health` | Fabric-wide health validation across all inventory devices. | `role: str` = 'all' | Read |
| `eos_pre_change_snapshot` | Capture pre-change state snapshot for later comparison. | `host: str` | Read |
| `eos_post_change_validation` | Validate current state against a pre-change snapshot. | `host: str`, `snapshot_id: str` | Read |
| `eos_interactive_troubleshoot` | Interactive troubleshooting session with MCP elicitation. | `host: str` | Read |

#### Workflow Tool Decision Tree

```
"Is a BGP neighbor down?"
  --> eos_diagnose_bgp_session(host, neighbor_ip)

"This interface has errors"
  --> eos_troubleshoot_interface_issues(host, interface)

"Are these two switches configured the same?"
  --> eos_compare_device_configs(host1, host2, sections=['router bgp'])

"Is the fabric healthy?"
  --> eos_validate_fabric_health(role='all')

"I'm about to make a change"
  --> eos_pre_change_snapshot(host) -> make changes -> eos_post_change_validation(host, snapshot_id)

"Help me troubleshoot step by step"
  --> eos_interactive_troubleshoot(host)
```

### Export

Module: `tools/export.py` (1 tool)

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_export_report` | Export tool output as a formatted report (Markdown, CSV, or JSON). | `data: dict`, `output_format: str` = 'markdown', `title: str` = 'Network Report' | Read |

### Compliance

Module: `tools/compliance.py` (2 tools) — *Optional module*

CIS benchmark compliance checking with pass/fail results, severity ratings, and remediation guidance. Supports EOS, IOS-XE, and NX-OS frameworks.

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_compliance_check` | Run compliance checks against a network device. | `host: str`, `framework: str` = 'cis-eos', `category: str \| None` | Read |
| `eos_list_compliance_checks` | List available compliance checks and their details. | `framework: str \| None` | Read |

### CloudVision

Module: `tools/cloudvision.py` (2 tools) — *Requires `EOS_CVP_URL`*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_cvp_inventory_sync` | Sync device inventory from CloudVision Portal. | *(none)* | Read |
| `eos_cvp_compliance_check` | Check configuration compliance via CloudVision. | `host: str` | Read |

---

## Cisco NX-OS Tools (`nxos_*`)

Modules: `tools/cisco/` — *Requires `cisco` extra (`pip install latticio[cisco]`)*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `nxos_get_vpc_status` | Get vPC (Virtual Port Channel) status from a Cisco NX-OS device. | `host: str` | Read |
| `nxos_get_vpc_detail` | Get detailed vPC information from a Cisco NX-OS device. | `host: str` | Read |
| `nxos_get_fex_status` | Get Fabric Extender (FEX) status from a Cisco NX-OS device. | `host: str` | Read |
| `nxos_get_fex_detail` | Get detailed FEX unit information. | `host: str`, `fex_id: int` | Read |
| `nxos_get_feature_list` | Get enabled/disabled feature list from a Cisco NX-OS device. | `host: str` | Read |
| `nxos_get_system_resources` | Get detailed system resources from a Cisco NX-OS device. | `host: str` | Read |

---

## Cisco IOS-XE Tools (`iosxe_*`)

Modules: `tools/cisco/` — *Requires `cisco` extra (`pip install latticio[cisco]`)*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `iosxe_get_restconf_capabilities` | Get RESTCONF capabilities from a Cisco IOS-XE device. | `host: str` | Read |
| `iosxe_get_platform_info` | Get detailed platform information from a Cisco IOS-XE device. | `host: str` | Read |
| `iosxe_get_sdwan_status` | Get SD-WAN status from a Cisco IOS-XE device. | `host: str` | Read |

---

## Juniper JunOS Tools (`junos_*`)

Modules: `tools/juniper/` — *Requires `juniper` extra (`pip install latticio[juniper]`)*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `junos_get_routing_instances` | Get routing instances from a Juniper JunOS device. | `host: str` | Read |
| `junos_get_firewall_filters` | Get firewall filter details from a Juniper JunOS device. | `host: str` | Read |
| `junos_get_system_alarms` | Get active system alarms from a Juniper JunOS device. | `host: str` | Read |
| `junos_get_commit_history` | Get commit history from a Juniper JunOS device. | `host: str`, `count: int` = 10 | Read |
| `junos_compare_rollback` | Compare current config against a rollback point. | `host: str`, `rollback_id: int` = 0 | Read |
| `junos_commit_confirmed` | [WRITE] Commit configuration with automatic rollback (commit-confirmed). | `host: str`, `minutes: int` = 10, `comment: str` = '' | Write |
| `junos_confirm_commit` | [WRITE] Confirm a pending commit-confirmed. | `host: str` | Write |

---

## Integration Tools

### Ansible Integration

Module: `integrations/ansible.py`

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_generate_playbook` | Generate an Ansible playbook from a task description. | `task: str`, `hosts: str` = 'all', `platform: str` = 'eos' | Read |
| `net_inventory_to_ansible` | Export network-mcp inventory as Ansible inventory YAML. | *(none)* | Read |

### ServiceNow Integration

Module: `integrations/servicenow.py` — *Requires `SNOW_INSTANCE`*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_create_incident` | Create a ServiceNow incident for a network device issue. | `host: str`, `short_description: str`, `urgency: int` = 2, `include_diagnostics: bool` = True | Write |
| `net_sync_cmdb` | Sync device inventory to ServiceNow CMDB. | `host: str` | Write |
| `net_update_incident` | Update an existing ServiceNow incident. | `host: str`, `sys_id: str`, `work_notes: str \| None`, `state: int \| None` | Write |
| `net_validate_change` | Validate a network change by comparing pre/post device state. | `host: str`, `change_sys_id: str`, `pre_snapshot_commands: list[str] \| None`, `post_snapshot_commands: list[str] \| None` | Write |

### NetBox Integration

Module: `integrations/netbox.py` — *Requires `NETBOX_URL`*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_sync_from_netbox` | Pull devices from NetBox DCIM and sync to local inventory. | `site: str \| None`, `role: str \| None`, `tag: str \| None` | Read |
| `net_push_to_netbox` | Push discovered device facts from a live device to NetBox DCIM. | `host: str` | Write |
| `net_netbox_drift_check` | Compare NetBox DCIM state vs live device state. | `site: str \| None` | Read |
| `net_netbox_cable_audit` | Validate LLDP neighbors match NetBox cable records. | `site: str \| None` | Read |

### Prometheus Integration

Module: `integrations/prometheus.py` — *Requires `PROMETHEUS_ENABLED=true`*

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `net_collect_prometheus_metrics` | Collect device health metrics and expose in Prometheus text format. | `hosts: list[str] \| None` | Read |
| `net_get_grafana_dashboard` | Generate a ready-to-import Grafana dashboard JSON. | `dashboard_type: str` | Read |

---

## Meta / Discovery

Module: `tools/meta.py` (4 tools)

Progressive discovery tools help LLMs discover relevant tools without loading all tool definitions into context.

| Tool | Description | Parameters | Type |
|------|-------------|------------|------|
| `eos_list_tool_categories` | List available tool categories with descriptions and tool counts. | *(none)* | Read |
| `eos_list_tools_in_category` | List all tools in a category with descriptions and parameters. | `category: str` | Read |
| `eos_load_tool_category` | Dynamically activate tools in a category (progressive discovery mode). | `category: str` | Read |
| `net_list_plugins` | List all installed network-mcp plugins (drivers, compliance, tools). | *(none)* | Read |

---

## Resources

Read-only data endpoints accessible via MCP resource URIs.

| URI | Description |
|-----|-------------|
| `net://inventory` | List of all devices in the inventory. |
| `net://devices` | Richer device list with roles, tags, groups. |
| `net://{host}/version` | Device version and model information. |
| `net://{host}/running-config` | Full running configuration (credentials sanitized). |
| `net://{host}/running-config/{section}` | Per-section running configuration. |
| `net://{host}/health` | Quick health summary: CPU, memory, uptime, interface counts. |
| `net://{host}/interfaces/summary` | Interface summary with link status counts and error interfaces. |
| `net://{host}/interfaces/{interface}` | Single interface deep-dive: status, counters, config. |
| `net://{host}/bgp/summary` | BGP peer summary for a device. |
| `net://{host}/vlans` | VLAN table for a device. |

---

## Prompts

Reusable workflow templates that guide the AI through multi-step network operations.

### EOS-Specific Prompts

| Prompt | Description | Parameters |
|--------|-------------|------------|
| `troubleshoot_interface` | Step-by-step interface troubleshooting workflow. | `host: str`, `interface: str` |
| `bgp_health_check` | BGP health check workflow. | `host: str` |
| `pre_change_audit` | Pre-change state capture for change management. | `host: str` |
| `daily_health_check` | Comprehensive daily operational health check. | `host: str` |
| `post_change_validation` | Post-change validation against baseline. | `host: str` |
| `security_audit` | Security baseline compliance audit. | `host: str` |
| `mlag_troubleshoot` | MLAG troubleshooting and consistency verification. | `host: str` |
| `safe_config_change` | Guided config session workflow with risk classification. | `host: str`, `change_description: str` |
| `evpn_vxlan_health_check` | Full EVPN/VXLAN fabric validation. | `host: str` |

### Vendor-Agnostic Prompts

| Prompt | Description | Parameters |
|--------|-------------|------------|
| `net_troubleshoot_interface` | Multi-vendor interface troubleshooting workflow. | `host: str`, `interface: str` |
| `net_bgp_health_check` | Multi-vendor BGP health check workflow. | `host: str` |
| `net_pre_change_audit` | Multi-vendor pre-change audit workflow. | `host: str` |

---

## OpenAPI Specification

When running with HTTP transport (`--transport streamable-http`), the server exposes an OpenAPI 3.1 specification at:

```
GET /openapi.json
```

This specification is auto-generated from all registered MCP tools and includes:

- All tool endpoints as `POST /tools/{tool_name}`
- JSON Schema for each tool's request parameters
- Tool access annotations (read-only, write, destructive) as tags
- JWT Bearer authentication scheme
- Rate limiting and circuit breaker error responses

## Deprecation Policy

- Deprecated tools remain functional for **2 major versions** (12 months minimum)
- Deprecated tool responses include metadata: `{"_deprecated": true, "_sunset": "YYYY-MM-DD"}`
- New tools may be marked as **beta** for 1 minor version before becoming stable
- Beta tool responses include: `{"_beta": true, "_since": "X.Y.Z"}`
- Use `versioning.is_deprecated(func)` and `versioning.is_beta(func)` for introspection
