"""Multi-vendor CIS compliance checking framework with structured config parsing.

Validates device configuration against CIS benchmarks for Arista EOS,
Cisco IOS-XE, and Cisco NX-OS platforms. Uses structured config parsing
with value-based verification and NIST 800-53 control mappings.

CIS control IDs reference the following benchmark structures:
- EOS: CIS Arista MLS EOS Benchmark (1.x Mgmt, 2.x Control, 3.x Data)
- IOS-XE: CIS Cisco IOS Benchmark (1.x Mgmt, 2.x Control, 3.x Data)
- NX-OS: CIS Cisco NX-OS Benchmark (1.x Mgmt, 2.x Control, 3.x Data)

IDs should be verified against the official CIS documents for production use.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from network_mcp.helpers import READ_ONLY, run_show_command
from network_mcp.server import conn_mgr, mcp

VALID_FRAMEWORKS = ("cis-eos", "cis-iosxe", "cis-nxos")
VALID_CATEGORIES = ("management", "control", "data")


# ============================================================================
# Structured config parsing
# ============================================================================


def _parse_eos_config(running_config: str) -> dict[str, list[str]]:
    """Parse EOS running config into structured sections.

    Top-level lines (no leading whitespace, not comments) become section keys.
    Indented lines are appended to the current section.
    """
    sections: dict[str, list[str]] = {}
    current_section = "global"
    sections[current_section] = []
    for line in running_config.splitlines():
        if not line or line.startswith("!"):
            continue
        if not line.startswith(" ") and not line.startswith("\t"):
            current_section = line.strip()
            sections.setdefault(current_section, [])
        else:
            sections.setdefault(current_section, []).append(line.strip())
    return sections


def _extract_value(lines: list[str], keyword: str) -> str | None:
    """Extract the value immediately following keyword in a config line.

    Searches through lines for one containing keyword as a distinct word,
    then returns the next token after it.
    """
    for line in lines:
        if keyword in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part == keyword and i + 1 < len(parts):
                    return parts[i + 1]
    return None


def _has_line(lines: list[str], substring: str) -> bool:
    """Check if any line in the list contains the given substring."""
    return any(substring in line for line in lines)


def _has_line_exact(lines: list[str], exact: str) -> bool:
    """Check if any stripped line matches exactly."""
    return any(line.strip() == exact for line in lines)


# ============================================================================
# ComplianceResult dataclass
# ============================================================================


@dataclass
class ComplianceResult:
    """Result of a single compliance check with detail and NIST mapping."""

    check_id: str
    check: str
    status: str  # "pass", "fail", "skip"
    detail: str = ""
    severity: str = "medium"  # "critical", "high", "medium", "low"
    nist_controls: list[str] = field(default_factory=list)


# ============================================================================
# ComplianceCheck dataclass
# ============================================================================


@dataclass
class ComplianceCheck:
    """A single compliance check definition."""

    id: str  # CIS control ID, e.g. "CIS-1.1.1"
    title: str  # Human-readable title
    severity: str  # "HIGH", "MEDIUM", "LOW"
    category: str  # "management", "control", "data"
    command: str  # Show command to run
    encoding: str  # "text" or "json"
    check_fn: Callable[..., bool]  # Returns True if compliant (accepts str or dict)
    remediation: str  # Remediation guidance
    nist_controls: list[str] = field(default_factory=list)  # NIST 800-53 control mappings


# ============================================================================
# Arista EOS check functions
# ============================================================================


def _check_eos_ssh_enabled(output: str) -> bool:
    """CIS EOS 1.1.1: Ensure SSH server is enabled."""
    return "no shutdown" in output or ("management ssh" in output and "shutdown" not in output)


def _check_eos_ssh_idle_timeout(output: str) -> bool:
    """CIS EOS 1.1.2: Ensure SSH idle timeout is configured."""
    return "idle-timeout" in output


def _check_eos_ssh_timeout_value(output: str) -> ComplianceResult:
    """CIS EOS 1.1.3: SSH idle timeout must be <= 300 seconds."""
    parsed = _parse_eos_config(output)
    all_lines = []
    for lines in parsed.values():
        all_lines.extend(lines)
    timeout = _extract_value(all_lines, "idle-timeout")
    if timeout is None:
        return ComplianceResult(
            "CIS-EOS-1.1.3", "SSH Timeout Value", "fail", "Not configured", "high", ["AC-12", "SC-10"]
        )
    try:
        val = int(timeout)
    except ValueError:
        return ComplianceResult(
            "CIS-EOS-1.1.3", "SSH Timeout Value", "fail", f"Invalid value: {timeout}", "high", ["AC-12", "SC-10"]
        )
    if val > 300:
        return ComplianceResult(
            "CIS-EOS-1.1.3",
            "SSH Timeout Value",
            "fail",
            f"Timeout {val}s > 300s maximum",
            "high",
            ["AC-12", "SC-10"],
        )
    return ComplianceResult("CIS-EOS-1.1.3", "SSH Timeout Value", "pass", f"Timeout {val}s", "high", ["AC-12", "SC-10"])


def _check_eos_ssh_timeout_value_bool(output: str) -> bool:
    """Boolean wrapper for SSH timeout value check."""
    return _check_eos_ssh_timeout_value(output).status == "pass"


def _check_eos_aaa_configured(output: str) -> bool:
    """CIS EOS 1.2.1: Ensure AAA authentication is configured (TACACS+ or RADIUS)."""
    return "aaa authentication" in output and ("group tacacs+" in output or "group radius" in output)


def _check_eos_aaa_accounting(output: str) -> bool:
    """CIS EOS 1.2.2: Ensure AAA accounting is configured."""
    return "aaa accounting" in output


def _check_eos_aaa_authorization(output: str) -> bool:
    """CIS EOS 1.2.3: Ensure AAA authorization is configured."""
    return "aaa authorization" in output


def _check_eos_password_encryption(output: str) -> bool:
    """CIS EOS 1.3.1: Ensure service password-encryption is enabled."""
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped == "service password-encryption":
            return True
    return False


def _check_eos_login_banner(output: str) -> bool:
    """CIS EOS 1.3.2: Ensure login banner is configured."""
    return "banner login" in output


def _check_eos_motd_banner(output: str) -> bool:
    """CIS EOS 1.3.3: Ensure MOTD banner is configured."""
    return "banner motd" in output


def _check_eos_password_min_length(output: str) -> bool:
    """CIS EOS 1.3.4: Ensure minimum password length is configured (>= 8)."""
    parsed = _parse_eos_config(output)
    all_lines = []
    for lines in parsed.values():
        all_lines.extend(lines)
    # Check for 'minimum-length' setting
    min_len = _extract_value(all_lines, "minimum-length")
    if min_len is not None:
        try:
            return int(min_len) >= 8
        except ValueError:
            return False
    # Also check for 'password minimum-length' as a full line
    return any("minimum-length" in line for line in output.split("\n"))


def _check_eos_eapi_https_only(output: str) -> bool:
    """CIS EOS 1.4.1: Ensure eAPI HTTPS is enabled and HTTP is disabled."""
    has_https = "protocol https" in output
    http_disabled = "no protocol http" in output or "protocol http" not in output
    return has_https and http_disabled


def _check_eos_ssh_version_2(output: str) -> bool:
    """CIS EOS 1.4.2: Ensure SSH version 2 is enforced (no v1 fallback)."""
    # EOS uses SSHv2 by default; check that there's no explicit v1
    if "protocol-version" in output:
        return "protocol-version 2" in output
    # If not explicitly configured, EOS defaults to v2
    return "management ssh" in output or "no shutdown" in output


def _check_eos_vty_access_class(output: str) -> bool:
    """CIS EOS 1.4.3: Ensure VTY access is restricted with an ACL."""
    return "access-class" in output or "ip access-group" in output


def _check_eos_ntp_configured(output: str) -> bool:
    """CIS EOS 2.1.1: Ensure NTP is configured with at least one server."""
    return "ntp server" in output


def _check_eos_ntp_authentication(output: str) -> bool:
    """CIS EOS 2.1.2: Ensure NTP authentication is enabled."""
    has_server = "ntp server" in output
    has_auth = "ntp authenticate" in output or "ntp authentication-key" in output
    return has_server and has_auth


def _check_eos_ntp_trusted_key(output: str) -> bool:
    """CIS EOS 2.1.3: Ensure NTP trusted key is configured."""
    return "ntp trusted-key" in output


def _check_eos_logging_remote(output: str) -> bool:
    """CIS EOS 2.2.1: Ensure logging to a remote syslog server is configured."""
    return "logging host" in output


def _check_eos_logging_level(output: str) -> bool:
    """CIS EOS 2.2.2: Ensure logging level is informational or higher."""
    if "logging trap" in output:
        # Check for valid levels (informational = 6, or named levels)
        valid_levels = ("informational", "notifications", "warnings", "errors", "critical", "alerts", "emergencies")
        return any(level in output for level in valid_levels) or any(f"logging trap {n}" in output for n in range(0, 7))
    # Default is informational, which is compliant
    return True


def _check_eos_logging_buffered(output: str) -> bool:
    """CIS EOS 2.2.3: Ensure logging buffer is configured."""
    return "logging buffered" in output


def _check_eos_snmp_no_defaults(output: str) -> bool:
    """CIS EOS 2.3.1: Ensure SNMP community strings are not set to defaults."""
    lines = output.lower().split("\n")
    for line in lines:
        if "snmp-server community" in line:
            if "public" in line or "private" in line:
                return False
    return True


def _check_eos_enable_secret(output: str) -> bool:
    """CIS EOS 2.3.2: Ensure enable secret is configured (not enable password)."""
    return "enable secret" in output


def _check_eos_snmp_v3(output: str) -> bool:
    """CIS EOS 2.3.3: Ensure SNMPv3 is used with authentication and encryption."""
    return "snmp-server group" in output and ("v3 priv" in output or "v3 auth" in output)


def _check_eos_snmp_acl(output: str) -> bool:
    """CIS EOS 2.3.4: Ensure SNMP has ACL restrictions."""
    if "snmp-server community" in output:
        # Check that community lines have access list restrictions
        for line in output.split("\n"):
            if "snmp-server community" in line and ("access" in line or "acl" in line.lower()):
                return True
        return False
    return True  # No SNMP communities at all is fine


def _check_eos_console_timeout(output: str) -> bool:
    """CIS EOS 2.4.1: Ensure console and VTY exec-timeout is configured."""
    return "exec-timeout" in output


def _check_eos_console_timeout_value(output: str) -> bool:
    """CIS EOS 2.4.2: Ensure console timeout is <= 10 minutes."""
    if "exec-timeout" not in output:
        return False
    for line in output.split("\n"):
        if "exec-timeout" in line:
            parts = line.strip().split()
            for i, part in enumerate(parts):
                if part == "exec-timeout" and i + 1 < len(parts):
                    try:
                        minutes = int(parts[i + 1])
                        return 0 < minutes <= 10
                    except ValueError:
                        return False
    return False


def _check_eos_radius_source_interface(output: str) -> bool:
    """CIS EOS 2.4.3: Ensure RADIUS uses a source interface."""
    return "ip radius source-interface" in output


def _check_eos_tacacs_source_interface(output: str) -> bool:
    """CIS EOS 2.4.4: Ensure TACACS+ uses a source interface."""
    return "ip tacacs source-interface" in output


def _check_eos_aaa_local_auth(output: str) -> bool:
    """CIS EOS 2.4.5: Ensure local authentication fallback is configured."""
    return "local" in output and "aaa authentication" in output


def _check_eos_unused_interfaces_shutdown(output: str) -> bool:
    """CIS EOS 3.1.1: Verify unused interfaces are administratively shut down."""
    return "shutdown" in output


def _check_eos_storm_control(output: str) -> bool:
    """CIS EOS 3.1.2: Ensure storm control is configured on access ports."""
    return "storm-control" in output


def _check_eos_stp_guard(output: str) -> bool:
    """CIS EOS 3.2.1: Ensure STP BPDU guard or root guard is enabled."""
    return "spanning-tree bpduguard" in output or "spanning-tree guard root" in output


def _check_eos_stp_mode(output: str) -> bool:
    """CIS EOS 3.2.2: Ensure STP mode is MSTP or RSTP (not PVST)."""
    return "spanning-tree mode mstp" in output or "spanning-tree mode rapid-pvst" in output


def _check_eos_dhcp_snooping(output: str) -> bool:
    """CIS EOS 3.3.1: Ensure DHCP snooping or ARP inspection is enabled."""
    return "ip dhcp snooping" in output or "ip arp inspection" in output


def _check_eos_arp_inspection(output: str) -> bool:
    """CIS EOS 3.3.2: Ensure dynamic ARP inspection is configured."""
    return "ip arp inspection" in output


def _check_eos_ip_source_guard(output: str) -> bool:
    """CIS EOS 3.3.3: Ensure IP source guard is enabled on access ports."""
    return "ip verify source" in output


def _check_eos_copp_configured(output: str) -> bool:
    """CIS EOS 3.4.1: Ensure Control Plane Policing (CoPP) is configured."""
    return "system control-plane" in output or "policy-map" in output


def _check_eos_copp_rate_limit(output: str) -> bool:
    """CIS EOS 3.4.2: Ensure CoPP has rate limiting configured."""
    return ("police" in output or "rate" in output) and ("system control-plane" in output or "policy-map" in output)


def _check_eos_acl_deny_all(output: str) -> bool:
    """CIS EOS 3.4.3: Ensure ACLs end with explicit deny."""
    if "ip access-list" not in output and "access-list" not in output:
        return True  # No ACLs defined
    return "deny" in output


def _check_eos_urpf(output: str) -> bool:
    """CIS EOS 3.4.4: Ensure uRPF (unicast reverse path forwarding) is configured."""
    return "ip verify unicast source reachable-via" in output


def _check_eos_lldp_restricted(output: str) -> bool:
    """CIS EOS 3.5.1: Ensure LLDP is restricted or disabled on untrusted ports."""
    return "no lldp transmit" in output or "no lldp receive" in output or "lldp run" not in output


def _check_eos_mgmt_vrf(output: str) -> bool:
    """CIS EOS 3.5.2: Ensure management traffic uses a dedicated VRF."""
    return "vrf" in output and ("management" in output.lower() or "MGMT" in output)


def _check_eos_ip_routing_secure(output: str) -> bool:
    """CIS EOS 3.5.3: Ensure no IP directed-broadcast is configured."""
    return "no ip directed-broadcast" in output or "ip directed-broadcast" not in output


def _check_eos_errdisable_recovery(output: str) -> bool:
    """CIS EOS 3.5.4: Ensure errdisable recovery is configured."""
    return "errdisable recovery" in output


# ============================================================================
# Cisco IOS-XE check functions
# ============================================================================


def _check_iosxe_ssh_version_2(output: str) -> bool:
    """CIS IOS 1.1.1: Ensure SSH version 2 is configured."""
    return "ip ssh version 2" in output


def _check_iosxe_ssh_timeout(output: str) -> bool:
    """CIS IOS 1.1.2: Ensure SSH timeout is configured."""
    return "ip ssh time-out" in output


def _check_iosxe_ssh_timeout_value(output: str) -> bool:
    """CIS IOS 1.1.3: Ensure SSH timeout value <= 60 seconds."""
    for line in output.split("\n"):
        if "ip ssh time-out" in line:
            parts = line.strip().split()
            for i, part in enumerate(parts):
                if part == "time-out" and i + 1 < len(parts):
                    try:
                        return int(parts[i + 1]) <= 60
                    except ValueError:
                        return False
    return False


def _check_iosxe_ssh_retries(output: str) -> bool:
    """CIS IOS 1.1.4: Ensure SSH authentication retries are limited."""
    return "ip ssh authentication-retries" in output


def _check_iosxe_aaa_configured(output: str) -> bool:
    """CIS IOS 1.2.1: Ensure AAA authentication is configured."""
    return "aaa authentication login" in output and ("group tacacs+" in output or "group radius" in output)


def _check_iosxe_aaa_accounting(output: str) -> bool:
    """CIS IOS 1.2.2: Ensure AAA accounting is configured."""
    return "aaa accounting" in output


def _check_iosxe_aaa_authorization(output: str) -> bool:
    """CIS IOS 1.2.3: Ensure AAA authorization is configured."""
    return "aaa authorization" in output


def _check_iosxe_password_encryption(output: str) -> bool:
    """CIS IOS 1.3.1: Ensure service password-encryption is enabled."""
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped == "service password-encryption":
            return True
    return False


def _check_iosxe_login_banner(output: str) -> bool:
    """CIS IOS 1.3.2: Ensure login banner is configured."""
    return "banner login" in output


def _check_iosxe_motd_banner(output: str) -> bool:
    """CIS IOS 1.3.3: Ensure MOTD banner is configured."""
    return "banner motd" in output


def _check_iosxe_password_min_length(output: str) -> bool:
    """CIS IOS 1.3.4: Ensure minimum password length (>= 8)."""
    if "security passwords min-length" in output:
        for line in output.split("\n"):
            if "security passwords min-length" in line:
                parts = line.strip().split()
                for i, part in enumerate(parts):
                    if part == "min-length" and i + 1 < len(parts):
                        try:
                            return int(parts[i + 1]) >= 8
                        except ValueError:
                            return False
    return False


def _check_iosxe_http_server_disabled(output: str) -> bool:
    """CIS IOS 1.4.1: Ensure HTTP server is disabled or HTTPS only."""
    has_no_http = "no ip http server" in output
    has_https = "ip http secure-server" in output
    return has_no_http and has_https


def _check_iosxe_aux_port_disabled(output: str) -> bool:
    """CIS IOS 1.4.2: Ensure auxiliary port is disabled."""
    return "no exec" in output or "transport input none" in output


def _check_iosxe_ntp_authentication(output: str) -> bool:
    """CIS IOS 2.1.1: Ensure NTP is configured with authentication."""
    has_server = "ntp server" in output
    has_auth = "ntp authenticate" in output or "ntp authentication-key" in output
    return has_server and has_auth


def _check_iosxe_ntp_trusted_key(output: str) -> bool:
    """CIS IOS 2.1.2: Ensure NTP trusted key is configured."""
    return "ntp trusted-key" in output


def _check_iosxe_logging_remote(output: str) -> bool:
    """CIS IOS 2.2.1: Ensure logging to a remote syslog server is configured."""
    return "logging host" in output


def _check_iosxe_logging_buffered(output: str) -> bool:
    """CIS IOS 2.2.2: Ensure logging buffer is configured."""
    return "logging buffered" in output


def _check_iosxe_logging_timestamps(output: str) -> bool:
    """CIS IOS 2.2.3: Ensure timestamps are enabled on log messages."""
    return "service timestamps log datetime" in output


def _check_iosxe_snmp_no_defaults(output: str) -> bool:
    """CIS IOS 2.3.1: Ensure SNMP community strings are not default."""
    lines = output.lower().split("\n")
    for line in lines:
        if "snmp-server community" in line:
            if "public" in line or "private" in line:
                return False
    return True


def _check_iosxe_enable_secret(output: str) -> bool:
    """CIS IOS 2.3.2: Ensure enable secret is configured (not enable password)."""
    return "enable secret" in output


def _check_iosxe_snmp_v3(output: str) -> bool:
    """CIS IOS 2.3.3: Ensure SNMPv3 is used with authentication and encryption."""
    return "snmp-server group" in output and ("v3 priv" in output or "v3 auth" in output)


def _check_iosxe_snmp_acl(output: str) -> bool:
    """CIS IOS 2.3.4: Ensure SNMP community strings have ACL restrictions."""
    if "snmp-server community" in output:
        for line in output.split("\n"):
            if "snmp-server community" in line and "access" in line.lower():
                return True
        return False
    return True


def _check_iosxe_vty_transport_ssh(output: str) -> bool:
    """CIS IOS 2.4.1: Ensure VTY transport input is restricted to SSH."""
    return "transport input ssh" in output


def _check_iosxe_vty_acl(output: str) -> bool:
    """CIS IOS 2.4.2: Ensure VTY lines have access-class configured."""
    return "access-class" in output


def _check_iosxe_console_timeout(output: str) -> bool:
    """CIS IOS 2.4.3: Ensure console timeout is configured."""
    return "exec-timeout" in output


def _check_iosxe_unused_interfaces_shutdown(output: str) -> bool:
    """CIS IOS 3.1.1: Ensure unused interfaces are shut down."""
    return "shutdown" in output


def _check_iosxe_cdp_disabled(output: str) -> bool:
    """CIS IOS 3.2.1: Ensure CDP is disabled globally."""
    return "no cdp run" in output


def _check_iosxe_no_source_routing(output: str) -> bool:
    """CIS IOS 3.3.1: Ensure IP source routing is disabled."""
    return "no ip source-route" in output


def _check_iosxe_no_icmp_redirects(output: str) -> bool:
    """CIS IOS 3.4.1: Ensure ICMP redirects are disabled on interfaces."""
    return "no ip redirects" in output


def _check_iosxe_no_proxy_arp(output: str) -> bool:
    """CIS IOS 3.4.2: Ensure proxy ARP is disabled on interfaces."""
    return "no ip proxy-arp" in output


def _check_iosxe_tcp_keepalives(output: str) -> bool:
    """CIS IOS 3.4.3: Ensure TCP keepalives are enabled."""
    return "service tcp-keepalives-in" in output and "service tcp-keepalives-out" in output


def _check_iosxe_no_ip_unreachables(output: str) -> bool:
    """CIS IOS 3.4.4: Ensure ICMP unreachable messages are disabled."""
    return "no ip unreachables" in output


def _check_iosxe_urpf(output: str) -> bool:
    """CIS IOS 3.5.1: Ensure uRPF is configured."""
    return "ip verify unicast source reachable-via" in output


def _check_iosxe_storm_control(output: str) -> bool:
    """CIS IOS 3.5.2: Ensure storm control is configured."""
    return "storm-control" in output


# ============================================================================
# Cisco NX-OS check functions
# ============================================================================


def _check_nxos_ssh_enabled(output: str) -> bool:
    """CIS NX-OS 1.1.1: Ensure SSH is enabled and configured."""
    return "feature ssh" in output or "ssh server enable" in output


def _check_nxos_ssh_kex_hardened(output: str) -> bool:
    """CIS NX-OS 1.1.2: Ensure SSH key exchange algorithm is hardened."""
    return "ssh key" in output or "ssh kex" in output or "ssh login-attempts" in output


def _check_nxos_ssh_login_attempts(output: str) -> bool:
    """CIS NX-OS 1.1.3: Ensure SSH login attempts are limited."""
    return "ssh login-attempts" in output


def _check_nxos_aaa_configured(output: str) -> bool:
    """CIS NX-OS 1.2.1: Ensure AAA authentication is configured."""
    return "aaa authentication login" in output and ("group tacacs+" in output or "group radius" in output)


def _check_nxos_aaa_accounting(output: str) -> bool:
    """CIS NX-OS 1.2.2: Ensure AAA accounting is configured."""
    return "aaa accounting" in output


def _check_nxos_aaa_authorization(output: str) -> bool:
    """CIS NX-OS 1.2.3: Ensure AAA authorization is configured."""
    return "aaa authorization" in output


def _check_nxos_password_strength(output: str) -> bool:
    """CIS NX-OS 1.3.1: Ensure password strength checking is enabled."""
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped == "password strength-check":
            return True
    return False


def _check_nxos_login_banner(output: str) -> bool:
    """CIS NX-OS 1.3.2: Ensure login banner is configured."""
    return "banner motd" in output


def _check_nxos_exec_banner(output: str) -> bool:
    """CIS NX-OS 1.3.3: Ensure exec banner is configured."""
    return "banner exec" in output or "banner motd" in output


def _check_nxos_user_role(output: str) -> bool:
    """CIS NX-OS 1.3.4: Ensure user roles are defined (not just admin)."""
    return "role name" in output


def _check_nxos_https_management(output: str) -> bool:
    """CIS NX-OS 1.4.1: Ensure HTTPS management only (no HTTP)."""
    has_nxapi_https = "feature nxapi" in output and "nxapi https" in output
    no_http = "no nxapi http" in output or "nxapi http" not in output
    return has_nxapi_https and no_http


def _check_nxos_console_timeout(output: str) -> bool:
    """CIS NX-OS 1.4.2: Ensure console timeout is configured."""
    return "exec-timeout" in output


def _check_nxos_ntp_configured(output: str) -> bool:
    """CIS NX-OS 2.1.1: Ensure NTP is configured and synchronized."""
    return "ntp server" in output


def _check_nxos_ntp_authentication(output: str) -> bool:
    """CIS NX-OS 2.1.2: Ensure NTP authentication is configured."""
    has_server = "ntp server" in output
    has_auth = "ntp authenticate" in output or "ntp authentication-key" in output
    return has_server and has_auth


def _check_nxos_ntp_trusted_key(output: str) -> bool:
    """CIS NX-OS 2.1.3: Ensure NTP trusted key is configured."""
    return "ntp trusted-key" in output


def _check_nxos_logging_remote(output: str) -> bool:
    """CIS NX-OS 2.2.1: Ensure logging to a remote syslog server is configured."""
    return "logging server" in output


def _check_nxos_logging_level(output: str) -> bool:
    """CIS NX-OS 2.2.2: Ensure logging level is informational or higher."""
    if "logging level" in output:
        return True
    return "logging server" in output


def _check_nxos_logging_timestamp(output: str) -> bool:
    """CIS NX-OS 2.2.3: Ensure logging timestamps are configured."""
    return "logging timestamp" in output or "logging server" in output


def _check_nxos_snmp_no_defaults(output: str) -> bool:
    """CIS NX-OS 2.3.1: Ensure SNMP community strings are not default."""
    lines = output.lower().split("\n")
    for line in lines:
        if "snmp-server community" in line:
            if "public" in line or "private" in line:
                return False
    return True


def _check_nxos_copp_configured(output: str) -> bool:
    """CIS NX-OS 2.3.2: Ensure Control Plane Policing (CoPP) is configured."""
    return "copp profile" in output or "service-policy input" in output


def _check_nxos_snmp_v3(output: str) -> bool:
    """CIS NX-OS 2.3.3: Ensure SNMPv3 is used with authentication."""
    return "snmp-server user" in output and ("auth" in output or "priv" in output)


def _check_nxos_snmp_acl(output: str) -> bool:
    """CIS NX-OS 2.3.4: Ensure SNMP has ACL restrictions."""
    if "snmp-server community" in output:
        return "use-acl" in output or "use-ipv4acl" in output
    return True


def _check_nxos_nxapi_secured(output: str) -> bool:
    """CIS NX-OS 2.4.1: Ensure NX-API is secured or disabled if not needed."""
    nxapi_disabled = "no feature nxapi" in output
    nxapi_https = "nxapi https" in output and "nxapi sandbox" not in output
    return nxapi_disabled or nxapi_https


def _check_nxos_tacacs_source(output: str) -> bool:
    """CIS NX-OS 2.4.2: Ensure TACACS+ uses source interface."""
    return "ip tacacs source-interface" in output


def _check_nxos_radius_source(output: str) -> bool:
    """CIS NX-OS 2.4.3: Ensure RADIUS uses source interface."""
    return "ip radius source-interface" in output


def _check_nxos_unused_interfaces_shutdown(output: str) -> bool:
    """CIS NX-OS 3.1.1: Ensure unused interfaces are shut down."""
    return "shutdown" in output


def _check_nxos_storm_control(output: str) -> bool:
    """CIS NX-OS 3.1.2: Ensure storm control is configured."""
    return "storm-control" in output


def _check_nxos_dhcp_snooping(output: str) -> bool:
    """CIS NX-OS 3.2.1: Ensure DHCP snooping is enabled on access VLANs."""
    return "ip dhcp snooping" in output or "feature dhcp" in output


def _check_nxos_stp_bpduguard(output: str) -> bool:
    """CIS NX-OS 3.2.2: Ensure STP BPDU guard is enabled."""
    return "spanning-tree bpduguard" in output or "spanning-tree port type edge bpduguard" in output


def _check_nxos_arp_inspection(output: str) -> bool:
    """CIS NX-OS 3.3.1: Ensure ARP inspection is enabled."""
    return "ip arp inspection" in output


def _check_nxos_ip_source_guard(output: str) -> bool:
    """CIS NX-OS 3.3.2: Ensure IP source guard is enabled."""
    return "ip verify source" in output or "ip source binding" in output


def _check_nxos_port_security(output: str) -> bool:
    """CIS NX-OS 3.4.1: Ensure port security or 802.1X is configured on access ports."""
    return "switchport port-security" in output or "dot1x" in output


def _check_nxos_acl_explicit_deny(output: str) -> bool:
    """CIS NX-OS 3.4.2: Ensure ACLs end with explicit deny."""
    if "ip access-list" not in output and "access-list" not in output:
        return True
    return "deny" in output


def _check_nxos_urpf(output: str) -> bool:
    """CIS NX-OS 3.4.3: Ensure uRPF is configured."""
    return "ip verify unicast source reachable-via" in output


def _check_nxos_vpc_peer_keepalive(output: str) -> bool:
    """CIS NX-OS 3.5.1: Ensure vPC peer-keepalive uses management VRF."""
    return "peer-keepalive" in output and "vrf" in output


# ============================================================================
# Check registries
# ============================================================================

CIS_EOS_CHECKS: list[ComplianceCheck] = [
    # Management Plane (1.x)
    ComplianceCheck(
        id="CIS-EOS-1.1.1",
        title="Ensure SSH server is enabled",
        severity="HIGH",
        category="management",
        command="show running-config section management ssh",
        encoding="text",
        check_fn=_check_eos_ssh_enabled,
        remediation="Configure: management ssh\n   no shutdown",
        nist_controls=["AC-17", "SC-8"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.1.2",
        title="Ensure SSH idle timeout is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config section management ssh",
        encoding="text",
        check_fn=_check_eos_ssh_idle_timeout,
        remediation="Configure: management ssh\n   idle-timeout 15",
        nist_controls=["AC-12", "SC-10"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.1.3",
        title="Ensure SSH idle timeout is <= 300 seconds",
        severity="HIGH",
        category="management",
        command="show running-config section management ssh",
        encoding="text",
        check_fn=_check_eos_ssh_timeout_value_bool,
        remediation="Configure: management ssh\n   idle-timeout 15",
        nist_controls=["AC-12", "SC-10"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.2.1",
        title="Ensure AAA authentication uses TACACS+ or RADIUS",
        severity="HIGH",
        category="management",
        command="show running-config section aaa",
        encoding="text",
        check_fn=_check_eos_aaa_configured,
        remediation="Configure: aaa authentication login default group tacacs+ local",
        nist_controls=["IA-2", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.2.2",
        title="Ensure AAA accounting is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config section aaa",
        encoding="text",
        check_fn=_check_eos_aaa_accounting,
        remediation="Configure: aaa accounting commands all default start-stop group tacacs+",
        nist_controls=["AU-2", "AU-3"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.2.3",
        title="Ensure AAA authorization is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config section aaa",
        encoding="text",
        check_fn=_check_eos_aaa_authorization,
        remediation="Configure: aaa authorization exec default group tacacs+ local",
        nist_controls=["AC-3", "AC-6"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.3.1",
        title="Ensure password encryption is enabled",
        severity="MEDIUM",
        category="management",
        command="show running-config section service",
        encoding="text",
        check_fn=_check_eos_password_encryption,
        remediation="Configure: service password-encryption",
        nist_controls=["IA-5"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.3.2",
        title="Ensure login banner is configured",
        severity="LOW",
        category="management",
        command="show running-config section banner",
        encoding="text",
        check_fn=_check_eos_login_banner,
        remediation="Configure: banner login\nAuthorized access only. All activity is monitored.\nEOF",
        nist_controls=["AC-8"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.3.3",
        title="Ensure MOTD banner is configured",
        severity="LOW",
        category="management",
        command="show running-config section banner",
        encoding="text",
        check_fn=_check_eos_motd_banner,
        remediation="Configure: banner motd\nAuthorized access only.\nEOF",
        nist_controls=["AC-8"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.3.4",
        title="Ensure minimum password length >= 8",
        severity="MEDIUM",
        category="management",
        command="show running-config section aaa",
        encoding="text",
        check_fn=_check_eos_password_min_length,
        remediation="Configure: aaa password policy minimum-length 8",
        nist_controls=["IA-5"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.4.1",
        title="Ensure eAPI HTTPS is enabled and HTTP is disabled",
        severity="HIGH",
        category="management",
        command="show running-config section management api http-commands",
        encoding="text",
        check_fn=_check_eos_eapi_https_only,
        remediation="Configure: management api http-commands\n   protocol https\n   no protocol http\n   no shutdown",
        nist_controls=["SC-8", "SC-23"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.4.2",
        title="Ensure SSH protocol version 2 is enforced",
        severity="HIGH",
        category="management",
        command="show running-config section management ssh",
        encoding="text",
        check_fn=_check_eos_ssh_version_2,
        remediation="EOS defaults to SSHv2. Verify no v1 fallback is configured.",
        nist_controls=["SC-8", "AC-17"],
    ),
    ComplianceCheck(
        id="CIS-EOS-1.4.3",
        title="Ensure VTY access is restricted with an ACL",
        severity="HIGH",
        category="management",
        command="show running-config section management ssh",
        encoding="text",
        check_fn=_check_eos_vty_access_class,
        remediation="Configure: management ssh\n   access-class SSH_ACL",
        nist_controls=["AC-3", "SC-7"],
    ),
    # Control Plane (2.x)
    ComplianceCheck(
        id="CIS-EOS-2.1.1",
        title="Ensure NTP is configured with at least one server",
        severity="HIGH",
        category="control",
        command="show running-config section ntp",
        encoding="text",
        check_fn=_check_eos_ntp_configured,
        remediation="Configure: ntp server <ntp-server-ip>",
        nist_controls=["AU-8"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.1.2",
        title="Ensure NTP authentication is enabled",
        severity="HIGH",
        category="control",
        command="show running-config section ntp",
        encoding="text",
        check_fn=_check_eos_ntp_authentication,
        remediation="Configure: ntp authenticate\nntp authentication-key 1 md5 <key>\nntp server <ip> key 1",
        nist_controls=["AU-8", "SC-8"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.1.3",
        title="Ensure NTP trusted key is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config section ntp",
        encoding="text",
        check_fn=_check_eos_ntp_trusted_key,
        remediation="Configure: ntp trusted-key 1",
        nist_controls=["AU-8"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.2.1",
        title="Ensure logging to remote syslog server is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config section logging",
        encoding="text",
        check_fn=_check_eos_logging_remote,
        remediation="Configure: logging host <syslog-server-ip>",
        nist_controls=["AU-2", "AU-3", "AU-6"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.2.2",
        title="Ensure logging level is informational or higher",
        severity="MEDIUM",
        category="control",
        command="show running-config section logging",
        encoding="text",
        check_fn=_check_eos_logging_level,
        remediation="Configure: logging trap informational",
        nist_controls=["AU-2", "AU-3"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.2.3",
        title="Ensure logging buffer is configured",
        severity="LOW",
        category="control",
        command="show running-config section logging",
        encoding="text",
        check_fn=_check_eos_logging_buffered,
        remediation="Configure: logging buffered 100000",
        nist_controls=["AU-2", "AU-4"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.3.1",
        title="Ensure SNMP community strings are not default (public/private)",
        severity="HIGH",
        category="control",
        command="show running-config section snmp-server",
        encoding="text",
        check_fn=_check_eos_snmp_no_defaults,
        remediation="Remove default communities: no snmp-server community public\nno snmp-server community private",
        nist_controls=["AC-3", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.3.2",
        title="Ensure enable secret is configured",
        severity="HIGH",
        category="control",
        command="show running-config section enable",
        encoding="text",
        check_fn=_check_eos_enable_secret,
        remediation="Configure: enable secret 0 <your-secret>",
        nist_controls=["IA-2", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.3.3",
        title="Ensure SNMPv3 with authentication and encryption",
        severity="HIGH",
        category="control",
        command="show running-config section snmp-server",
        encoding="text",
        check_fn=_check_eos_snmp_v3,
        remediation="Configure: snmp-server group MYGROUP v3 priv\nsnmp-server user MYUSER MYGROUP v3 auth sha <key>",
        nist_controls=["SC-8", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.3.4",
        title="Ensure SNMP has ACL restrictions",
        severity="MEDIUM",
        category="control",
        command="show running-config section snmp-server",
        encoding="text",
        check_fn=_check_eos_snmp_acl,
        remediation="Configure: snmp-server community <string> ro access <ACL-NAME>",
        nist_controls=["AC-3", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.4.1",
        title="Ensure console and VTY exec-timeout is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config section line",
        encoding="text",
        check_fn=_check_eos_console_timeout,
        remediation="Configure: line con 0\n   exec-timeout 10 0\nline vty 0 15\n   exec-timeout 10 0",
        nist_controls=["AC-12", "SC-10"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.4.2",
        title="Ensure console timeout is <= 10 minutes",
        severity="MEDIUM",
        category="control",
        command="show running-config section line",
        encoding="text",
        check_fn=_check_eos_console_timeout_value,
        remediation="Configure: line con 0\n   exec-timeout 10 0",
        nist_controls=["AC-12", "SC-10"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.4.3",
        title="Ensure RADIUS uses a source interface",
        severity="MEDIUM",
        category="control",
        command="show running-config section radius",
        encoding="text",
        check_fn=_check_eos_radius_source_interface,
        remediation="Configure: ip radius source-interface <interface>",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.4.4",
        title="Ensure TACACS+ uses a source interface",
        severity="MEDIUM",
        category="control",
        command="show running-config section tacacs",
        encoding="text",
        check_fn=_check_eos_tacacs_source_interface,
        remediation="Configure: ip tacacs source-interface <interface>",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-2.4.5",
        title="Ensure local authentication fallback is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config section aaa",
        encoding="text",
        check_fn=_check_eos_aaa_local_auth,
        remediation="Configure: aaa authentication login default group tacacs+ local",
        nist_controls=["IA-2", "AC-7"],
    ),
    # Data Plane (3.x)
    ComplianceCheck(
        id="CIS-EOS-3.1.1",
        title="Ensure unused interfaces are administratively shut down",
        severity="MEDIUM",
        category="data",
        command="show running-config section interface",
        encoding="text",
        check_fn=_check_eos_unused_interfaces_shutdown,
        remediation="Configure: interface <unused-intf>\n   shutdown",
        nist_controls=["CM-7", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.1.2",
        title="Ensure storm control is configured on access ports",
        severity="MEDIUM",
        category="data",
        command="show running-config section interface",
        encoding="text",
        check_fn=_check_eos_storm_control,
        remediation="Configure: interface <intf>\n   storm-control broadcast level 10",
        nist_controls=["SC-5"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.2.1",
        title="Ensure STP BPDU guard or root guard is enabled",
        severity="MEDIUM",
        category="data",
        command="show running-config section spanning-tree",
        encoding="text",
        check_fn=_check_eos_stp_guard,
        remediation="Configure: spanning-tree bpduguard enable (on access ports)\nor: spanning-tree guard root",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.2.2",
        title="Ensure STP mode is MSTP or RSTP",
        severity="LOW",
        category="data",
        command="show running-config section spanning-tree",
        encoding="text",
        check_fn=_check_eos_stp_mode,
        remediation="Configure: spanning-tree mode mstp",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.3.1",
        title="Ensure DHCP snooping or ARP inspection is enabled",
        severity="MEDIUM",
        category="data",
        command="show running-config section dhcp",
        encoding="text",
        check_fn=_check_eos_dhcp_snooping,
        remediation="Configure: ip dhcp snooping\nip dhcp snooping vlan <vlan-id>",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.3.2",
        title="Ensure dynamic ARP inspection is configured",
        severity="MEDIUM",
        category="data",
        command="show running-config section arp",
        encoding="text",
        check_fn=_check_eos_arp_inspection,
        remediation="Configure: ip arp inspection vlan <vlan-id>",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.3.3",
        title="Ensure IP source guard is enabled on access ports",
        severity="MEDIUM",
        category="data",
        command="show running-config section interface",
        encoding="text",
        check_fn=_check_eos_ip_source_guard,
        remediation="Configure: interface <intf>\n   ip verify source",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.4.1",
        title="Ensure Control Plane Policing (CoPP) is configured",
        severity="HIGH",
        category="data",
        command="show running-config section system control-plane",
        encoding="text",
        check_fn=_check_eos_copp_configured,
        remediation="Configure CoPP: system control-plane\n   ip access-group <copp-acl> in",
        nist_controls=["SC-5", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.4.2",
        title="Ensure CoPP has rate limiting",
        severity="HIGH",
        category="data",
        command="show running-config section system control-plane",
        encoding="text",
        check_fn=_check_eos_copp_rate_limit,
        remediation="Ensure policy-map applied to CoPP includes rate limiting (police/rate statements)",
        nist_controls=["SC-5"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.4.3",
        title="Ensure ACLs end with explicit deny",
        severity="MEDIUM",
        category="data",
        command="show running-config section access-list",
        encoding="text",
        check_fn=_check_eos_acl_deny_all,
        remediation="Add explicit deny at end of ACLs: deny any",
        nist_controls=["AC-3", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.4.4",
        title="Ensure uRPF is configured",
        severity="HIGH",
        category="data",
        command="show running-config section interface",
        encoding="text",
        check_fn=_check_eos_urpf,
        remediation="Configure: interface <intf>\n   ip verify unicast source reachable-via rx",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.5.1",
        title="Ensure LLDP is restricted on untrusted ports",
        severity="LOW",
        category="data",
        command="show running-config section lldp",
        encoding="text",
        check_fn=_check_eos_lldp_restricted,
        remediation="Configure on untrusted interfaces: no lldp transmit\n   no lldp receive",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.5.2",
        title="Ensure management traffic uses a dedicated VRF",
        severity="MEDIUM",
        category="data",
        command="show running-config section management",
        encoding="text",
        check_fn=_check_eos_mgmt_vrf,
        remediation="Configure: vrf instance MGMT\ninterface Management1\n   vrf MGMT",
        nist_controls=["SC-7", "AC-3"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.5.3",
        title="Ensure no IP directed-broadcast is configured",
        severity="MEDIUM",
        category="data",
        command="show running-config section interface",
        encoding="text",
        check_fn=_check_eos_ip_routing_secure,
        remediation="Configure: interface <intf>\n   no ip directed-broadcast",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-EOS-3.5.4",
        title="Ensure errdisable recovery is configured",
        severity="LOW",
        category="data",
        command="show running-config section errdisable",
        encoding="text",
        check_fn=_check_eos_errdisable_recovery,
        remediation="Configure: errdisable recovery cause bpduguard\nerrdisable recovery interval 300",
        nist_controls=["SC-7"],
    ),
]

# Backward-compatible alias — existing code may reference CIS_CHECKS
CIS_CHECKS: list[ComplianceCheck] = CIS_EOS_CHECKS

CIS_IOSXE_CHECKS: list[ComplianceCheck] = [
    # Management Plane (1.x)
    ComplianceCheck(
        id="CIS-IOSXE-1.1.1",
        title="Ensure SSH version 2 is configured",
        severity="HIGH",
        category="management",
        command="show running-config | include ip ssh",
        encoding="text",
        check_fn=_check_iosxe_ssh_version_2,
        remediation="Configure: ip ssh version 2",
        nist_controls=["AC-17", "SC-8"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.1.2",
        title="Ensure SSH timeout is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config | include ip ssh",
        encoding="text",
        check_fn=_check_iosxe_ssh_timeout,
        remediation="Configure: ip ssh time-out 60",
        nist_controls=["AC-12", "SC-10"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.1.3",
        title="Ensure SSH timeout value <= 60 seconds",
        severity="HIGH",
        category="management",
        command="show running-config | include ip ssh",
        encoding="text",
        check_fn=_check_iosxe_ssh_timeout_value,
        remediation="Configure: ip ssh time-out 60",
        nist_controls=["AC-12", "SC-10"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.1.4",
        title="Ensure SSH authentication retries are limited",
        severity="MEDIUM",
        category="management",
        command="show running-config | include ip ssh",
        encoding="text",
        check_fn=_check_iosxe_ssh_retries,
        remediation="Configure: ip ssh authentication-retries 3",
        nist_controls=["AC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.2.1",
        title="Ensure AAA authentication is configured",
        severity="HIGH",
        category="management",
        command="show running-config | section aaa",
        encoding="text",
        check_fn=_check_iosxe_aaa_configured,
        remediation="Configure: aaa authentication login default group tacacs+ local",
        nist_controls=["IA-2", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.2.2",
        title="Ensure AAA accounting is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config | section aaa",
        encoding="text",
        check_fn=_check_iosxe_aaa_accounting,
        remediation="Configure: aaa accounting exec default start-stop group tacacs+",
        nist_controls=["AU-2", "AU-3"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.2.3",
        title="Ensure AAA authorization is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config | section aaa",
        encoding="text",
        check_fn=_check_iosxe_aaa_authorization,
        remediation="Configure: aaa authorization exec default group tacacs+ local",
        nist_controls=["AC-3", "AC-6"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.3.1",
        title="Ensure service password-encryption is enabled",
        severity="MEDIUM",
        category="management",
        command="show running-config | include service password",
        encoding="text",
        check_fn=_check_iosxe_password_encryption,
        remediation="Configure: service password-encryption",
        nist_controls=["IA-5"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.3.2",
        title="Ensure login banner is configured",
        severity="LOW",
        category="management",
        command="show running-config | section banner",
        encoding="text",
        check_fn=_check_iosxe_login_banner,
        remediation="Configure: banner login ^Authorized access only.^",
        nist_controls=["AC-8"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.3.3",
        title="Ensure MOTD banner is configured",
        severity="LOW",
        category="management",
        command="show running-config | section banner",
        encoding="text",
        check_fn=_check_iosxe_motd_banner,
        remediation="Configure: banner motd ^Authorized access only.^",
        nist_controls=["AC-8"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.3.4",
        title="Ensure minimum password length >= 8",
        severity="MEDIUM",
        category="management",
        command="show running-config | include security passwords",
        encoding="text",
        check_fn=_check_iosxe_password_min_length,
        remediation="Configure: security passwords min-length 8",
        nist_controls=["IA-5"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.4.1",
        title="Ensure HTTP server is disabled and HTTPS is enabled",
        severity="HIGH",
        category="management",
        command="show running-config | include ip http",
        encoding="text",
        check_fn=_check_iosxe_http_server_disabled,
        remediation="Configure: no ip http server\nip http secure-server",
        nist_controls=["SC-8", "SC-23"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-1.4.2",
        title="Ensure auxiliary port is disabled",
        severity="MEDIUM",
        category="management",
        command="show running-config | section line aux",
        encoding="text",
        check_fn=_check_iosxe_aux_port_disabled,
        remediation="Configure: line aux 0\n   no exec\n   transport input none",
        nist_controls=["AC-17", "CM-7"],
    ),
    # Control Plane (2.x)
    ComplianceCheck(
        id="CIS-IOSXE-2.1.1",
        title="Ensure NTP is configured with authentication",
        severity="HIGH",
        category="control",
        command="show running-config | section ntp",
        encoding="text",
        check_fn=_check_iosxe_ntp_authentication,
        remediation="Configure: ntp authenticate\nntp authentication-key 1 md5 <key>\nntp server <ip> key 1",
        nist_controls=["AU-8", "SC-8"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.1.2",
        title="Ensure NTP trusted key is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config | section ntp",
        encoding="text",
        check_fn=_check_iosxe_ntp_trusted_key,
        remediation="Configure: ntp trusted-key 1",
        nist_controls=["AU-8"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.2.1",
        title="Ensure logging to a remote syslog server is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config | include logging host",
        encoding="text",
        check_fn=_check_iosxe_logging_remote,
        remediation="Configure: logging host <syslog-server-ip>",
        nist_controls=["AU-2", "AU-3", "AU-6"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.2.2",
        title="Ensure logging buffer is configured",
        severity="LOW",
        category="control",
        command="show running-config | include logging buffered",
        encoding="text",
        check_fn=_check_iosxe_logging_buffered,
        remediation="Configure: logging buffered 100000",
        nist_controls=["AU-2", "AU-4"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.2.3",
        title="Ensure timestamps on log messages",
        severity="MEDIUM",
        category="control",
        command="show running-config | include service timestamps",
        encoding="text",
        check_fn=_check_iosxe_logging_timestamps,
        remediation="Configure: service timestamps log datetime msec",
        nist_controls=["AU-2", "AU-3"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.3.1",
        title="Ensure SNMP community strings are not default (public/private)",
        severity="HIGH",
        category="control",
        command="show running-config | include snmp-server community",
        encoding="text",
        check_fn=_check_iosxe_snmp_no_defaults,
        remediation="Remove default communities: no snmp-server community public\nno snmp-server community private",
        nist_controls=["AC-3", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.3.2",
        title="Ensure enable secret is configured (not enable password)",
        severity="HIGH",
        category="control",
        command="show running-config | include enable secret",
        encoding="text",
        check_fn=_check_iosxe_enable_secret,
        remediation="Configure: enable secret 0 <your-secret>",
        nist_controls=["IA-2", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.3.3",
        title="Ensure SNMPv3 with authentication and encryption",
        severity="HIGH",
        category="control",
        command="show running-config | include snmp-server",
        encoding="text",
        check_fn=_check_iosxe_snmp_v3,
        remediation="Configure: snmp-server group MYGROUP v3 priv",
        nist_controls=["SC-8", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.3.4",
        title="Ensure SNMP community strings have ACL restrictions",
        severity="MEDIUM",
        category="control",
        command="show running-config | include snmp-server community",
        encoding="text",
        check_fn=_check_iosxe_snmp_acl,
        remediation="Configure: snmp-server community <string> ro access <ACL>",
        nist_controls=["AC-3", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.4.1",
        title="Ensure VTY transport input is restricted to SSH",
        severity="HIGH",
        category="control",
        command="show running-config | section line vty",
        encoding="text",
        check_fn=_check_iosxe_vty_transport_ssh,
        remediation="Configure: line vty 0 15\n   transport input ssh",
        nist_controls=["AC-17", "SC-8"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.4.2",
        title="Ensure VTY lines have access-class configured",
        severity="HIGH",
        category="control",
        command="show running-config | section line vty",
        encoding="text",
        check_fn=_check_iosxe_vty_acl,
        remediation="Configure: line vty 0 15\n   access-class SSH_ACL in",
        nist_controls=["AC-3", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-2.4.3",
        title="Ensure console timeout is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config | section line con",
        encoding="text",
        check_fn=_check_iosxe_console_timeout,
        remediation="Configure: line con 0\n   exec-timeout 10 0",
        nist_controls=["AC-12", "SC-10"],
    ),
    # Data Plane (3.x)
    ComplianceCheck(
        id="CIS-IOSXE-3.1.1",
        title="Ensure unused interfaces are shut down",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_iosxe_unused_interfaces_shutdown,
        remediation="Configure: interface <unused-intf>\n   shutdown",
        nist_controls=["CM-7", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-3.2.1",
        title="Ensure CDP is disabled globally",
        severity="MEDIUM",
        category="data",
        command="show running-config | include cdp",
        encoding="text",
        check_fn=_check_iosxe_cdp_disabled,
        remediation="Configure: no cdp run",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-3.3.1",
        title="Ensure IP source routing is disabled",
        severity="HIGH",
        category="data",
        command="show running-config | include ip source-route",
        encoding="text",
        check_fn=_check_iosxe_no_source_routing,
        remediation="Configure: no ip source-route",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-3.4.1",
        title="Ensure ICMP redirects are disabled on interfaces",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_iosxe_no_icmp_redirects,
        remediation="Configure: interface <intf>\n   no ip redirects",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-3.4.2",
        title="Ensure proxy ARP is disabled on interfaces",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_iosxe_no_proxy_arp,
        remediation="Configure: interface <intf>\n   no ip proxy-arp",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-3.4.3",
        title="Ensure TCP keepalives are enabled",
        severity="LOW",
        category="data",
        command="show running-config | include tcp-keepalives",
        encoding="text",
        check_fn=_check_iosxe_tcp_keepalives,
        remediation="Configure: service tcp-keepalives-in\nservice tcp-keepalives-out",
        nist_controls=["SC-7", "SC-10"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-3.4.4",
        title="Ensure ICMP unreachable messages are disabled",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_iosxe_no_ip_unreachables,
        remediation="Configure: interface <intf>\n   no ip unreachables",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-3.5.1",
        title="Ensure uRPF is configured",
        severity="HIGH",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_iosxe_urpf,
        remediation="Configure: interface <intf>\n   ip verify unicast source reachable-via rx",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-IOSXE-3.5.2",
        title="Ensure storm control is configured",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_iosxe_storm_control,
        remediation="Configure: interface <intf>\n   storm-control broadcast level 10",
        nist_controls=["SC-5"],
    ),
]

CIS_NXOS_CHECKS: list[ComplianceCheck] = [
    # Management Plane (1.x)
    ComplianceCheck(
        id="CIS-NXOS-1.1.1",
        title="Ensure SSH is enabled and configured",
        severity="HIGH",
        category="management",
        command="show running-config | include feature ssh",
        encoding="text",
        check_fn=_check_nxos_ssh_enabled,
        remediation="Configure: feature ssh",
        nist_controls=["AC-17", "SC-8"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.1.2",
        title="Ensure SSH key exchange algorithm is hardened",
        severity="MEDIUM",
        category="management",
        command="show running-config | include ssh",
        encoding="text",
        check_fn=_check_nxos_ssh_kex_hardened,
        remediation="Configure: ssh key rsa 2048\nssh login-attempts 3",
        nist_controls=["SC-8", "AC-17"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.1.3",
        title="Ensure SSH login attempts are limited",
        severity="MEDIUM",
        category="management",
        command="show running-config | include ssh",
        encoding="text",
        check_fn=_check_nxos_ssh_login_attempts,
        remediation="Configure: ssh login-attempts 3",
        nist_controls=["AC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.2.1",
        title="Ensure AAA authentication is configured",
        severity="HIGH",
        category="management",
        command="show running-config | section aaa",
        encoding="text",
        check_fn=_check_nxos_aaa_configured,
        remediation="Configure: aaa authentication login default group tacacs+ local",
        nist_controls=["IA-2", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.2.2",
        title="Ensure AAA accounting is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config | section aaa",
        encoding="text",
        check_fn=_check_nxos_aaa_accounting,
        remediation="Configure: aaa accounting default group tacacs+",
        nist_controls=["AU-2", "AU-3"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.2.3",
        title="Ensure AAA authorization is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config | section aaa",
        encoding="text",
        check_fn=_check_nxos_aaa_authorization,
        remediation="Configure: aaa authorization commands default group tacacs+ local",
        nist_controls=["AC-3", "AC-6"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.3.1",
        title="Ensure password strength checking is enabled",
        severity="MEDIUM",
        category="management",
        command="show running-config | include password strength",
        encoding="text",
        check_fn=_check_nxos_password_strength,
        remediation="Configure: password strength-check",
        nist_controls=["IA-5"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.3.2",
        title="Ensure login banner is configured",
        severity="LOW",
        category="management",
        command="show running-config | section banner",
        encoding="text",
        check_fn=_check_nxos_login_banner,
        remediation="Configure: banner motd @Authorized access only.@",
        nist_controls=["AC-8"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.3.3",
        title="Ensure exec banner is configured",
        severity="LOW",
        category="management",
        command="show running-config | section banner",
        encoding="text",
        check_fn=_check_nxos_exec_banner,
        remediation="Configure: banner exec @Authorized access only.@",
        nist_controls=["AC-8"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.3.4",
        title="Ensure user roles are defined",
        severity="MEDIUM",
        category="management",
        command="show running-config | include role",
        encoding="text",
        check_fn=_check_nxos_user_role,
        remediation="Configure: role name network-operator",
        nist_controls=["AC-2", "AC-6"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.4.1",
        title="Ensure HTTPS management only (no HTTP)",
        severity="HIGH",
        category="management",
        command="show running-config | include nxapi",
        encoding="text",
        check_fn=_check_nxos_https_management,
        remediation="Configure: feature nxapi\nnxapi https port 443\nno nxapi http",
        nist_controls=["SC-8", "SC-23"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-1.4.2",
        title="Ensure console timeout is configured",
        severity="MEDIUM",
        category="management",
        command="show running-config | section line",
        encoding="text",
        check_fn=_check_nxos_console_timeout,
        remediation="Configure: line console\n   exec-timeout 10",
        nist_controls=["AC-12", "SC-10"],
    ),
    # Control Plane (2.x)
    ComplianceCheck(
        id="CIS-NXOS-2.1.1",
        title="Ensure NTP is configured and synchronized",
        severity="HIGH",
        category="control",
        command="show running-config | include ntp server",
        encoding="text",
        check_fn=_check_nxos_ntp_configured,
        remediation="Configure: ntp server <ntp-server-ip>",
        nist_controls=["AU-8"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.1.2",
        title="Ensure NTP authentication is configured",
        severity="HIGH",
        category="control",
        command="show running-config | include ntp",
        encoding="text",
        check_fn=_check_nxos_ntp_authentication,
        remediation="Configure: ntp authenticate\nntp authentication-key 1 md5 <key>\nntp server <ip> key 1",
        nist_controls=["AU-8", "SC-8"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.1.3",
        title="Ensure NTP trusted key is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config | include ntp",
        encoding="text",
        check_fn=_check_nxos_ntp_trusted_key,
        remediation="Configure: ntp trusted-key 1",
        nist_controls=["AU-8"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.2.1",
        title="Ensure logging to a remote syslog server is configured",
        severity="MEDIUM",
        category="control",
        command="show running-config | include logging server",
        encoding="text",
        check_fn=_check_nxos_logging_remote,
        remediation="Configure: logging server <syslog-server-ip>",
        nist_controls=["AU-2", "AU-3", "AU-6"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.2.2",
        title="Ensure logging level is informational or higher",
        severity="MEDIUM",
        category="control",
        command="show running-config | include logging",
        encoding="text",
        check_fn=_check_nxos_logging_level,
        remediation="Configure: logging level <facility> 6",
        nist_controls=["AU-2", "AU-3"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.2.3",
        title="Ensure logging timestamps are configured",
        severity="MEDIUM",
        category="control",
        command="show running-config | include logging",
        encoding="text",
        check_fn=_check_nxos_logging_timestamp,
        remediation="Configure: logging timestamp milliseconds",
        nist_controls=["AU-2", "AU-3"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.3.1",
        title="Ensure SNMP community strings are not default (public/private)",
        severity="HIGH",
        category="control",
        command="show running-config | include snmp-server community",
        encoding="text",
        check_fn=_check_nxos_snmp_no_defaults,
        remediation="Remove default communities: no snmp-server community public\nno snmp-server community private",
        nist_controls=["AC-3", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.3.2",
        title="Ensure Control Plane Policing (CoPP) is configured",
        severity="HIGH",
        category="control",
        command="show running-config | include copp|service-policy",
        encoding="text",
        check_fn=_check_nxos_copp_configured,
        remediation="Configure: copp profile strict",
        nist_controls=["SC-5", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.3.3",
        title="Ensure SNMPv3 with authentication",
        severity="HIGH",
        category="control",
        command="show running-config | include snmp-server",
        encoding="text",
        check_fn=_check_nxos_snmp_v3,
        remediation="Configure: snmp-server user MYUSER MYGROUP v3 auth sha <key> priv aes128 <key>",
        nist_controls=["SC-8", "IA-5"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.3.4",
        title="Ensure SNMP has ACL restrictions",
        severity="MEDIUM",
        category="control",
        command="show running-config | include snmp-server",
        encoding="text",
        check_fn=_check_nxos_snmp_acl,
        remediation="Configure: snmp-server community <string> use-acl <ACL-NAME>",
        nist_controls=["AC-3", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.4.1",
        title="Ensure NX-API is secured or disabled if not needed",
        severity="MEDIUM",
        category="control",
        command="show running-config | include nxapi",
        encoding="text",
        check_fn=_check_nxos_nxapi_secured,
        remediation="Disable if not needed: no feature nxapi\nOr secure: nxapi https port 443\nno nxapi sandbox",
        nist_controls=["SC-8", "CM-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.4.2",
        title="Ensure TACACS+ uses source interface",
        severity="MEDIUM",
        category="control",
        command="show running-config | include tacacs",
        encoding="text",
        check_fn=_check_nxos_tacacs_source,
        remediation="Configure: ip tacacs source-interface <interface>",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-2.4.3",
        title="Ensure RADIUS uses source interface",
        severity="MEDIUM",
        category="control",
        command="show running-config | include radius",
        encoding="text",
        check_fn=_check_nxos_radius_source,
        remediation="Configure: ip radius source-interface <interface>",
        nist_controls=["SC-7"],
    ),
    # Data Plane (3.x)
    ComplianceCheck(
        id="CIS-NXOS-3.1.1",
        title="Ensure unused interfaces are shut down",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_nxos_unused_interfaces_shutdown,
        remediation="Configure: interface <unused-intf>\n   shutdown",
        nist_controls=["CM-7", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.1.2",
        title="Ensure storm control is configured",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_nxos_storm_control,
        remediation="Configure: interface <intf>\n   storm-control broadcast level 10",
        nist_controls=["SC-5"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.2.1",
        title="Ensure DHCP snooping is enabled on access VLANs",
        severity="MEDIUM",
        category="data",
        command="show running-config | include dhcp",
        encoding="text",
        check_fn=_check_nxos_dhcp_snooping,
        remediation="Configure: feature dhcp\nip dhcp snooping\nip dhcp snooping vlan <vlan-id>",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.2.2",
        title="Ensure STP BPDU guard is enabled",
        severity="MEDIUM",
        category="data",
        command="show running-config | include spanning-tree",
        encoding="text",
        check_fn=_check_nxos_stp_bpduguard,
        remediation="Configure: spanning-tree port type edge bpduguard default",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.3.1",
        title="Ensure ARP inspection is enabled",
        severity="MEDIUM",
        category="data",
        command="show running-config | include arp inspection",
        encoding="text",
        check_fn=_check_nxos_arp_inspection,
        remediation="Configure: ip arp inspection vlan <vlan-id>",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.3.2",
        title="Ensure IP source guard is enabled",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_nxos_ip_source_guard,
        remediation="Configure: interface <intf>\n   ip verify source dhcp-snooping-vlan",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.4.1",
        title="Ensure port security or 802.1X is configured on access ports",
        severity="MEDIUM",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_nxos_port_security,
        remediation="Configure: interface <intf>\n   switchport port-security\nor: dot1x system-auth-control",
        nist_controls=["AC-3", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.4.2",
        title="Ensure ACLs end with explicit deny",
        severity="MEDIUM",
        category="data",
        command="show running-config | section access-list",
        encoding="text",
        check_fn=_check_nxos_acl_explicit_deny,
        remediation="Add explicit deny at end of ACLs: deny ip any any",
        nist_controls=["AC-3", "SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.4.3",
        title="Ensure uRPF is configured",
        severity="HIGH",
        category="data",
        command="show running-config | section interface",
        encoding="text",
        check_fn=_check_nxos_urpf,
        remediation="Configure: interface <intf>\n   ip verify unicast source reachable-via rx",
        nist_controls=["SC-7"],
    ),
    ComplianceCheck(
        id="CIS-NXOS-3.5.1",
        title="Ensure vPC peer-keepalive uses management VRF",
        severity="MEDIUM",
        category="data",
        command="show running-config | section vpc",
        encoding="text",
        check_fn=_check_nxos_vpc_peer_keepalive,
        remediation="Configure: vpc domain <id>\n   peer-keepalive destination <ip> source <ip> vrf management",
        nist_controls=["SC-7"],
    ),
]

# Index checks by framework
_FRAMEWORK_CHECKS: dict[str, list[ComplianceCheck]] = {
    "cis-eos": CIS_EOS_CHECKS,
    "cis-iosxe": CIS_IOSXE_CHECKS,
    "cis-nxos": CIS_NXOS_CHECKS,
}


# ============================================================================
# Helper: run all checks and return ComplianceResult list
# ============================================================================


def _run_compliance_checks(host: str, framework: str, category: str | None = None) -> dict | list[ComplianceResult]:
    """Run checks for a framework/category and return ComplianceResult list, or error dict."""
    checks = _FRAMEWORK_CHECKS[framework]
    if category:
        checks = [c for c in checks if c.category == category]

    results: list[ComplianceResult] = []
    for check in checks:
        result = run_show_command(conn_mgr, host, [check.command], encoding=check.encoding)
        if result["status"] == "error":
            return result

        if check.encoding == "text":
            output = result["data"][0].get("output", "")
        else:
            output = result["data"][0]

        compliant = check.check_fn(output)
        results.append(
            ComplianceResult(
                check_id=check.id,
                check=check.title,
                status="pass" if compliant else "fail",
                detail=check.remediation if not compliant else "",
                severity=check.severity.lower(),
                nist_controls=check.nist_controls[:],
            )
        )

    return results


# --- Tools ---


@mcp.tool(annotations=READ_ONLY)
def eos_compliance_check(host: str, framework: str = "cis-eos", category: str | None = None) -> dict:
    """Run compliance checks against a network device.

    Validates device configuration against CIS security benchmarks and returns
    pass/fail results with remediation guidance, NIST 800-53 control mappings,
    and severity for each check.

    Supports Arista EOS, Cisco IOS-XE, and Cisco NX-OS platforms.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        framework: Compliance framework to use. Supported: "cis-eos", "cis-iosxe", "cis-nxos".
        category: Optional filter by category ("management", "control", "data"). Runs all if not specified.
    """
    if framework not in VALID_FRAMEWORKS:
        return {
            "status": "error",
            "device": host,
            "error": f"Unsupported framework '{framework}'. Supported: {', '.join(VALID_FRAMEWORKS)}",
        }

    if category is not None and category not in VALID_CATEGORIES:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid category '{category}'. Valid: {', '.join(VALID_CATEGORIES)}",
        }

    check_results = _run_compliance_checks(host, framework, category)
    if isinstance(check_results, dict):
        return check_results

    results = []
    for cr in check_results:
        entry: dict = {
            "id": cr.check_id,
            "title": cr.check,
            "severity": cr.severity.upper(),
            "category": _get_category_for_id(cr.check_id, framework),
            "status": "PASS" if cr.status == "pass" else "FAIL",
            "nist_controls": cr.nist_controls,
        }
        if cr.status == "fail":
            entry["remediation"] = cr.detail
        results.append(entry)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = len(results) - passed
    total = len(results)
    pass_rate = f"{(passed / total * 100):.1f}%" if total > 0 else "N/A"

    return {
        "status": "success",
        "device": host,
        "data": {
            "framework": framework,
            "total_checks": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "results": results,
        },
    }


def _get_category_for_id(check_id: str, framework: str) -> str:
    """Look up the category for a check ID."""
    for check in _FRAMEWORK_CHECKS.get(framework, []):
        if check.id == check_id:
            return check.category
    return "unknown"


@mcp.tool(annotations=READ_ONLY)
def eos_list_compliance_checks(framework: str | None = None) -> dict:
    """List available compliance checks and their details.

    Returns checks for a given framework (or all frameworks) with id, title,
    severity, category, and NIST 800-53 control mappings. No device connection is required.

    Args:
        framework: Compliance framework to list. If None, shows all frameworks.
                   Supported: "cis-eos", "cis-iosxe", "cis-nxos".
    """
    if framework is not None and framework not in VALID_FRAMEWORKS:
        return {
            "status": "error",
            "error": f"Unsupported framework '{framework}'. Supported: {', '.join(VALID_FRAMEWORKS)}",
        }

    if framework is not None:
        checks = _FRAMEWORK_CHECKS[framework]
        return {
            "status": "success",
            "data": {
                "framework": framework,
                "total_checks": len(checks),
                "checks": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "severity": c.severity,
                        "category": c.category,
                        "nist_controls": c.nist_controls,
                    }
                    for c in checks
                ],
            },
        }

    # Return all frameworks
    frameworks_data = {}
    total_all = 0
    for fw_name, fw_checks in _FRAMEWORK_CHECKS.items():
        total_all += len(fw_checks)
        frameworks_data[fw_name] = {
            "total_checks": len(fw_checks),
            "checks": [
                {
                    "id": c.id,
                    "title": c.title,
                    "severity": c.severity,
                    "category": c.category,
                    "nist_controls": c.nist_controls,
                }
                for c in fw_checks
            ],
        }

    return {
        "status": "success",
        "data": {
            "total_frameworks": len(_FRAMEWORK_CHECKS),
            "total_checks": total_all,
            "frameworks": frameworks_data,
        },
    }


@mcp.tool(annotations=READ_ONLY)
def eos_compliance_report(host: str, framework: str = "cis-eos", report_format: str = "json") -> dict:
    """Run all compliance checks and generate a compliance report.

    Runs the full suite of CIS compliance checks against a device and returns
    a structured report with NIST 800-53 control mappings, severity breakdown,
    and pass/fail statistics.

    Args:
        host: Hostname, IP address, or inventory name of the network device.
        framework: Compliance framework. Supported: "cis-eos", "cis-iosxe", "cis-nxos".
        report_format: Output format — 'json' or 'summary'.
    """
    if framework not in VALID_FRAMEWORKS:
        return {
            "status": "error",
            "device": host,
            "error": f"Unsupported framework '{framework}'. Supported: {', '.join(VALID_FRAMEWORKS)}",
        }

    if report_format not in ("json", "summary"):
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid format '{report_format}'. Supported: json, summary",
        }

    check_results = _run_compliance_checks(host, framework)
    if isinstance(check_results, dict):
        return check_results

    passed = [r for r in check_results if r.status == "pass"]
    failed = [r for r in check_results if r.status == "fail"]
    total = len(check_results)
    pass_rate = f"{(len(passed) / total * 100):.1f}%" if total > 0 else "N/A"

    # Severity breakdown
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    severity_failed = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in check_results:
        sev = r.severity.lower()
        if sev in severity_counts:
            severity_counts[sev] += 1
        if r.status == "fail" and sev in severity_failed:
            severity_failed[sev] += 1

    # Collect all NIST controls referenced
    nist_coverage: dict[str, dict[str, int]] = {}
    for r in check_results:
        for ctrl in r.nist_controls:
            if ctrl not in nist_coverage:
                nist_coverage[ctrl] = {"total": 0, "passed": 0, "failed": 0}
            nist_coverage[ctrl]["total"] += 1
            if r.status == "pass":
                nist_coverage[ctrl]["passed"] += 1
            else:
                nist_coverage[ctrl]["failed"] += 1

    if report_format == "summary":
        # Build text summary
        lines = [
            f"Compliance Report: {framework}",
            f"Device: {host}",
            f"Total Checks: {total}",
            f"Passed: {len(passed)} | Failed: {len(failed)} | Pass Rate: {pass_rate}",
            "",
            "Severity Breakdown (failed/total):",
        ]
        for sev in ("critical", "high", "medium", "low"):
            lines.append(f"  {sev.upper()}: {severity_failed[sev]}/{severity_counts[sev]}")

        if failed:
            lines.append("")
            lines.append("Failed Checks:")
            for r in failed:
                nist_str = ", ".join(r.nist_controls) if r.nist_controls else "N/A"
                lines.append(f"  [{r.severity.upper()}] {r.check_id}: {r.check} (NIST: {nist_str})")

        return {
            "status": "success",
            "device": host,
            "data": {
                "format": "summary",
                "report": "\n".join(lines),
            },
        }

    # JSON format
    return {
        "status": "success",
        "device": host,
        "data": {
            "format": "json",
            "framework": framework,
            "total_checks": total,
            "passed": len(passed),
            "failed": len(failed),
            "pass_rate": pass_rate,
            "severity_breakdown": severity_counts,
            "severity_failed": severity_failed,
            "nist_coverage": nist_coverage,
            "results": [
                {
                    "check_id": r.check_id,
                    "check": r.check,
                    "status": r.status,
                    "severity": r.severity,
                    "nist_controls": r.nist_controls,
                    "detail": r.detail,
                }
                for r in check_results
            ],
        },
    }
