# Driver Coverage Matrix

Coverage of the 35+ NetworkDriver protocol methods across all 5 vendor drivers.

## Summary

| Driver | Platform | Transport | Implemented | Not Supported | Coverage |
|--------|----------|-----------|-------------|---------------|----------|
| EosDriver | `eos` | pyeapi (eAPI) | 33 | 4 | 89% |
| IosXeDriver | `iosxe` | RESTCONF + SSH | 33 | 4 | 89% |
| NxosDriver | `nxos` | NX-API + SSH | 33 | 4 | 89% |
| JunosDriver | `junos` | NETCONF + SSH | 33 | 4 | 89% |
| SonicDriver | `sonic` | REST (OpenConfig) | 10 | 27 | 27% |

## Method Coverage

✅ = Implemented | ❌ = NotSupportedError

| Method | EOS | IOS-XE | NX-OS | JunOS | SONiC |
|--------|:---:|:------:|:-----:|:-----:|:-----:|
| **Core** | | | | | |
| `get_facts` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_interfaces` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_config` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `run_show` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `run_config` | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Routing** | | | | | |
| `get_bgp_summary` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_bgp_neighbors` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_route_table` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_route_summary` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_ospf_neighbors` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_ospf_interfaces` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_arp_table` | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Switching** | | | | | |
| `get_lldp_neighbors` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_lldp_neighbor_detail` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_mac_table` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_stp_status` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_port_channels` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_vlans` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_vlan_detail` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_trunk_interfaces` | ✅ | ✅ | ✅ | ✅ | ❌ |
| **VRF** | | | | | |
| `get_vrfs` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_vrf_detail` | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Interfaces** | | | | | |
| `get_interface_counters` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_interface_errors` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_interface_flaps` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_ip_interfaces` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_transceiver_info` | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Monitoring** | | | | | |
| `get_environment` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_ntp_status` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_cpu_memory` | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Config** | | | | | |
| `get_running_config_section` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_startup_config` | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Security** | | | | | |
| `get_acls` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_snmp_config` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `get_aaa_status` | ❌ | ❌ | ❌ | ❌ | ❌ |
| `get_user_sessions` | ❌ | ❌ | ❌ | ❌ | ❌ |
| `get_copp_policy` | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Diagnostics** | | | | | |
| `ping` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `traceroute` | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Management** | | | | | |
| `capabilities` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `reconnect` | ✅ | ✅ | ✅ | ✅ | ✅ |

## Universal Gaps (Not Supported on Any Driver)

| Method | Reason |
|--------|--------|
| `get_aaa_status` | Vendor-specific AAA APIs not yet implemented |
| `get_user_sessions` | Active session introspection varies significantly by vendor |
| `get_copp_policy` | Primarily NX-OS/EOS concept; not uniform across vendors |

## SONiC Remediation Priority

The SONiC driver has the largest gap (27 methods). Priority for implementation:

1. **High** — `get_arp_table`, `get_mac_table`, `get_bgp_neighbors` (essential for troubleshooting)
2. **Medium** — `get_config`, `get_interface_counters`, `get_cpu_memory`, `ping` (operational monitoring)
3. **Low** — `get_stp_status`, `get_ospf_*`, `get_transceiver_info` (less common on SONiC deployments)

## Transport Details

| Driver | Primary Transport | Fallback | Auth |
|--------|------------------|----------|------|
| EOS | eAPI (JSON-RPC over HTTPS) | — | Basic auth |
| IOS-XE | RESTCONF (HTTPS) | SSH (scrapli) | Basic auth |
| NX-OS | NX-API (JSON-RPC) | SSH (scrapli) | Basic auth |
| JunOS | NETCONF (SSH) | SSH CLI (scrapli) | Key/password |
| SONiC | REST API (OpenConfig YANG) | — | Token/basic |

## Test Coverage

| Driver | Unit Tests | Integration Tests | Confidence |
|--------|-----------|-------------------|------------|
| EOS | 2,000+ | 265 (live cEOS) | **High** — production-tested |
| IOS-XE | 400+ | 0 (no free image) | **Medium** — mock-tested only |
| NX-OS | 400+ | 0 (no free image) | **Medium** — mock-tested only |
| JunOS | 300+ | 0 (no free image) | **Medium** — mock-tested only |
| SONiC | 100+ | 0 (VS image available) | **Low** — minimal coverage |
