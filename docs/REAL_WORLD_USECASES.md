# Real-World Use Cases for Network MCP

How network engineers and AI assistants will use network-mcp in production. Each section describes the workflow, tool sequences, data expectations, gaps, and likely error scenarios.

---

## 1. Day-1 Provisioning Workflows

### Scenario: Provision a new leaf switch into an existing fabric

**Tool sequence:**
1. `net_containerlab_discover` — discover if device is visible in the lab/environment
2. `net_get_device_info` / `net_get_facts` — verify device is reachable, collect hardware model, software version
3. `net_get_running_config` — inspect factory defaults or ZTP config
4. `net_configure_interface_description` — label management, uplink, and host-facing interfaces
5. `net_create_vlan` (×N) — provision required VLANs (server, management, storage, etc.)
6. `net_get_vlans` — verify VLANs were created correctly
7. `net_get_bgp_summary` — verify BGP sessions establish to spines after config push
8. `net_get_lldp_neighbors` — verify physical cabling matches the intended topology

**Data format expectations:**
- AI assistants expect structured JSON with consistent keys across vendors
- VLAN creation should return the created VLAN ID and name for confirmation
- BGP summary should include peer state and prefix counts for health assessment

**Current gaps:**
- No bulk VLAN creation tool — must call `net_create_vlan` N times sequentially
- No hostname/NTP/DNS/AAA configuration tools — base config requires raw `run_config_command`
- No "apply config template" tool that takes a Jinja2 template + variables
- Missing `net_configure_bgp_neighbor` for routing protocol setup

**Likely errors:**
- VLAN ID conflicts (VLAN already exists) — need idempotent behavior
- BGP sessions stuck in "Active" due to missing route-map or peer-group config
- Interface names differ between vendors (Ethernet1 vs GigabitEthernet0/0)

---

## 2. Day-2 Operations & Troubleshooting

### 2.1 "Why is this interface down?"

**Tool sequence:**
1. `net_get_interfaces` — check `is_up` and `is_enabled` for the interface
2. `net_get_interface_errors` — look for CRC errors, runts, giants (physical layer)
3. `net_get_transceiver_info` — check optical power levels (tx/rx)
4. `net_get_lldp_neighbors` — verify the remote end is connected
5. `net_get_interface_counters` — check for traffic flow (or lack thereof)

**AI decision tree:**
- `is_enabled=false` → interface is admin-down → suggest `net_no_shutdown_interface`
- `is_enabled=true, is_up=false` → physical layer issue → check transceivers, cables
- High CRC errors → bad cable, dirty optic, or speed mismatch
- No LLDP neighbor → cable disconnected or remote end down

### 2.2 "Why can't host A reach host B?"

**Tool sequence:**
1. `net_ping` from source switch to destination IP — basic L3 reachability
2. `net_get_arp_table` — verify ARP entry exists for the destination
3. `net_get_route_table` — verify routing path exists
4. `net_get_mac_table` — verify MAC is learned on expected interface (L2)
5. `net_traceroute` — trace hop-by-hop path
6. `net_get_acls` — check if an ACL is blocking traffic
7. `net_get_interfaces` on each hop — verify interfaces are up along the path

**Current gaps:**
- No tool to correlate MAC ↔ IP ↔ interface in a single call
- No "path trace" tool that queries multiple devices along a route
- `net_get_route_table` limit parameter may truncate results and miss the specific route

### 2.3 "What changed?"

**Tool sequence:**
1. `net_get_config_diff` — compare running vs startup config
2. `net_get_running_config` — inspect current state
3. `net_get_event_monitor` — check for recent configuration change events
4. `net_get_route_changes` — check for route flaps or withdrawals

**Current gaps:**
- No baseline config storage — can only diff running vs startup, not running vs "known good"
- Event monitor tools are EOS-specific and optional
- No timestamp correlation across events

### 2.4 BGP neighbor flapping

**Tool sequence:**
1. `net_get_bgp_summary` — check peer states and uptime (short uptime = recent flap)
2. `net_get_bgp_neighbors` — detailed neighbor info (state, prefixes, description)
3. `net_get_interface_errors` on the BGP-facing interface — physical layer issues
4. `net_get_cpu_usage` — high CPU can cause BGP keepalive timeouts
5. `net_get_route_table protocol=bgp` — check for route churn

**Data needed by AI:**
- BGP uptime (to detect recent flaps)
- Hold-time and keepalive timers
- Number of state transitions (not currently exposed)

### 2.5 MLAG/vPC inconsistency

**Tool sequence:**
1. `net_get_mlag_status` — check MLAG state, peer-link status, consistency
2. `net_get_interfaces` on both MLAG peers — compare interface states
3. `net_get_vlans` on both peers — compare VLAN databases
4. `net_get_stp_status` — check STP root bridge and port states
5. `net_get_port_channels` — verify MLAG port-channel members match

**Current gaps:**
- No cross-device comparison tool (must query each device and diff manually)
- MLAG consistency check details are vendor-specific

---

## 3. Compliance & Audit

### 3.1 CIS benchmark checking

**Tool sequence:**
1. `net_check_cis_compliance` — run CIS benchmarks against a device
2. `net_get_compliance_report` — get formatted report
3. `net_get_acls` — verify management ACLs exist
4. `net_get_aaa_status` — verify AAA is configured (not local-only)
5. `net_get_snmp_config` — verify SNMPv3, no v2c community strings
6. `net_get_ntp_status` — verify NTP is synchronized
7. `net_get_users` — check for unauthorized active sessions

**Current gaps:**
- CIS compliance tools are EOS-focused; need multi-vendor CIS profiles
- No "fleet-wide compliance" tool — must iterate over devices manually
- No compliance history/trending

### 3.2 Change window validation

**Pre-change:**
1. `net_get_device_health` on all affected devices — baseline health
2. `net_get_bgp_summary` — baseline BGP state
3. `net_get_interfaces` — baseline interface states
4. `net_create_checkpoint` — save rollback point

**Change execution:**
5. Apply changes via config tools

**Post-change:**
6. Repeat steps 1-3 — compare against baseline
7. `net_get_config_diff` — verify only intended changes were made

**Rollback (if needed):**
8. `net_restore_checkpoint` — revert to pre-change state

---

## 4. Capacity Planning & Monitoring

### 4.1 Interface utilization

**Tool sequence:**
1. `net_get_interface_counters` — get current byte counts
2. (wait interval)
3. `net_get_interface_counters` — get new byte counts
4. Calculate delta → utilization percentage

**Current gaps:**
- No built-in utilization calculation (requires two samples and math)
- No historical data storage — only point-in-time snapshots
- Prometheus integration exists but requires external setup

### 4.2 Route table growth

**Tool sequence:**
1. `net_get_route_summary` — total routes by protocol
2. `net_get_route_table` — detailed route entries (limited to 100 by default)

**Current gaps:**
- Route table limit (100 default) may miss growth trends
- No historical route count tracking

### 4.3 Fleet health dashboard

**Tool sequence (per device):**
1. `net_get_cpu_usage` — CPU utilization
2. `net_get_memory_usage` — memory utilization
3. `net_get_environment_temp` — temperature readings
4. `net_get_device_health` — composite health score

**AI workflow:** Query all devices in inventory, aggregate results, flag outliers.

---

## 5. Multi-Vendor Brownfield Networks

### Challenge: Mixed Arista/Cisco/Juniper fabric

**What works well:**
- Common tools (`net_*` namespace) abstract vendor differences for the 35 normalized getters
- LLDP topology discovery works across vendor boundaries
- Interface status, routing tables, and ARP tables are normalized

**What doesn't work well:**
- Config syntax is completely different (EOS CLI vs IOS-XE vs JunOS set-style)
- Write operations use vendor-specific commands under the hood
- Error messages vary by vendor (same failure, different error text)
- Feature gaps differ by platform (e.g., CoPP supported on EOS/NX-OS but not IOS-XE/JunOS)

**Recommendations:**
- Config template engine (`templates.py`) should be the primary write path for multi-vendor
- `not_supported` responses now include `supported_platforms` list (Part 1.4 fix)
- AI assistants should check `capabilities()` before attempting vendor-specific operations

---

## 6. Scale Considerations

| Scenario | Expected Behavior | Current Limitation |
|----------|------------------|--------------------|
| 50-device inventory | Discovery/listing < 5s | Connection pool auto-tunes to min(N×2, 500) |
| 1000 VLANs | `net_get_vlans` returns all | Output sanitizer may truncate at size limit |
| 100k+ routes | `net_get_route_table` limited to 100 | Must increase `limit` parameter; risk of context overflow |
| 20-device LLDP mesh | Topology builder queries sequentially | No parallel LLDP collection; O(N) latency |
| Concurrent tool calls | Per-host locking serializes same-device access | Different devices fully parallel |

### Recommendations for scale:
1. Add parallel LLDP collection to `net_build_topology_from_lldp` using ThreadPoolExecutor
2. Implement streaming/pagination for large route tables and MAC tables
3. Add fleet-wide tool variants that batch queries across devices
4. Consider async driver support for high-concurrency scenarios
