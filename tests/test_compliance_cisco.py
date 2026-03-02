"""Tests for CIS compliance checking — Cisco IOS-XE and NX-OS frameworks."""

import pytest

from network_mcp.helpers import device_rate_limiter
from network_mcp.tools.compliance import (
    CIS_IOSXE_CHECKS,
    CIS_NXOS_CHECKS,
    VALID_FRAMEWORKS,
    _check_iosxe_aaa_configured,
    _check_iosxe_cdp_disabled,
    _check_iosxe_enable_secret,
    _check_iosxe_http_server_disabled,
    _check_iosxe_logging_remote,
    _check_iosxe_login_banner,
    _check_iosxe_no_icmp_redirects,
    _check_iosxe_no_source_routing,
    _check_iosxe_ntp_authentication,
    _check_iosxe_password_encryption,
    _check_iosxe_snmp_no_defaults,
    _check_iosxe_ssh_timeout,
    _check_iosxe_ssh_version_2,
    _check_iosxe_unused_interfaces_shutdown,
    _check_iosxe_vty_transport_ssh,
    _check_nxos_aaa_configured,
    _check_nxos_arp_inspection,
    _check_nxos_copp_configured,
    _check_nxos_dhcp_snooping,
    _check_nxos_https_management,
    _check_nxos_logging_remote,
    _check_nxos_login_banner,
    _check_nxos_ntp_configured,
    _check_nxos_nxapi_secured,
    _check_nxos_password_strength,
    _check_nxos_port_security,
    _check_nxos_snmp_no_defaults,
    _check_nxos_ssh_enabled,
    _check_nxos_ssh_kex_hardened,
    _check_nxos_unused_interfaces_shutdown,
    eos_compliance_check,
    eos_list_compliance_checks,
)


@pytest.fixture(autouse=True)
def _high_rate_limit():
    """Raise show rate limit for compliance tests — each test runs 15+ show commands."""
    orig_rate = device_rate_limiter._show_rate
    orig_burst = device_rate_limiter._show_burst
    device_rate_limiter._show_rate = 1000.0
    device_rate_limiter._show_burst = 1000.0
    yield
    device_rate_limiter._show_rate = orig_rate
    device_rate_limiter._show_burst = orig_burst


def _make_text_response(text: str) -> list:
    """Build a mock run_commands return value for text encoding."""
    return [{"output": text}]


# ============================================================================
# IOS-XE compliant config fragments
# ============================================================================

IOSXE_COMPLIANT_SSH = """\
ip ssh version 2
ip ssh time-out 60
ip ssh authentication-retries 3
"""

IOSXE_COMPLIANT_AAA = """\
aaa authentication login default group tacacs+ local
aaa authorization exec default group tacacs+ local
"""

IOSXE_COMPLIANT_PASSWORD_ENCRYPTION = """\
service password-encryption
"""

IOSXE_COMPLIANT_BANNER = """\
banner login ^Authorized access only. All activity is monitored.^
"""

IOSXE_COMPLIANT_HTTP = """\
no ip http server
ip http secure-server
"""

IOSXE_COMPLIANT_NTP = """\
ntp authenticate
ntp authentication-key 1 md5 MyNTPKey
ntp server 10.10.10.1 key 1
"""

IOSXE_COMPLIANT_LOGGING = """\
logging host 192.168.1.100
"""

IOSXE_COMPLIANT_SNMP = """\
snmp-server community MySecretString ro
"""

IOSXE_COMPLIANT_ENABLE_SECRET = """\
enable secret 9 $14$abc123
"""

IOSXE_COMPLIANT_VTY = """\
line vty 0 15
 transport input ssh
 login authentication default
"""

IOSXE_COMPLIANT_INTERFACE_SHUTDOWN = """\
interface GigabitEthernet0/1
 shutdown
"""

IOSXE_COMPLIANT_CDP = """\
no cdp run
"""

IOSXE_COMPLIANT_NO_SOURCE_ROUTE = """\
no ip source-route
"""

IOSXE_COMPLIANT_NO_REDIRECTS = """\
interface GigabitEthernet0/1
 no ip redirects
"""

# ============================================================================
# IOS-XE non-compliant config fragments
# ============================================================================

IOSXE_NONCOMPLIANT_SSH = """\
ip ssh version 1
"""

IOSXE_NONCOMPLIANT_SSH_TIMEOUT = """\
ip ssh version 2
"""

IOSXE_NONCOMPLIANT_AAA = """\
aaa authentication login default local
"""

IOSXE_NONCOMPLIANT_PASSWORD = """\
no service password-encryption
"""

IOSXE_NONCOMPLIANT_BANNER = """\
"""

IOSXE_NONCOMPLIANT_HTTP = """\
ip http server
ip http secure-server
"""

IOSXE_NONCOMPLIANT_NTP = """\
ntp server 10.10.10.1
"""

IOSXE_NONCOMPLIANT_LOGGING = """\
logging buffered 100000
"""

IOSXE_NONCOMPLIANT_SNMP = """\
snmp-server community public ro
"""

IOSXE_NONCOMPLIANT_ENABLE = """\
enable password 0 admin123
"""

IOSXE_NONCOMPLIANT_VTY = """\
line vty 0 15
 transport input telnet ssh
"""

IOSXE_NONCOMPLIANT_INTERFACE = """\
interface GigabitEthernet0/1
 no switchport
"""

IOSXE_NONCOMPLIANT_CDP = """\
cdp run
"""

IOSXE_NONCOMPLIANT_SOURCE_ROUTE = """\
ip source-route
"""

IOSXE_NONCOMPLIANT_REDIRECTS = """\
interface GigabitEthernet0/1
 ip redirects
"""

# ============================================================================
# NX-OS compliant config fragments
# ============================================================================

NXOS_COMPLIANT_SSH = """\
feature ssh
"""

NXOS_COMPLIANT_SSH_KEX = """\
ssh key rsa 2048
ssh login-attempts 3
"""

NXOS_COMPLIANT_AAA = """\
aaa authentication login default group tacacs+ local
"""

NXOS_COMPLIANT_PASSWORD_STRENGTH = """\
password strength-check
"""

NXOS_COMPLIANT_BANNER = """\
banner motd @Authorized access only. All activity is monitored.@
"""

NXOS_COMPLIANT_HTTPS = """\
feature nxapi
nxapi https port 443
no nxapi http
"""

NXOS_COMPLIANT_NTP = """\
ntp server 10.10.10.1
ntp server 10.10.10.2
"""

NXOS_COMPLIANT_LOGGING = """\
logging server 192.168.1.100
"""

NXOS_COMPLIANT_SNMP = """\
snmp-server community MySecretString group network-operator
"""

NXOS_COMPLIANT_COPP = """\
copp profile strict
"""

NXOS_COMPLIANT_NXAPI_SECURED = """\
feature nxapi
nxapi https port 443
"""

NXOS_COMPLIANT_NXAPI_DISABLED = """\
no feature nxapi
"""

NXOS_COMPLIANT_INTERFACE_SHUTDOWN = """\
interface Ethernet1/1
  shutdown
"""

NXOS_COMPLIANT_DHCP = """\
feature dhcp
ip dhcp snooping
ip dhcp snooping vlan 100
"""

NXOS_COMPLIANT_ARP_INSPECTION = """\
ip arp inspection vlan 100
"""

NXOS_COMPLIANT_PORT_SECURITY = """\
interface Ethernet1/1
  switchport port-security
"""

# ============================================================================
# NX-OS non-compliant config fragments
# ============================================================================

NXOS_NONCOMPLIANT_SSH = """\
"""

NXOS_NONCOMPLIANT_SSH_KEX = """\
"""

NXOS_NONCOMPLIANT_AAA = """\
aaa authentication login default local
"""

NXOS_NONCOMPLIANT_PASSWORD_STRENGTH = """\
no password strength-check
"""

NXOS_NONCOMPLIANT_BANNER = """\
"""

NXOS_NONCOMPLIANT_HTTPS = """\
feature nxapi
nxapi http port 80
"""

NXOS_NONCOMPLIANT_NTP = """\
"""

NXOS_NONCOMPLIANT_LOGGING = """\
logging logfile messages 5
"""

NXOS_NONCOMPLIANT_SNMP = """\
snmp-server community public group network-operator
"""

NXOS_NONCOMPLIANT_COPP = """\
"""

NXOS_NONCOMPLIANT_NXAPI = """\
feature nxapi
nxapi http port 80
nxapi sandbox
"""

NXOS_NONCOMPLIANT_INTERFACE = """\
interface Ethernet1/1
  no switchport
"""

NXOS_NONCOMPLIANT_DHCP = """\
"""

NXOS_NONCOMPLIANT_ARP = """\
"""

NXOS_NONCOMPLIANT_PORT_SECURITY = """\
interface Ethernet1/1
  switchport mode access
"""


# ============================================================================
# IOS-XE individual check function tests
# ============================================================================


class TestIosXeCheckFunctions:
    """Test each IOS-XE check function with compliant and non-compliant configs."""

    def test_ssh_version_2_compliant(self):
        assert _check_iosxe_ssh_version_2(IOSXE_COMPLIANT_SSH) is True

    def test_ssh_version_2_noncompliant(self):
        assert _check_iosxe_ssh_version_2(IOSXE_NONCOMPLIANT_SSH) is False

    def test_ssh_timeout_compliant(self):
        assert _check_iosxe_ssh_timeout(IOSXE_COMPLIANT_SSH) is True

    def test_ssh_timeout_noncompliant(self):
        assert _check_iosxe_ssh_timeout(IOSXE_NONCOMPLIANT_SSH_TIMEOUT) is False

    def test_aaa_compliant(self):
        assert _check_iosxe_aaa_configured(IOSXE_COMPLIANT_AAA) is True

    def test_aaa_noncompliant(self):
        assert _check_iosxe_aaa_configured(IOSXE_NONCOMPLIANT_AAA) is False

    def test_password_encryption_compliant(self):
        assert _check_iosxe_password_encryption(IOSXE_COMPLIANT_PASSWORD_ENCRYPTION) is True

    def test_password_encryption_noncompliant(self):
        assert _check_iosxe_password_encryption(IOSXE_NONCOMPLIANT_PASSWORD) is False

    def test_login_banner_compliant(self):
        assert _check_iosxe_login_banner(IOSXE_COMPLIANT_BANNER) is True

    def test_login_banner_noncompliant(self):
        assert _check_iosxe_login_banner(IOSXE_NONCOMPLIANT_BANNER) is False

    def test_http_server_disabled_compliant(self):
        assert _check_iosxe_http_server_disabled(IOSXE_COMPLIANT_HTTP) is True

    def test_http_server_disabled_noncompliant(self):
        assert _check_iosxe_http_server_disabled(IOSXE_NONCOMPLIANT_HTTP) is False

    def test_ntp_authentication_compliant(self):
        assert _check_iosxe_ntp_authentication(IOSXE_COMPLIANT_NTP) is True

    def test_ntp_authentication_noncompliant(self):
        assert _check_iosxe_ntp_authentication(IOSXE_NONCOMPLIANT_NTP) is False

    def test_logging_remote_compliant(self):
        assert _check_iosxe_logging_remote(IOSXE_COMPLIANT_LOGGING) is True

    def test_logging_remote_noncompliant(self):
        assert _check_iosxe_logging_remote(IOSXE_NONCOMPLIANT_LOGGING) is False

    def test_snmp_no_defaults_compliant(self):
        assert _check_iosxe_snmp_no_defaults(IOSXE_COMPLIANT_SNMP) is True

    def test_snmp_no_defaults_noncompliant(self):
        assert _check_iosxe_snmp_no_defaults(IOSXE_NONCOMPLIANT_SNMP) is False

    def test_snmp_no_defaults_empty(self):
        assert _check_iosxe_snmp_no_defaults("") is True

    def test_enable_secret_compliant(self):
        assert _check_iosxe_enable_secret(IOSXE_COMPLIANT_ENABLE_SECRET) is True

    def test_enable_secret_noncompliant(self):
        assert _check_iosxe_enable_secret(IOSXE_NONCOMPLIANT_ENABLE) is False

    def test_vty_transport_ssh_compliant(self):
        assert _check_iosxe_vty_transport_ssh(IOSXE_COMPLIANT_VTY) is True

    def test_vty_transport_ssh_noncompliant(self):
        assert _check_iosxe_vty_transport_ssh(IOSXE_NONCOMPLIANT_VTY) is False

    def test_unused_interfaces_shutdown_compliant(self):
        assert _check_iosxe_unused_interfaces_shutdown(IOSXE_COMPLIANT_INTERFACE_SHUTDOWN) is True

    def test_unused_interfaces_shutdown_noncompliant(self):
        assert _check_iosxe_unused_interfaces_shutdown(IOSXE_NONCOMPLIANT_INTERFACE) is False

    def test_cdp_disabled_compliant(self):
        assert _check_iosxe_cdp_disabled(IOSXE_COMPLIANT_CDP) is True

    def test_cdp_disabled_noncompliant(self):
        assert _check_iosxe_cdp_disabled(IOSXE_NONCOMPLIANT_CDP) is False

    def test_no_source_routing_compliant(self):
        assert _check_iosxe_no_source_routing(IOSXE_COMPLIANT_NO_SOURCE_ROUTE) is True

    def test_no_source_routing_noncompliant(self):
        assert _check_iosxe_no_source_routing(IOSXE_NONCOMPLIANT_SOURCE_ROUTE) is False

    def test_no_icmp_redirects_compliant(self):
        assert _check_iosxe_no_icmp_redirects(IOSXE_COMPLIANT_NO_REDIRECTS) is True

    def test_no_icmp_redirects_noncompliant(self):
        assert _check_iosxe_no_icmp_redirects(IOSXE_NONCOMPLIANT_REDIRECTS) is False


# ============================================================================
# NX-OS individual check function tests
# ============================================================================


class TestNxosCheckFunctions:
    """Test each NX-OS check function with compliant and non-compliant configs."""

    def test_ssh_enabled_compliant(self):
        assert _check_nxos_ssh_enabled(NXOS_COMPLIANT_SSH) is True

    def test_ssh_enabled_noncompliant(self):
        assert _check_nxos_ssh_enabled(NXOS_NONCOMPLIANT_SSH) is False

    def test_ssh_kex_hardened_compliant(self):
        assert _check_nxos_ssh_kex_hardened(NXOS_COMPLIANT_SSH_KEX) is True

    def test_ssh_kex_hardened_noncompliant(self):
        assert _check_nxos_ssh_kex_hardened(NXOS_NONCOMPLIANT_SSH_KEX) is False

    def test_aaa_compliant(self):
        assert _check_nxos_aaa_configured(NXOS_COMPLIANT_AAA) is True

    def test_aaa_noncompliant(self):
        assert _check_nxos_aaa_configured(NXOS_NONCOMPLIANT_AAA) is False

    def test_password_strength_compliant(self):
        assert _check_nxos_password_strength(NXOS_COMPLIANT_PASSWORD_STRENGTH) is True

    def test_password_strength_noncompliant(self):
        assert _check_nxos_password_strength(NXOS_NONCOMPLIANT_PASSWORD_STRENGTH) is False

    def test_login_banner_compliant(self):
        assert _check_nxos_login_banner(NXOS_COMPLIANT_BANNER) is True

    def test_login_banner_noncompliant(self):
        assert _check_nxos_login_banner(NXOS_NONCOMPLIANT_BANNER) is False

    def test_https_management_compliant(self):
        assert _check_nxos_https_management(NXOS_COMPLIANT_HTTPS) is True

    def test_https_management_noncompliant(self):
        assert _check_nxos_https_management(NXOS_NONCOMPLIANT_HTTPS) is False

    def test_ntp_compliant(self):
        assert _check_nxos_ntp_configured(NXOS_COMPLIANT_NTP) is True

    def test_ntp_noncompliant(self):
        assert _check_nxos_ntp_configured(NXOS_NONCOMPLIANT_NTP) is False

    def test_logging_remote_compliant(self):
        assert _check_nxos_logging_remote(NXOS_COMPLIANT_LOGGING) is True

    def test_logging_remote_noncompliant(self):
        assert _check_nxos_logging_remote(NXOS_NONCOMPLIANT_LOGGING) is False

    def test_snmp_no_defaults_compliant(self):
        assert _check_nxos_snmp_no_defaults(NXOS_COMPLIANT_SNMP) is True

    def test_snmp_no_defaults_noncompliant(self):
        assert _check_nxos_snmp_no_defaults(NXOS_NONCOMPLIANT_SNMP) is False

    def test_snmp_no_defaults_empty(self):
        assert _check_nxos_snmp_no_defaults("") is True

    def test_copp_compliant(self):
        assert _check_nxos_copp_configured(NXOS_COMPLIANT_COPP) is True

    def test_copp_service_policy(self):
        assert _check_nxos_copp_configured("service-policy input copp-system-p-policy") is True

    def test_copp_noncompliant(self):
        assert _check_nxos_copp_configured(NXOS_NONCOMPLIANT_COPP) is False

    def test_nxapi_secured_compliant(self):
        assert _check_nxos_nxapi_secured(NXOS_COMPLIANT_NXAPI_SECURED) is True

    def test_nxapi_disabled_compliant(self):
        assert _check_nxos_nxapi_secured(NXOS_COMPLIANT_NXAPI_DISABLED) is True

    def test_nxapi_noncompliant(self):
        assert _check_nxos_nxapi_secured(NXOS_NONCOMPLIANT_NXAPI) is False

    def test_unused_interfaces_shutdown_compliant(self):
        assert _check_nxos_unused_interfaces_shutdown(NXOS_COMPLIANT_INTERFACE_SHUTDOWN) is True

    def test_unused_interfaces_shutdown_noncompliant(self):
        assert _check_nxos_unused_interfaces_shutdown(NXOS_NONCOMPLIANT_INTERFACE) is False

    def test_dhcp_snooping_compliant(self):
        assert _check_nxos_dhcp_snooping(NXOS_COMPLIANT_DHCP) is True

    def test_dhcp_snooping_noncompliant(self):
        assert _check_nxos_dhcp_snooping(NXOS_NONCOMPLIANT_DHCP) is False

    def test_arp_inspection_compliant(self):
        assert _check_nxos_arp_inspection(NXOS_COMPLIANT_ARP_INSPECTION) is True

    def test_arp_inspection_noncompliant(self):
        assert _check_nxos_arp_inspection(NXOS_NONCOMPLIANT_ARP) is False

    def test_port_security_compliant(self):
        assert _check_nxos_port_security(NXOS_COMPLIANT_PORT_SECURITY) is True

    def test_port_security_dot1x(self):
        assert _check_nxos_port_security("dot1x system-auth-control") is True

    def test_port_security_noncompliant(self):
        assert _check_nxos_port_security(NXOS_NONCOMPLIANT_PORT_SECURITY) is False


# ============================================================================
# IOS-XE check registry tests
# ============================================================================


class TestIosXeCheckRegistry:
    def test_all_checks_have_required_fields(self):
        for check in CIS_IOSXE_CHECKS:
            assert check.id.startswith("CIS-IOSXE-")
            assert len(check.title) > 0
            assert check.severity in ("HIGH", "MEDIUM", "LOW")
            assert check.category in ("management", "control", "data")
            assert len(check.command) > 0
            assert check.encoding in ("text", "json")
            assert callable(check.check_fn)
            assert len(check.remediation) > 0

    def test_check_ids_are_unique(self):
        ids = [c.id for c in CIS_IOSXE_CHECKS]
        assert len(ids) == len(set(ids)), f"Duplicate check IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_minimum_check_count(self):
        """Ensure at least 10 checks are defined."""
        assert len(CIS_IOSXE_CHECKS) >= 10

    def test_category_coverage(self):
        """Ensure checks span all three categories."""
        categories = {c.category for c in CIS_IOSXE_CHECKS}
        assert categories == {"management", "control", "data"}


# ============================================================================
# NX-OS check registry tests
# ============================================================================


class TestNxosCheckRegistry:
    def test_all_checks_have_required_fields(self):
        for check in CIS_NXOS_CHECKS:
            assert check.id.startswith("CIS-NXOS-")
            assert len(check.title) > 0
            assert check.severity in ("HIGH", "MEDIUM", "LOW")
            assert check.category in ("management", "control", "data")
            assert len(check.command) > 0
            assert check.encoding in ("text", "json")
            assert callable(check.check_fn)
            assert len(check.remediation) > 0

    def test_check_ids_are_unique(self):
        ids = [c.id for c in CIS_NXOS_CHECKS]
        assert len(ids) == len(set(ids)), f"Duplicate check IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_minimum_check_count(self):
        """Ensure at least 10 checks are defined."""
        assert len(CIS_NXOS_CHECKS) >= 10

    def test_category_coverage(self):
        """Ensure checks span all three categories."""
        categories = {c.category for c in CIS_NXOS_CHECKS}
        assert categories == {"management", "control", "data"}


# ============================================================================
# IOS-XE compliance tool integration tests
# ============================================================================


class TestIosXeComplianceCheck:
    def test_compliant_iosxe_device(self, mock_conn_mgr, mock_node):
        """Fully compliant IOS-XE device returns all PASS results."""
        responses = {
            "show running-config | include ip ssh": _make_text_response(
                IOSXE_COMPLIANT_SSH + "\nip ssh authentication-retries 3"
            ),
            "show running-config | section aaa": _make_text_response(
                IOSXE_COMPLIANT_AAA
                + "\naaa accounting exec default start-stop group tacacs+\n"
                + "aaa authorization exec default group tacacs+ local"
            ),
            "show running-config | include service password": _make_text_response(IOSXE_COMPLIANT_PASSWORD_ENCRYPTION),
            "show running-config | section banner": _make_text_response(
                IOSXE_COMPLIANT_BANNER + "\nbanner motd ^Authorized access only.^"
            ),
            "show running-config | include ip http": _make_text_response(IOSXE_COMPLIANT_HTTP),
            "show running-config | include security passwords": _make_text_response("security passwords min-length 8"),
            "show running-config | section line aux": _make_text_response(
                "line aux 0\n no exec\n transport input none"
            ),
            "show running-config | section ntp": _make_text_response(IOSXE_COMPLIANT_NTP + "\nntp trusted-key 1"),
            "show running-config | include logging host": _make_text_response(IOSXE_COMPLIANT_LOGGING),
            "show running-config | include logging buffered": _make_text_response("logging buffered 100000"),
            "show running-config | include service timestamps": _make_text_response(
                "service timestamps log datetime msec"
            ),
            "show running-config | include snmp-server community": _make_text_response(
                IOSXE_COMPLIANT_SNMP + "\nsnmp-server community MySecretString ro access SNMP_ACL"
            ),
            "show running-config | include enable secret": _make_text_response(IOSXE_COMPLIANT_ENABLE_SECRET),
            "show running-config | include snmp-server": _make_text_response("snmp-server group MYGROUP v3 priv"),
            "show running-config | section line vty": _make_text_response(
                IOSXE_COMPLIANT_VTY + "\n access-class SSH_ACL in"
            ),
            "show running-config | section line con": _make_text_response("line con 0\n exec-timeout 10 0"),
            "show running-config | section interface": _make_text_response(
                IOSXE_COMPLIANT_INTERFACE_SHUTDOWN
                + "\n"
                + IOSXE_COMPLIANT_NO_REDIRECTS
                + "\n no ip proxy-arp\n no ip unreachables"
                + "\n ip verify unicast source reachable-via rx"
                + "\n storm-control broadcast level 10"
            ),
            "show running-config | include cdp": _make_text_response(IOSXE_COMPLIANT_CDP),
            "show running-config | include ip source-route": _make_text_response(IOSXE_COMPLIANT_NO_SOURCE_ROUTE),
            "show running-config | include tcp-keepalives": _make_text_response(
                "service tcp-keepalives-in\nservice tcp-keepalives-out"
            ),
        }

        def side_effect(commands, encoding="json"):  # noqa: ARG001
            cmd = commands[0]
            if cmd in responses:
                return responses[cmd]
            return [{"output": ""}]

        mock_node.run_commands.side_effect = side_effect

        result = eos_compliance_check("router-01", framework="cis-iosxe")
        assert result["status"] == "success"
        assert result["device"] == "router-01"
        assert result["data"]["framework"] == "cis-iosxe"
        assert result["data"]["total_checks"] == len(CIS_IOSXE_CHECKS)
        assert result["data"]["passed"] == len(CIS_IOSXE_CHECKS)
        assert result["data"]["failed"] == 0
        assert result["data"]["pass_rate"] == "100.0%"

    def test_noncompliant_iosxe_device(self, mock_conn_mgr, mock_node):
        """Non-compliant IOS-XE device returns FAIL results with remediation."""
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("router-01", framework="cis-iosxe")
        assert result["status"] == "success"
        assert result["data"]["failed"] > 0

        failed_results = [r for r in result["data"]["results"] if r["status"] == "FAIL"]
        assert len(failed_results) > 0
        for r in failed_results:
            assert "remediation" in r

    def test_iosxe_category_filter(self, mock_conn_mgr, mock_node):
        """Category filter only runs the selected category."""
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("router-01", framework="cis-iosxe", category="management")
        assert result["status"] == "success"

        management_checks = [c for c in CIS_IOSXE_CHECKS if c.category == "management"]
        assert result["data"]["total_checks"] == len(management_checks)

        for r in result["data"]["results"]:
            assert r["category"] == "management"


# ============================================================================
# NX-OS compliance tool integration tests
# ============================================================================


class TestNxosComplianceCheck:
    def test_compliant_nxos_device(self, mock_conn_mgr, mock_node):
        """Fully compliant NX-OS device returns all PASS results."""
        responses = {
            "show running-config | include feature ssh": _make_text_response(NXOS_COMPLIANT_SSH),
            "show running-config | include ssh": _make_text_response(NXOS_COMPLIANT_SSH_KEX + "\nssh login-attempts 3"),
            "show running-config | section aaa": _make_text_response(
                NXOS_COMPLIANT_AAA
                + "\naaa accounting default group tacacs+\naaa authorization commands default group tacacs+ local"
            ),
            "show running-config | include password strength": _make_text_response(NXOS_COMPLIANT_PASSWORD_STRENGTH),
            "show running-config | section banner": _make_text_response(
                NXOS_COMPLIANT_BANNER + "\nbanner exec @Authorized access only.@"
            ),
            "show running-config | include role": _make_text_response("role name network-operator"),
            "show running-config | include nxapi": _make_text_response(NXOS_COMPLIANT_HTTPS),
            "show running-config | section line": _make_text_response("line console\n  exec-timeout 10"),
            "show running-config | include ntp server": _make_text_response(NXOS_COMPLIANT_NTP),
            "show running-config | include ntp": _make_text_response(
                NXOS_COMPLIANT_NTP + "\nntp authenticate\nntp authentication-key 1 md5 KEY\nntp trusted-key 1"
            ),
            "show running-config | include logging server": _make_text_response(NXOS_COMPLIANT_LOGGING),
            "show running-config | include logging": _make_text_response(
                NXOS_COMPLIANT_LOGGING + "\nlogging level local7 6\nlogging timestamp milliseconds"
            ),
            "show running-config | include snmp-server community": _make_text_response(NXOS_COMPLIANT_SNMP),
            "show running-config | include copp|service-policy": _make_text_response(NXOS_COMPLIANT_COPP),
            "show running-config | include snmp-server": _make_text_response(
                "snmp-server user MYUSER MYGROUP v3 auth sha KEY priv aes128 KEY\n"
                + "snmp-server community MySecretString group network-operator use-acl SNMP_ACL"
            ),
            "show running-config | include tacacs": _make_text_response("ip tacacs source-interface Management0"),
            "show running-config | include radius": _make_text_response("ip radius source-interface Management0"),
            "show running-config | section interface": _make_text_response(
                NXOS_COMPLIANT_INTERFACE_SHUTDOWN
                + "\n"
                + NXOS_COMPLIANT_PORT_SECURITY
                + "\n  storm-control broadcast level 10"
                + "\n  ip verify source dhcp-snooping-vlan"
                + "\n  ip verify unicast source reachable-via rx"
            ),
            "show running-config | include dhcp": _make_text_response(NXOS_COMPLIANT_DHCP),
            "show running-config | include spanning-tree": _make_text_response(
                "spanning-tree port type edge bpduguard default"
            ),
            "show running-config | include arp inspection": _make_text_response(NXOS_COMPLIANT_ARP_INSPECTION),
            "show running-config | section access-list": _make_text_response("ip access-list ACL1\n  deny ip any any"),
            "show running-config | section vpc": _make_text_response(
                "vpc domain 1\n  peer-keepalive destination 10.0.0.1 source 10.0.0.2 vrf management"
            ),
        }

        def side_effect(commands, encoding="json"):  # noqa: ARG001
            cmd = commands[0]
            if cmd in responses:
                return responses[cmd]
            return [{"output": ""}]

        mock_node.run_commands.side_effect = side_effect

        result = eos_compliance_check("nexus-01", framework="cis-nxos")
        assert result["status"] == "success"
        assert result["device"] == "nexus-01"
        assert result["data"]["framework"] == "cis-nxos"
        assert result["data"]["total_checks"] == len(CIS_NXOS_CHECKS)
        assert result["data"]["passed"] == len(CIS_NXOS_CHECKS)
        assert result["data"]["failed"] == 0
        assert result["data"]["pass_rate"] == "100.0%"

    def test_noncompliant_nxos_device(self, mock_conn_mgr, mock_node):
        """Non-compliant NX-OS device returns FAIL results with remediation."""
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("nexus-01", framework="cis-nxos")
        assert result["status"] == "success"
        assert result["data"]["failed"] > 0

        failed_results = [r for r in result["data"]["results"] if r["status"] == "FAIL"]
        assert len(failed_results) > 0
        for r in failed_results:
            assert "remediation" in r

    def test_nxos_category_filter(self, mock_conn_mgr, mock_node):
        """Category filter only runs the selected category."""
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("nexus-01", framework="cis-nxos", category="control")
        assert result["status"] == "success"

        control_checks = [c for c in CIS_NXOS_CHECKS if c.category == "control"]
        assert result["data"]["total_checks"] == len(control_checks)

        for r in result["data"]["results"]:
            assert r["category"] == "control"


# ============================================================================
# Multi-framework list tool tests
# ============================================================================


class TestListComplianceChecksMultiFramework:
    def test_list_all_frameworks(self):
        """Calling with framework=None shows all frameworks."""
        result = eos_list_compliance_checks(framework=None)
        assert result["status"] == "success"
        assert result["data"]["total_frameworks"] == 3
        assert "cis-eos" in result["data"]["frameworks"]
        assert "cis-iosxe" in result["data"]["frameworks"]
        assert "cis-nxos" in result["data"]["frameworks"]

        total = sum(fw["total_checks"] for fw in result["data"]["frameworks"].values())
        assert result["data"]["total_checks"] == total

    def test_list_iosxe_checks(self):
        result = eos_list_compliance_checks(framework="cis-iosxe")
        assert result["status"] == "success"
        assert result["data"]["framework"] == "cis-iosxe"
        assert result["data"]["total_checks"] == len(CIS_IOSXE_CHECKS)

    def test_list_nxos_checks(self):
        result = eos_list_compliance_checks(framework="cis-nxos")
        assert result["status"] == "success"
        assert result["data"]["framework"] == "cis-nxos"
        assert result["data"]["total_checks"] == len(CIS_NXOS_CHECKS)

    def test_list_invalid_framework(self):
        result = eos_list_compliance_checks(framework="cis-junos")
        assert result["status"] == "error"
        assert "Unsupported framework" in result["error"]

    def test_valid_frameworks_tuple_matches_registry(self):
        """VALID_FRAMEWORKS tuple should match the _FRAMEWORK_CHECKS keys."""
        assert set(VALID_FRAMEWORKS) == {"cis-eos", "cis-iosxe", "cis-nxos"}

    def test_all_framework_checks_have_categories(self):
        """Every check across all frameworks has valid categories."""
        result = eos_list_compliance_checks(framework=None)
        for fw_data in result["data"]["frameworks"].values():
            for check in fw_data["checks"]:
                assert check["category"] in ("management", "control", "data")

    def test_all_framework_checks_have_severities(self):
        """Every check across all frameworks has valid severities."""
        result = eos_list_compliance_checks(framework=None)
        for fw_data in result["data"]["frameworks"].values():
            for check in fw_data["checks"]:
                assert check["severity"] in ("HIGH", "MEDIUM", "LOW")
