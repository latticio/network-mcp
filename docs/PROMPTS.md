# Prompt Reference

> MCP Prompt templates for network-mcp v4.0.0.

Prompts are reusable workflow templates that guide the AI through multi-step network operations. They provide structured, step-by-step instructions for common tasks.

## Available Prompts

### 1. `troubleshoot_interface`

**Step-by-step interface troubleshooting workflow.**

Parameters: `host: str`, `interface: str`

Guides through: interface status, error counters, transceiver levels, LLDP neighbors, and log review. Uses `eos_*` tools for Arista EOS devices.

> **Quick alternative:** Call `eos_troubleshoot_interface_issues(host, interface)` for an automated diagnosis that runs all these checks in a single operation. For non-EOS devices, use `net_troubleshoot_interface(host, interface)`.

```
Use prompt: troubleshoot_interface(host="leaf-01", interface="Ethernet1")
```

---

### 2. `bgp_health_check`

**BGP health check workflow.**

Parameters: `host: str`

Guides through: BGP summary, identifying down neighbors, detailed neighbor info, route table size, and NTP verification. Uses `eos_*` tools for Arista EOS devices.

> **Quick alternative:** Call `eos_diagnose_bgp_session(host, neighbor_ip)` for automated diagnosis of a specific BGP peer. For non-EOS devices, use `net_diagnose_bgp_session(host, neighbor_ip)`.

```
Use prompt: bgp_health_check(host="spine-01")
```

---

### 3. `pre_change_audit`

**Pre-change state capture for change management documentation.**

Parameters: `host: str`

Captures: device info, interface status, BGP neighbor states, MLAG status, and running config backup.

> **Quick alternative:** Call `eos_pre_change_snapshot(host)` to capture state programmatically, then `eos_post_change_validation(host, snapshot_id)` after changes.

```
Use prompt: pre_change_audit(host="leaf-01")
```

---

### 4. `daily_health_check`

**Comprehensive daily operational health check.**

Parameters: `host: str`

A thorough 10-step check covering: device info, CPU/memory, environment (temp/fans/power), interface status, interface errors, BGP peers, MLAG status, NTP sync, unsaved config changes, and recent logs.

> **Quick alternative:** Call `eos_validate_fabric_health(role='all')` for a quick fabric-wide health score, or `net_validate_fabric(role='all')` for multi-vendor environments.

```
Use prompt: daily_health_check(host="spine-01")
```

---

### 5. `post_change_validation`

**Post-change validation to compare against pre-change baseline.**

Parameters: `host: str`

Validates: device reachability, interface status, new errors, BGP neighbors, route counts, MLAG status, NTP sync, and recent logs. Reports PASS/FAIL for each check.

> **Quick alternative:** Call `eos_post_change_validation(host, snapshot_id)` for automated comparison against a pre-change snapshot.

```
Use prompt: post_change_validation(host="leaf-01")
```

---

### 6. `security_audit`

**Security baseline compliance audit.**

Parameters: `host: str`

Audits: eAPI access, AAA settings, RADIUS/TACACS+ servers, ACLs, CoPP policy, user sessions, security config sections, and NTP. Reports against security best practices.

> **Note:** Requires the `security` module. If `security` is in `NET_DISABLED_MODULES`, the referenced tools will not be available. For cross-vendor compliance, use `eos_compliance_check(host, framework='cis-eos')` or the IOS-XE/NX-OS frameworks.

```
Use prompt: security_audit(host="spine-01")
```

---

### 7. `mlag_troubleshoot`

**MLAG troubleshooting and consistency verification.**

Parameters: `host: str`

Guides through: MLAG domain status, config-sanity checks, per-interface MLAG status, peer-link port-channel, LLDP verification, spanning tree topology, and related logs. Arista EOS-specific (MLAG is an EOS feature).

```
Use prompt: mlag_troubleshoot(host="leaf-01")
```

---

### 8. `safe_config_change`

**Guided workflow for making safe configuration changes with session-based review.**

Parameters: `host: str`, `change_description: str`

A 7-step workflow with risk classification:
1. Classify change risk (LOW/MEDIUM/HIGH/CRITICAL)
2. Create checkpoint (MEDIUM+)
3. Create config session and stage commands
4. Review diff and wait for user approval
5. Apply with appropriate safety (timer for HIGH/CRITICAL)
6. Post-commit verification
7. Rollback procedure if needed

Uses Arista EOS config sessions (`eos_*` tools). Requires `NET_READ_ONLY=false`.

```
Use prompt: safe_config_change(host="leaf-01", change_description="Add VLAN 100 for servers")
```

---

### 9. `evpn_vxlan_health_check`

**Full EVPN/VXLAN fabric validation.**

Parameters: `host: str`

A comprehensive 10-step check covering: VXLAN interface, remote VTEPs, VNI mappings, config-sanity, BGP EVPN peers, EVPN instances, Type-2 MAC/IP routes, Type-3 IMET routes, Virtual ARP, and underlay BGP. Arista EOS-specific.

> **Note:** Requires the `evpn_vxlan` module. If `evpn_vxlan` is in `NET_DISABLED_MODULES`, the referenced tools will not be available.

```
Use prompt: evpn_vxlan_health_check(host="leaf-01")
```

## Prompts vs Workflow Tools

| Feature | Prompts | Workflow Tools |
|---------|---------|----------------|
| Execution | Interactive, step-by-step | Single automated call |
| Control | User guides each step | Tool runs all steps |
| Output | Multiple tool calls with commentary | Single structured result |
| Best for | Detailed investigation, learning | Quick diagnosis, automation |

Use **prompts** when you want interactive, educational walkthroughs.
Use **workflow tools** when you want fast, automated diagnosis.

### Multi-Vendor Alternatives

Most EOS-specific prompts have vendor-agnostic workflow tool alternatives:

| Prompt | EOS Workflow Tool | Multi-Vendor Alternative |
|--------|-------------------|--------------------------|
| `troubleshoot_interface` | `eos_troubleshoot_interface_issues` | `net_troubleshoot_interface` |
| `bgp_health_check` | `eos_diagnose_bgp_session` | `net_diagnose_bgp_session` |
| `daily_health_check` | `eos_validate_fabric_health` | `net_validate_fabric` |
