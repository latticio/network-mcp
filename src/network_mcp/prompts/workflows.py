"""Reusable MCP prompt templates for common network workflows."""

from typing import Literal

from network_mcp.server import mcp


@mcp.prompt()
def troubleshoot_interface(host: str, interface: str) -> str:
    """Step-by-step interface troubleshooting workflow for Arista EOS devices."""
    return f"""Please troubleshoot interface {interface} on device {host}. Follow these steps:

> **Quick option:** For a quick automated diagnosis, call
> eos_troubleshoot_interface_issues(host='{host}', interface='{interface}')
> directly. For a vendor-agnostic approach, use
> net_troubleshoot_interface(host='{host}', interface='{interface}').
> Use this prompt for deeper interactive investigation on EOS devices.

1. Check the interface status using eos_get_interface_detail
2. Look for errors using eos_get_interface_errors
3. Check transceiver levels using eos_get_transceiver_info
4. Check LLDP neighbor on that port using eos_get_lldp_neighbor_detail
5. Review recent logs for that interface using eos_get_logging

Provide a summary of findings and any recommended actions."""


@mcp.prompt()
def bgp_health_check(host: str) -> str:
    """BGP health check workflow for Arista EOS devices."""
    return f"""Perform a BGP health check on {host}:

> **Quick option:** For a quick automated diagnosis of a specific BGP peer,
> call eos_diagnose_bgp_session(host='{host}', neighbor_ip='<peer_ip>').
> For a vendor-agnostic BGP summary, use net_get_bgp_summary(host='{host}').
> Use this prompt for a comprehensive review of all BGP peers on EOS devices.

1. Get BGP summary to check neighbor states using eos_get_bgp_summary
2. Identify any neighbors not in Established state
3. For any down neighbors, get detailed info using eos_get_bgp_neighbors
4. Check the route table size using eos_get_route_summary
5. Verify NTP sync (BGP needs accurate time) using eos_get_ntp_status

Report: total neighbors, established count, down neighbors with reasons, and route count."""


@mcp.prompt()
def pre_change_audit(host: str) -> str:
    """Pre-change state capture for change management on Arista EOS devices."""
    return f"""Capture pre-change state on {host} for change management documentation:

> **Quick option:** Call eos_pre_change_snapshot(host='{host}') to capture
> state, then eos_post_change_validation(host='{host}', snapshot_id='<id>')
> after changes. For a vendor-agnostic baseline, use the net_pre_change_audit prompt.
> Use this prompt for a detailed manual capture on EOS devices.

1. Device info and version using eos_get_device_info
2. Interface status snapshot using eos_get_interfaces_status
3. BGP neighbor states using eos_get_bgp_summary
4. MLAG status (if applicable) using eos_get_mlag_status
5. Running config backup using eos_get_running_config

Store all outputs — these will be compared against post-change state."""


@mcp.prompt()
def daily_health_check(host: str) -> str:
    """Comprehensive daily operational health check workflow for Arista EOS devices."""
    return f"""Perform a comprehensive daily health check on {host}:

> **Quick option:** For a quick fabric-wide health score, call
> eos_validate_fabric_health(role='all') (EOS) or
> net_validate_fabric_health(role='all') (any vendor). Use this prompt for a
> thorough single-device health audit on EOS devices.

1. Get device info and uptime using eos_get_device_info
2. Check CPU and memory utilization using eos_get_cpu_usage and eos_get_memory_usage
3. Check environment (temperature, fans, power) using eos_get_device_health
4. Review interface status — look for unexpected down ports using eos_get_interfaces_status
5. Check interface errors — look for incrementing CRC/input errors using eos_get_interface_errors
6. Check BGP neighbor states using eos_get_bgp_summary — flag any non-Established peers
7. Check MLAG status (if applicable) using eos_get_mlag_status
8. Check NTP synchronization using eos_get_ntp_status
9. Check for unsaved config changes using eos_diff_running_startup
10. Review last 50 log entries for warnings/errors using eos_get_logging

Provide a health summary with:
- Overall status: HEALTHY / WARNING / CRITICAL
- Any items requiring attention
- Recommended actions for any issues found"""


@mcp.prompt()
def post_change_validation(host: str) -> str:
    """Post-change validation to compare against pre-change baseline on Arista EOS devices."""
    return f"""Perform post-change validation on {host}. Compare current state against expected state:

> **Quick option:** If you captured a pre-change snapshot, call
> eos_post_change_validation(host='{host}', snapshot_id='<id>') for
> automated comparison. Use this prompt for thorough manual validation.

1. Verify device is reachable and responding using eos_get_device_info
2. Check interface status — compare against expected using eos_get_interfaces_status
3. Check for new interface errors since the change using eos_get_interface_errors
4. Verify BGP neighbors are all Established using eos_get_bgp_summary
5. Verify route count is within expected range using eos_get_route_summary
6. Check MLAG is active with no config-sanity issues using eos_get_mlag_status and eos_get_mlag_config_sanity
7. Check NTP is still synchronized using eos_get_ntp_status
8. Review logs since the change window for errors using eos_get_logging

Report:
- PASS/FAIL for each check
- Any deviations from expected state
- Recommendation: proceed, rollback, or investigate"""


@mcp.prompt()
def security_audit(host: str) -> str:
    """Security baseline compliance audit workflow for Arista EOS devices.

    Note: This prompt requires the 'security' module. If 'security' is listed in
    NET_DISABLED_MODULES, the referenced tools will not be available.
    """
    return f"""Perform a security audit on {host}:

1. Check eAPI access configuration using eos_get_eapi_status
2. Review AAA settings (authentication methods) using eos_get_aaa_status
3. Check RADIUS server configuration and reachability using eos_get_radius_status
4. Check TACACS+ server configuration and reachability using eos_get_tacacs_status
5. Review access control lists using eos_get_ip_access_lists
6. Check control-plane policing using eos_get_copp_policy
7. Review logged-in users and sessions using eos_get_users
8. Check running-config for security concerns using eos_get_running_config_section with section 'aaa'
9. Check NTP configuration (time accuracy for audit logs) using eos_get_ntp_status

Report findings against security best practices:
- Authentication: local-only vs AAA server backed
- eAPI: HTTP vs HTTPS, access restrictions
- CoPP: rate limits configured for control plane protection
- ACLs: management access restrictions in place
- Flag any security concerns or non-compliance"""


@mcp.prompt()
def mlag_troubleshoot(host: str) -> str:
    """MLAG troubleshooting and consistency verification workflow for Arista EOS devices."""
    return f"""Troubleshoot MLAG on {host}:

1. Check MLAG domain status and peer link using eos_get_mlag_status
2. Run config-sanity to find consistency issues using eos_get_mlag_config_sanity
3. Check per-interface MLAG status using eos_get_mlag_interfaces
4. Check the peer-link port-channel using eos_get_port_channels
5. Check LLDP to verify physical connectivity to MLAG peer using eos_get_lldp_neighbors
6. Check spanning tree for any topology issues using eos_get_spanning_tree
7. Review logs for MLAG-related events using eos_get_logging

Diagnose:
- Is the MLAG peer reachable? (peer link status)
- Are configs consistent? (config-sanity results)
- Are any MLAG interfaces in error state?
- Any split-brain indicators?

Provide diagnosis and remediation steps."""


@mcp.prompt()
def safe_config_change(host: str, change_description: str) -> str:
    """Guided workflow for making safe configuration changes with session-based review on Arista EOS devices."""
    return f"""Make the following change to {host}: {change_description}

Follow this safe change workflow with risk-appropriate safety measures:

> **Tip:** Before starting, call eos_pre_change_snapshot(host='{host}') to
> capture a baseline. After the change, call
> eos_post_change_validation(host='{host}', snapshot_id='<id>') for
> automated regression detection.

**Step 1: Classify the change risk level**
- LOW risk (description change, VLAN name): Config session only
- MEDIUM risk (VLAN creation, interface shutdown): Session + checkpoint
- HIGH risk (IP change, ACL, BGP changes): Session + checkpoint + commit timer
- CRITICAL risk (routing protocol, MLAG, config replace): Session + checkpoint + timer + validation

**Step 2: Create a pre-change checkpoint (MEDIUM risk and above)**
- Call eos_create_checkpoint on {host} with a descriptive name like 'pre-change-<timestamp>'

**Step 3: Create a config session and stage commands**
- Call eos_create_config_session on {host}
- Call eos_add_to_config_session with the required configuration commands

**Step 4: Review the diff — STOP and wait for user approval**
- Call eos_get_config_session_diff to see exactly what will change
- Show me the diff and explain the impact
- DO NOT proceed without my explicit approval

**Step 5: Apply changes based on risk level**
- If I approve and risk is LOW/MEDIUM: call eos_commit_config_session
- If I approve and risk is HIGH/CRITICAL: call eos_commit_config_session_timer (default 5 min)
- If I decline: call eos_abort_config_session

**Step 6: Post-commit verification (if committed)**
- Verify the change took effect by checking relevant device state
- If committed with timer and verification passes: call eos_confirm_commit_timer
- If committed with timer and verification fails: let the timer expire (auto-rollback)

**Step 7: If something went wrong**
- Use eos_rollback_to_checkpoint to restore the pre-change state

Always show me the diff before committing. Never commit without my explicit approval."""


@mcp.prompt()
def evpn_vxlan_health_check(host: str) -> str:
    """EVPN/VXLAN fabric health check workflow for Arista EOS devices.

    Note: This prompt requires the 'evpn_vxlan' module. If 'evpn_vxlan' is listed in
    NET_DISABLED_MODULES, the referenced tools will not be available.
    """
    return f"""Perform an EVPN/VXLAN fabric health check on {host}:

1. Check VXLAN interface status using eos_get_vxlan_interface
2. Verify remote VTEPs are discovered using eos_get_vxlan_vtep
3. Verify VNI-to-VLAN/VRF mappings using eos_get_vxlan_vni
4. Run VXLAN config-sanity check using eos_get_vxlan_config_sanity
5. Check BGP EVPN peer states using eos_get_bgp_evpn_summary
6. Verify EVPN instances and route targets using eos_get_bgp_evpn_instance
7. Check Type-2 MAC/IP routes are being exchanged using eos_get_evpn_route_type_macip
8. Check Type-3 IMET routes for BUM replication using eos_get_evpn_route_type_imet
9. Check Virtual ARP configuration using eos_get_varp_status
10. Check underlay BGP peers using eos_get_bgp_summary

Report:
- Underlay health: BGP peers, ECMP paths
- Overlay health: EVPN peers, route types present
- VXLAN data plane: VTEPs, VNI mappings, config-sanity
- Any issues requiring attention"""


@mcp.prompt()
def net_troubleshoot_interface(host: str, interface: str) -> str:
    """Multi-vendor interface troubleshooting workflow using common tools."""
    return f"""Troubleshoot interface {interface} on {host} using vendor-agnostic tools:

1. Get device info: `net_get_device_info(host="{host}")`
2. Get interface list: `net_get_interfaces(host="{host}")`
3. Get interface detail: `net_get_interface_detail(host="{host}", interface="{interface}")`
4. Check LLDP neighbors: `net_get_lldp_neighbors(host="{host}")`

Analyze:
- Is the interface up/down? Check admin status vs operational status.
- Are there CRC errors, input/output errors, or drops?
- What device is connected (LLDP neighbor)?
- Is speed/duplex negotiation correct?
- What is the vendor/platform? Are there vendor-specific considerations?
"""


@mcp.prompt()
def net_bgp_health_check(host: str) -> str:
    """Multi-vendor BGP health check using common tools."""
    return f"""Check BGP health on {host} using vendor-agnostic tools:

1. Get device info: `net_get_device_info(host="{host}")`
2. Get BGP summary: `net_get_bgp_summary(host="{host}")`

Analyze:
- Are all expected BGP peers established?
- Are peers receiving expected prefix counts?
- Are there any peers in Idle/Active/Connect state?
- How long have peers been established (uptime)?
- What is the vendor/platform? Note any vendor-specific BGP behaviors.

For deeper analysis, use `net_diagnose_bgp_session(host="{host}", peer="<peer-ip>")`.
"""


@mcp.prompt()
def net_pre_change_audit(host: str) -> str:
    """Multi-vendor pre-change audit using common tools."""
    return f"""Perform a pre-change audit on {host} to capture baseline state:

1. Get device info: `net_get_device_info(host="{host}")`
2. Get all interfaces: `net_get_interfaces(host="{host}")`
3. Get BGP summary: `net_get_bgp_summary(host="{host}")`
4. Get LLDP neighbors: `net_get_lldp_neighbors(host="{host}")`

Document the following baseline:
- Device vendor, model, OS version, uptime
- Total interfaces: up/down count
- BGP peers: established/total, prefix counts
- LLDP neighbors: connected devices and interfaces

Save this baseline for comparison after the change window.
"""


@mcp.prompt()
def incident_response(host: str, severity: str = "P2") -> str:
    """Device-down incident response workflow with severity-based escalation.

    Guides the AI through systematic device diagnostics and provides
    escalation recommendations based on incident severity.
    """
    escalation_guidance = {
        "P1": (
            "**P1 — CRITICAL**: Immediate escalation required.\n"
            "- Page on-call network engineer immediately\n"
            "- Open a bridge call with NOC and engineering\n"
            "- Notify management within 15 minutes\n"
            "- Target resolution: 30 minutes"
        ),
        "P2": (
            "**P2 — HIGH**: Escalate if not resolved within 30 minutes.\n"
            "- Notify on-call network engineer\n"
            "- Open incident ticket with detailed diagnostics\n"
            "- Target resolution: 2 hours"
        ),
        "P3": (
            "**P3 — MEDIUM**: Standard troubleshooting workflow.\n"
            "- Open incident ticket\n"
            "- Investigate during business hours\n"
            "- Target resolution: 8 hours"
        ),
        "P4": (
            "**P4 — LOW**: Informational, no immediate action required.\n"
            "- Log in ticket system for tracking\n"
            "- Address during next maintenance window\n"
            "- Target resolution: 5 business days"
        ),
    }
    escalation = escalation_guidance.get(
        severity.upper(),
        f"**{severity}**: Unknown severity. Treat as P2 and follow standard escalation.",
    )
    return f"""Incident Response for device {host} (Severity: {severity})

## Escalation Guidance

{escalation}

## Diagnostic Steps

Perform the following checks systematically. Use vendor-agnostic tools where possible.

### 1. Verify device reachability
- Ping the device: `net_ping(host="{host}", destination="{host}")`
  (If the device is completely unreachable, skip to Step 7 for escalation.)

### 2. Check device health
- Get device info and uptime: `net_get_device_info(host="{host}")`
- Check overall health: `net_get_device_health(host="{host}")`

### 3. Check interface status
- List all interfaces: `net_get_interfaces(host="{host}")`
- Look for unexpected down interfaces or error counters
- Check interface errors: `net_get_interface_errors(host="{host}")`

### 4. Check routing protocol neighbors
- BGP summary: `net_get_bgp_summary(host="{host}")`
- Check for any peers not in Established state
- If OSPF is used, check OSPF neighbors

### 5. Check recent log events
- Review system logs for errors and warnings
- Look for interface flaps, BGP state changes, hardware errors

### 6. Check MLAG status (if applicable)
- Verify MLAG peer link and domain status
- Check for split-brain conditions

### 7. Check environment (power, temperature, fans)
- Verify power supplies, fan status, and temperatures
- Look for hardware alerts or environmental warnings

## Summary

Provide a structured incident summary:
- **Device**: {host}
- **Severity**: {severity}
- **Root cause** (if identified): ...
- **Impact**: Which services/users are affected
- **Remediation steps**: Ordered list of recommended actions
- **Escalation needed**: Yes/No with justification
"""


@mcp.prompt()
def capacity_planning(
    host: str = "",
    resource_type: Literal["cpu", "memory", "interfaces", "all"] = "all",
) -> str:
    """Resource utilization review for capacity planning.

    Guides the AI through checking CPU, memory, interface utilization,
    and TCAM usage to identify devices approaching capacity thresholds.
    """
    host_clause = f'host="{host}"' if host else ""
    scope = f"device **{host}**" if host else "**all devices in the inventory**"

    sections = []

    if resource_type in ("cpu", "all"):
        sections.append(f"""### CPU Utilization
- Check CPU usage: `net_get_cpu_usage({host_clause})`
- Thresholds: WARNING > 70% sustained, CRITICAL > 90%
- Look for processes consuming excessive CPU
- Compare against historical baseline if available""")

    if resource_type in ("memory", "all"):
        sections.append(f"""### Memory Utilization
- Check memory usage: `net_get_memory_usage({host_clause})`
- Thresholds: WARNING > 75% used, CRITICAL > 90%
- Identify memory-intensive features (large routing tables, ACLs, MAC tables)
- Check for memory leaks (gradual increase over time)""")

    if resource_type in ("interfaces", "all"):
        sections.append(f"""### Interface Utilization
- Check interface status and counters: `net_get_interfaces({host_clause})`
- Check interface errors: `net_get_interface_errors({host_clause})`
- Thresholds: WARNING > 70% bandwidth utilization, CRITICAL > 90%
- Identify interfaces with incrementing error counters
- Check for interfaces approaching maximum throughput
- Review transceiver levels for optical interfaces""")

    if resource_type == "all":
        sections.append(f"""### TCAM Utilization (EOS specific)
- Check TCAM usage: `eos_get_tcam_usage({host_clause})`
- Thresholds: WARNING > 75% of any TCAM region, CRITICAL > 90%
- Identify which features are consuming TCAM (ACLs, routes, MAC entries)""")

    checks = "\n\n".join(sections)

    return f"""Capacity Planning Review for {scope}

## Resource Type: {resource_type}

Review resource utilization and identify devices approaching capacity limits.

## Checks

{checks}

## Analysis

After gathering data, provide:

1. **Utilization Summary Table**
   | Device | CPU % | Memory % | Interface Peak % | Status |
   |--------|-------|----------|-------------------|--------|
   | ...    | ...   | ...      | ...               | OK/WARN/CRIT |

2. **Devices Approaching Thresholds**
   - List any device/resource combinations above WARNING thresholds
   - Estimate time to capacity exhaustion based on growth trends

3. **Scaling Recommendations**
   - Hardware upgrades needed (memory, line cards, optics)
   - Configuration optimizations (route summarization, ACL consolidation)
   - Architecture changes (add spine/leaf, redistribute traffic)
   - Timeline for recommended actions
"""


@mcp.prompt()
def net_security_audit(
    host: str = "",
    standard: Literal["cis", "nist", "custom"] = "cis",
) -> str:
    """Fleet-wide security compliance audit using vendor-agnostic tools.

    Guides the AI through a comprehensive security review covering AAA,
    ACLs, SNMP, NTP, control plane policing, and user accounts.
    """
    host_clause = f'host="{host}"' if host else ""
    scope = f"device **{host}**" if host else "**all devices in the inventory**"

    standard_descriptions = {
        "cis": (
            "**CIS Benchmarks** — Center for Internet Security best practices for network devices. "
            "Covers authentication, access control, logging, and management plane hardening."
        ),
        "nist": (
            "**NIST SP 800-53** — Federal security controls applicable to network infrastructure. "
            "Covers access control (AC), audit (AU), identification and authentication (IA), "
            "and system and communications protection (SC)."
        ),
        "custom": (
            "**Custom Standard** — Organization-specific security requirements. "
            "Adapt the checks below to match your internal security policy."
        ),
    }
    standard_desc = standard_descriptions.get(standard, standard_descriptions["cis"])

    return f"""Security Compliance Audit for {scope}

## Compliance Standard

{standard_desc}

## Audit Checks

Perform the following checks using vendor-agnostic tools where possible.

### 1. AAA Configuration
- Check AAA settings: `net_get_aaa_status({host_clause})`
- Verify RADIUS/TACACS+ servers are configured and reachable
- Confirm that local-only authentication is not used in production
- Check for fallback authentication methods

### 2. Access Control Lists
- Review ACLs: `net_get_acls({host_clause})`
- Verify management access ACLs restrict source IPs
- Check for overly permissive rules (permit any any)
- Verify ACLs are applied to management interfaces (VTY, console)

### 3. SNMP Community Strings
- Check SNMP configuration: `net_get_snmp_config({host_clause})`
- Verify no default community strings (public, private)
- Confirm SNMPv3 is used where possible
- Check SNMP access restrictions (ACLs on SNMP)

### 4. NTP Authentication
- Check NTP status: `net_get_ntp_status({host_clause})`
- Verify NTP servers are configured and synchronized
- Check for NTP authentication (prevents time-source spoofing)
- Confirm time accuracy for audit log integrity

### 5. Control Plane Policing
- Check CoPP policy: `net_get_copp_policy({host_clause})`
- Verify rate limits are configured for control plane traffic
- Check that CoPP is active and not in permissive mode

### 6. User Accounts
- Review local user accounts: `net_get_users({host_clause})`
- Check for default credentials (admin/admin, cisco/cisco)
- Verify password complexity requirements
- Identify accounts with excessive privileges
- Check for unused/stale accounts

### 7. Management Plane Security
- Verify HTTPS is enabled for management access (not HTTP)
- Check SSH version (SSHv2 required, SSHv1 must be disabled)
- Verify console and VTY line security settings

### 8. Running Configuration Review
- Get running config: `net_get_running_config({host_clause})`
- Search for plaintext passwords or secrets
- Verify service password encryption is enabled
- Check for insecure protocol configurations

## Compliance Report

Generate a structured compliance report:

### Summary
| Check | Status | Finding |
|-------|--------|---------|
| AAA | PASS/FAIL | ... |
| ACLs | PASS/FAIL | ... |
| SNMP | PASS/FAIL | ... |
| NTP | PASS/FAIL | ... |
| CoPP | PASS/FAIL | ... |
| Users | PASS/FAIL | ... |
| Mgmt Plane | PASS/FAIL | ... |
| Config Review | PASS/FAIL | ... |

### Risk Assessment
- **Critical findings**: Immediate action required
- **High findings**: Address within 7 days
- **Medium findings**: Address within 30 days
- **Low findings**: Address during next maintenance window

### Remediation Plan
For each finding, provide:
1. Description of the issue
2. Risk if not remediated
3. Recommended fix (with example commands if applicable)
4. Verification steps after remediation
"""
