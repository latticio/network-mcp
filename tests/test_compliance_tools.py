"""Tests for CIS compliance checking framework (Arista EOS)."""

import pytest

from network_mcp.helpers import device_rate_limiter
from network_mcp.tools.compliance import (
    CIS_CHECKS,
    CIS_EOS_CHECKS,
    _check_eos_aaa_configured,
    _check_eos_console_timeout,
    _check_eos_copp_configured,
    _check_eos_dhcp_snooping,
    _check_eos_eapi_https_only,
    _check_eos_enable_secret,
    _check_eos_logging_remote,
    _check_eos_login_banner,
    _check_eos_ntp_configured,
    _check_eos_password_encryption,
    _check_eos_snmp_no_defaults,
    _check_eos_ssh_enabled,
    _check_eos_ssh_idle_timeout,
    _check_eos_stp_guard,
    _check_eos_unused_interfaces_shutdown,
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


# --- Compliant running-config fragments ---

COMPLIANT_SSH = """\
management ssh
   idle-timeout 15
   no shutdown
"""

COMPLIANT_AAA = """\
aaa authentication login default group tacacs+ local
aaa authorization exec default group tacacs+ local
"""

COMPLIANT_PASSWORD_ENCRYPTION = """\
service password-encryption
"""

COMPLIANT_BANNER = """\
banner login
Authorized access only. All activity is monitored.
EOF
"""

COMPLIANT_EAPI = """\
management api http-commands
   protocol https
   no protocol http
   no shutdown
"""

COMPLIANT_NTP = """\
ntp server 10.10.10.1
ntp server 10.10.10.2
"""

COMPLIANT_LOGGING = """\
logging host 192.168.1.100
logging host 192.168.1.101
"""

COMPLIANT_SNMP = """\
snmp-server community MySecretCommunity123 ro
"""

COMPLIANT_ENABLE_SECRET = """\
enable secret sha512 $6$abc123
"""

COMPLIANT_CONSOLE_TIMEOUT = """\
line con 0
   exec-timeout 10 0
line vty 0 15
   exec-timeout 10 0
"""

COMPLIANT_INTERFACE_SHUTDOWN = """\
interface Ethernet1
   description to-spine-01
   no shutdown
!
interface Ethernet2
   shutdown
"""

COMPLIANT_STP_GUARD = """\
spanning-tree bpduguard enable
"""

COMPLIANT_DHCP_SNOOPING = """\
ip dhcp snooping
ip dhcp snooping vlan 100
"""

COMPLIANT_COPP = """\
system control-plane
   ip access-group COPP-ACL in
"""

# --- Non-compliant running-config fragments ---

NONCOMPLIANT_SSH = """\
management ssh
   shutdown
"""

NONCOMPLIANT_SSH_MISSING = """\
"""

NONCOMPLIANT_AAA = """\
aaa authentication login default local
"""

NONCOMPLIANT_PASSWORD = """\
no service password-encryption
"""

NONCOMPLIANT_BANNER = """\
"""

NONCOMPLIANT_EAPI = """\
management api http-commands
   protocol http
   protocol https
   no shutdown
"""

NONCOMPLIANT_NTP = """\
"""

NONCOMPLIANT_LOGGING = """\
logging buffered 100000
"""

NONCOMPLIANT_SNMP_PUBLIC = """\
snmp-server community public ro
"""

NONCOMPLIANT_SNMP_PRIVATE = """\
snmp-server community private rw
"""

NONCOMPLIANT_ENABLE = """\
enable password 0 admin123
"""

NONCOMPLIANT_CONSOLE_TIMEOUT = """\
line con 0
line vty 0 15
"""

NONCOMPLIANT_INTERFACE = """\
interface Ethernet1
   description to-spine-01
   no switchport
"""

NONCOMPLIANT_STP = """\
spanning-tree mode mstp
"""

NONCOMPLIANT_DHCP = """\
"""

NONCOMPLIANT_COPP = """\
"""


# --- Individual check function tests ---


class TestCheckFunctions:
    """Test each individual check function with compliant and non-compliant configs."""

    def test_ssh_enabled_compliant(self):
        assert _check_eos_ssh_enabled(COMPLIANT_SSH) is True

    def test_ssh_enabled_noncompliant_shutdown(self):
        assert _check_eos_ssh_enabled(NONCOMPLIANT_SSH) is False

    def test_ssh_enabled_noncompliant_missing(self):
        assert _check_eos_ssh_enabled(NONCOMPLIANT_SSH_MISSING) is False

    def test_ssh_idle_timeout_compliant(self):
        assert _check_eos_ssh_idle_timeout(COMPLIANT_SSH) is True

    def test_ssh_idle_timeout_noncompliant(self):
        assert _check_eos_ssh_idle_timeout(NONCOMPLIANT_SSH) is False

    def test_aaa_compliant(self):
        assert _check_eos_aaa_configured(COMPLIANT_AAA) is True

    def test_aaa_noncompliant(self):
        assert _check_eos_aaa_configured(NONCOMPLIANT_AAA) is False

    def test_password_encryption_compliant(self):
        assert _check_eos_password_encryption(COMPLIANT_PASSWORD_ENCRYPTION) is True

    def test_password_encryption_noncompliant(self):
        assert _check_eos_password_encryption(NONCOMPLIANT_PASSWORD) is False

    def test_login_banner_compliant(self):
        assert _check_eos_login_banner(COMPLIANT_BANNER) is True

    def test_login_banner_noncompliant(self):
        assert _check_eos_login_banner(NONCOMPLIANT_BANNER) is False

    def test_eapi_https_only_compliant(self):
        assert _check_eos_eapi_https_only(COMPLIANT_EAPI) is True

    def test_eapi_https_only_noncompliant(self):
        assert _check_eos_eapi_https_only(NONCOMPLIANT_EAPI) is False

    def test_ntp_compliant(self):
        assert _check_eos_ntp_configured(COMPLIANT_NTP) is True

    def test_ntp_noncompliant(self):
        assert _check_eos_ntp_configured(NONCOMPLIANT_NTP) is False

    def test_logging_remote_compliant(self):
        assert _check_eos_logging_remote(COMPLIANT_LOGGING) is True

    def test_logging_remote_noncompliant(self):
        assert _check_eos_logging_remote(NONCOMPLIANT_LOGGING) is False

    def test_snmp_no_defaults_compliant(self):
        assert _check_eos_snmp_no_defaults(COMPLIANT_SNMP) is True

    def test_snmp_no_defaults_public(self):
        assert _check_eos_snmp_no_defaults(NONCOMPLIANT_SNMP_PUBLIC) is False

    def test_snmp_no_defaults_private(self):
        assert _check_eos_snmp_no_defaults(NONCOMPLIANT_SNMP_PRIVATE) is False

    def test_snmp_no_defaults_empty(self):
        """No SNMP community configured at all should pass (no defaults present)."""
        assert _check_eos_snmp_no_defaults("") is True

    def test_enable_secret_compliant(self):
        assert _check_eos_enable_secret(COMPLIANT_ENABLE_SECRET) is True

    def test_enable_secret_noncompliant(self):
        assert _check_eos_enable_secret(NONCOMPLIANT_ENABLE) is False

    def test_console_timeout_compliant(self):
        assert _check_eos_console_timeout(COMPLIANT_CONSOLE_TIMEOUT) is True

    def test_console_timeout_noncompliant(self):
        assert _check_eos_console_timeout(NONCOMPLIANT_CONSOLE_TIMEOUT) is False

    def test_unused_interfaces_shutdown_compliant(self):
        assert _check_eos_unused_interfaces_shutdown(COMPLIANT_INTERFACE_SHUTDOWN) is True

    def test_unused_interfaces_shutdown_noncompliant(self):
        assert _check_eos_unused_interfaces_shutdown(NONCOMPLIANT_INTERFACE) is False

    def test_stp_guard_bpduguard(self):
        assert _check_eos_stp_guard(COMPLIANT_STP_GUARD) is True

    def test_stp_guard_rootguard(self):
        assert _check_eos_stp_guard("spanning-tree guard root") is True

    def test_stp_guard_noncompliant(self):
        assert _check_eos_stp_guard(NONCOMPLIANT_STP) is False

    def test_dhcp_snooping_compliant(self):
        assert _check_eos_dhcp_snooping(COMPLIANT_DHCP_SNOOPING) is True

    def test_dhcp_snooping_arp_inspection(self):
        assert _check_eos_dhcp_snooping("ip arp inspection vlan 100") is True

    def test_dhcp_snooping_noncompliant(self):
        assert _check_eos_dhcp_snooping(NONCOMPLIANT_DHCP) is False

    def test_copp_compliant(self):
        assert _check_eos_copp_configured(COMPLIANT_COPP) is True

    def test_copp_policy_map(self):
        assert _check_eos_copp_configured("policy-map COPP") is True

    def test_copp_noncompliant(self):
        assert _check_eos_copp_configured(NONCOMPLIANT_COPP) is False


# --- eos_list_compliance_checks tool tests ---


class TestListComplianceChecks:
    def test_list_cis_eos_checks_returns_all(self):
        result = eos_list_compliance_checks(framework="cis-eos")
        assert result["status"] == "success"
        assert result["data"]["framework"] == "cis-eos"
        assert result["data"]["total_checks"] == len(CIS_EOS_CHECKS)
        assert len(result["data"]["checks"]) == len(CIS_EOS_CHECKS)

    def test_list_checks_structure(self):
        result = eos_list_compliance_checks(framework="cis-eos")
        for check in result["data"]["checks"]:
            assert "id" in check
            assert "title" in check
            assert "severity" in check
            assert "category" in check
            assert check["severity"] in ("HIGH", "MEDIUM", "LOW")
            assert check["category"] in ("management", "control", "data")

    def test_list_checks_invalid_framework(self):
        result = eos_list_compliance_checks(framework="nist")
        assert result["status"] == "error"
        assert "Unsupported framework" in result["error"]

    def test_list_checks_has_management_control_data(self):
        result = eos_list_compliance_checks(framework="cis-eos")
        categories = {c["category"] for c in result["data"]["checks"]}
        assert "management" in categories
        assert "control" in categories
        assert "data" in categories

    def test_backward_compat_alias(self):
        """CIS_CHECKS alias points to the same list as CIS_EOS_CHECKS."""
        assert CIS_CHECKS is CIS_EOS_CHECKS


# --- eos_compliance_check tool tests ---


def _make_text_response(text: str) -> list:
    """Build a mock run_commands return value for text encoding."""
    return [{"output": text}]


class TestComplianceCheck:
    def test_compliant_device(self, mock_conn_mgr, mock_node):
        """Fully compliant device returns all PASS results."""
        responses = {
            "show running-config section management ssh": _make_text_response(
                COMPLIANT_SSH + "\n   access-class SSH_ACL\n   protocol-version 2"
            ),
            "show running-config section aaa": _make_text_response(
                COMPLIANT_AAA
                + "\naaa accounting commands all default start-stop group tacacs+\n"
                + "aaa authorization exec default group tacacs+ local\n"
                + "aaa password policy\n   minimum-length 10\n"
            ),
            "show running-config section service": _make_text_response(COMPLIANT_PASSWORD_ENCRYPTION),
            "show running-config section banner": _make_text_response(
                COMPLIANT_BANNER + "\nbanner motd\nAuthorized access only.\nEOF"
            ),
            "show running-config section management api http-commands": _make_text_response(COMPLIANT_EAPI),
            "show running-config section ntp": _make_text_response(
                COMPLIANT_NTP + "\nntp authenticate\nntp authentication-key 1 md5 KEY\nntp trusted-key 1"
            ),
            "show running-config section logging": _make_text_response(
                COMPLIANT_LOGGING + "\nlogging trap informational\nlogging buffered 100000"
            ),
            "show running-config section snmp-server": _make_text_response(
                COMPLIANT_SNMP
                + "\nsnmp-server group MYGROUP v3 priv\nsnmp-server community MySecretCommunity123 ro access SNMP_ACL"
            ),
            "show running-config section enable": _make_text_response(COMPLIANT_ENABLE_SECRET),
            "show running-config section line": _make_text_response(COMPLIANT_CONSOLE_TIMEOUT),
            "show running-config section interface": _make_text_response(
                COMPLIANT_INTERFACE_SHUTDOWN
                + "\n   storm-control broadcast level 10\n   ip verify source"
                + "\n   ip verify unicast source reachable-via rx"
                + "\n   no ip directed-broadcast"
            ),
            "show running-config section spanning-tree": _make_text_response(
                COMPLIANT_STP_GUARD + "\nspanning-tree mode mstp"
            ),
            "show running-config section dhcp": _make_text_response(COMPLIANT_DHCP_SNOOPING),
            "show running-config section system control-plane": _make_text_response(
                COMPLIANT_COPP + "\n   police rate 100"
            ),
            "show running-config section arp": _make_text_response("ip arp inspection vlan 100"),
            "show running-config section access-list": _make_text_response("ip access-list COPP-ACL\n   deny any"),
            "show running-config section lldp": _make_text_response("no lldp transmit"),
            "show running-config section management": _make_text_response(
                "vrf instance MGMT\ninterface Management1\n   vrf MGMT"
            ),
            "show running-config section errdisable": _make_text_response(
                "errdisable recovery cause bpduguard\nerrdisable recovery interval 300"
            ),
            "show running-config section radius": _make_text_response("ip radius source-interface Management1"),
            "show running-config section tacacs": _make_text_response("ip tacacs source-interface Management1"),
        }

        def side_effect(commands, encoding="json"):  # noqa: ARG001
            cmd = commands[0]
            if cmd in responses:
                return responses[cmd]
            return [{"output": ""}]

        mock_node.run_commands.side_effect = side_effect

        result = eos_compliance_check("spine-01", framework="cis-eos")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["data"]["framework"] == "cis-eos"
        assert result["data"]["total_checks"] == len(CIS_EOS_CHECKS)
        assert result["data"]["passed"] == len(CIS_EOS_CHECKS)
        assert result["data"]["failed"] == 0
        assert result["data"]["pass_rate"] == "100.0%"

        # No remediation keys on PASS results
        for r in result["data"]["results"]:
            assert r["status"] == "PASS"
            assert "remediation" not in r

    def test_noncompliant_device(self, mock_conn_mgr, mock_node):
        """Non-compliant device returns FAIL results with remediation."""
        # Return empty/non-compliant output for all commands
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("spine-01", framework="cis-eos")
        assert result["status"] == "success"
        assert result["data"]["failed"] > 0

        failed_results = [r for r in result["data"]["results"] if r["status"] == "FAIL"]
        assert len(failed_results) > 0

        # Every FAIL result must have remediation
        for r in failed_results:
            assert "remediation" in r
            assert len(r["remediation"]) > 0

    def test_category_filter_management(self, mock_conn_mgr, mock_node):
        """Category filter only runs management checks."""
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("spine-01", framework="cis-eos", category="management")
        assert result["status"] == "success"

        management_checks = [c for c in CIS_EOS_CHECKS if c.category == "management"]
        assert result["data"]["total_checks"] == len(management_checks)

        for r in result["data"]["results"]:
            assert r["category"] == "management"

    def test_category_filter_control(self, mock_conn_mgr, mock_node):
        """Category filter only runs control checks."""
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("spine-01", framework="cis-eos", category="control")
        assert result["status"] == "success"

        control_checks = [c for c in CIS_EOS_CHECKS if c.category == "control"]
        assert result["data"]["total_checks"] == len(control_checks)

        for r in result["data"]["results"]:
            assert r["category"] == "control"

    def test_category_filter_data(self, mock_conn_mgr, mock_node):
        """Category filter only runs data checks."""
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("spine-01", framework="cis-eos", category="data")
        assert result["status"] == "success"

        data_checks = [c for c in CIS_EOS_CHECKS if c.category == "data"]
        assert result["data"]["total_checks"] == len(data_checks)

        for r in result["data"]["results"]:
            assert r["category"] == "data"

    def test_invalid_framework(self, mock_conn_mgr):
        result = eos_compliance_check("spine-01", framework="nist")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "Unsupported framework" in result["error"]

    def test_invalid_category(self, mock_conn_mgr):
        result = eos_compliance_check("spine-01", framework="cis-eos", category="invalid")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "Invalid category" in result["error"]

    def test_invalid_host(self):
        """Invalid host triggers ToolError from validate_host."""
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            eos_compliance_check("", framework="cis-eos")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        """Device connection error is propagated properly."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "Connection refused")

        result = eos_compliance_check("spine-01", framework="cis-eos")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "Connection" in result["error"]

    def test_result_structure(self, mock_conn_mgr, mock_node):
        """Verify result dict has all required keys."""
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_check("spine-01")
        assert "status" in result
        assert "device" in result
        assert "data" in result

        data = result["data"]
        assert "framework" in data
        assert "total_checks" in data
        assert "passed" in data
        assert "failed" in data
        assert "pass_rate" in data
        assert "results" in data

        for r in data["results"]:
            assert "id" in r
            assert "title" in r
            assert "severity" in r
            assert "category" in r
            assert "status" in r
            assert "nist_controls" in r

    def test_pass_rate_calculation(self, mock_conn_mgr, mock_node):
        """Verify pass rate is calculated correctly."""

        # Make only SSH checks pass, everything else fail
        def side_effect(commands, encoding="json"):  # noqa: ARG001
            cmd = commands[0]
            if "management ssh" in cmd:
                return _make_text_response(COMPLIANT_SSH)
            return [{"output": ""}]

        mock_node.run_commands.side_effect = side_effect

        result = eos_compliance_check("spine-01", framework="cis-eos")
        assert result["status"] == "success"
        total = result["data"]["total_checks"]
        passed = result["data"]["passed"]
        failed = result["data"]["failed"]
        assert passed + failed == total
        expected_rate = f"{(passed / total * 100):.1f}%"
        assert result["data"]["pass_rate"] == expected_rate

    def test_mixed_compliance(self, mock_conn_mgr, mock_node):
        """Test a mix of passing and failing checks."""
        responses = {
            "show running-config section management ssh": _make_text_response(COMPLIANT_SSH),
            "show running-config section ntp": _make_text_response(COMPLIANT_NTP),
        }

        def side_effect(commands, encoding="json"):  # noqa: ARG001
            cmd = commands[0]
            if cmd in responses:
                return responses[cmd]
            return [{"output": ""}]

        mock_node.run_commands.side_effect = side_effect

        result = eos_compliance_check("spine-01")
        assert result["status"] == "success"
        assert result["data"]["passed"] > 0
        assert result["data"]["failed"] > 0

        statuses = {r["status"] for r in result["data"]["results"]}
        assert "PASS" in statuses
        assert "FAIL" in statuses


# --- CIS check registry tests ---


class TestCheckRegistry:
    def test_all_checks_have_required_fields(self):
        for check in CIS_EOS_CHECKS:
            assert check.id.startswith("CIS-EOS-")
            assert len(check.title) > 0
            assert check.severity in ("HIGH", "MEDIUM", "LOW")
            assert check.category in ("management", "control", "data")
            assert len(check.command) > 0
            assert check.encoding in ("text", "json")
            assert callable(check.check_fn)
            assert len(check.remediation) > 0

    def test_check_ids_are_unique(self):
        ids = [c.id for c in CIS_EOS_CHECKS]
        assert len(ids) == len(set(ids)), f"Duplicate check IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_minimum_check_count(self):
        """Ensure at least 10 checks are defined as required by the spec."""
        assert len(CIS_EOS_CHECKS) >= 10

    def test_category_coverage(self):
        """Ensure checks span all three categories."""
        categories = {c.category for c in CIS_EOS_CHECKS}
        assert categories == {"management", "control", "data"}
