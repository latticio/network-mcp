"""Tests for the expanded compliance framework: structured parsing, value checks, NIST mappings, report tool."""

import re

import pytest

from network_mcp.helpers import device_rate_limiter
from network_mcp.tools.compliance import (
    _FRAMEWORK_CHECKS,
    CIS_EOS_CHECKS,
    CIS_IOSXE_CHECKS,
    CIS_NXOS_CHECKS,
    ComplianceCheck,
    ComplianceResult,
    _check_eos_aaa_accounting,
    _check_eos_aaa_authorization,
    _check_eos_arp_inspection,
    _check_eos_console_timeout_value,
    _check_eos_copp_rate_limit,
    _check_eos_errdisable_recovery,
    _check_eos_ip_source_guard,
    _check_eos_lldp_restricted,
    _check_eos_logging_buffered,
    _check_eos_logging_level,
    _check_eos_mgmt_vrf,
    _check_eos_motd_banner,
    _check_eos_ntp_authentication,
    _check_eos_ntp_trusted_key,
    _check_eos_password_min_length,
    _check_eos_radius_source_interface,
    _check_eos_snmp_acl,
    _check_eos_snmp_v3,
    _check_eos_ssh_timeout_value,
    _check_eos_ssh_timeout_value_bool,
    _check_eos_ssh_version_2,
    _check_eos_storm_control,
    _check_eos_stp_mode,
    _check_eos_tacacs_source_interface,
    _check_eos_urpf,
    _check_eos_vty_access_class,
    _extract_value,
    _has_line,
    _has_line_exact,
    _parse_eos_config,
    eos_compliance_report,
    eos_list_compliance_checks,
)


@pytest.fixture(autouse=True)
def _high_rate_limit():
    """Raise show rate limit for compliance tests."""
    orig_rate = device_rate_limiter._show_rate
    orig_burst = device_rate_limiter._show_burst
    device_rate_limiter._show_rate = 1000.0
    device_rate_limiter._show_burst = 1000.0
    yield
    device_rate_limiter._show_rate = orig_rate
    device_rate_limiter._show_burst = orig_burst


# ============================================================================
# Config parser tests
# ============================================================================


class TestParseEosConfig:
    """Test structured EOS config parser."""

    def test_simple_sections(self):
        config = """\
management ssh
   idle-timeout 15
   no shutdown
management api http-commands
   protocol https
   no protocol http"""
        parsed = _parse_eos_config(config)
        assert "management ssh" in parsed
        assert "management api http-commands" in parsed
        assert "idle-timeout 15" in parsed["management ssh"]
        assert "no shutdown" in parsed["management ssh"]
        assert "protocol https" in parsed["management api http-commands"]

    def test_global_section(self):
        config = """\
hostname spine-01
!
service password-encryption
!
management ssh
   no shutdown"""
        parsed = _parse_eos_config(config)
        assert "global" in parsed
        assert "management ssh" in parsed

    def test_empty_config(self):
        parsed = _parse_eos_config("")
        assert "global" in parsed
        assert parsed["global"] == []

    def test_comments_and_bangs_ignored(self):
        config = """\
! This is a comment
hostname spine-01
!
! Another comment
management ssh
   no shutdown"""
        parsed = _parse_eos_config(config)
        assert "hostname spine-01" in parsed
        assert "management ssh" in parsed

    def test_tab_indented_lines(self):
        config = "management ssh\n\tno shutdown\n\tidle-timeout 15"
        parsed = _parse_eos_config(config)
        assert "no shutdown" in parsed["management ssh"]
        assert "idle-timeout 15" in parsed["management ssh"]

    def test_multiple_sections_same_depth(self):
        config = """\
ntp server 10.10.10.1
ntp server 10.10.10.2
logging host 192.168.1.1"""
        parsed = _parse_eos_config(config)
        assert "ntp server 10.10.10.1" in parsed
        assert "ntp server 10.10.10.2" in parsed
        assert "logging host 192.168.1.1" in parsed


# ============================================================================
# Value extraction tests
# ============================================================================


class TestExtractValue:
    """Test config value extraction."""

    def test_extract_numeric(self):
        lines = ["idle-timeout 15", "no shutdown"]
        assert _extract_value(lines, "idle-timeout") == "15"

    def test_extract_string(self):
        lines = ["logging trap informational"]
        assert _extract_value(lines, "trap") == "informational"

    def test_missing_keyword(self):
        lines = ["no shutdown", "idle-timeout 15"]
        assert _extract_value(lines, "exec-timeout") is None

    def test_keyword_at_end_of_line(self):
        lines = ["ip dhcp snooping"]
        assert _extract_value(lines, "snooping") is None

    def test_extract_from_empty_lines(self):
        assert _extract_value([], "anything") is None

    def test_extract_multiple_matches_returns_first(self):
        lines = ["exec-timeout 10 0", "exec-timeout 5 0"]
        assert _extract_value(lines, "exec-timeout") == "10"


# ============================================================================
# Helper function tests
# ============================================================================


class TestHelperFunctions:
    """Test _has_line and _has_line_exact."""

    def test_has_line_present(self):
        assert _has_line(["ip dhcp snooping", "no shutdown"], "dhcp") is True

    def test_has_line_absent(self):
        assert _has_line(["no shutdown"], "dhcp") is False

    def test_has_line_empty(self):
        assert _has_line([], "anything") is False

    def test_has_line_exact_match(self):
        assert _has_line_exact(["service password-encryption", "no shutdown"], "service password-encryption") is True

    def test_has_line_exact_no_match(self):
        assert _has_line_exact(["service password-encryption", "no shutdown"], "service password") is False

    def test_has_line_exact_with_whitespace(self):
        assert _has_line_exact(["  service password-encryption  "], "service password-encryption") is True


# ============================================================================
# ComplianceResult dataclass tests
# ============================================================================


class TestComplianceResult:
    """Test ComplianceResult dataclass fields and defaults."""

    def test_all_fields(self):
        cr = ComplianceResult(
            check_id="CIS-EOS-1.1.1",
            check="SSH Timeout",
            status="pass",
            detail="Timeout 15s",
            severity="high",
            nist_controls=["AC-12", "SC-10"],
        )
        assert cr.check_id == "CIS-EOS-1.1.1"
        assert cr.check == "SSH Timeout"
        assert cr.status == "pass"
        assert cr.detail == "Timeout 15s"
        assert cr.severity == "high"
        assert cr.nist_controls == ["AC-12", "SC-10"]

    def test_defaults(self):
        cr = ComplianceResult(check_id="TEST", check="Test Check", status="fail")
        assert cr.detail == ""
        assert cr.severity == "medium"
        assert cr.nist_controls == []

    def test_status_values(self):
        for status in ("pass", "fail", "skip"):
            cr = ComplianceResult(check_id="T", check="T", status=status)
            assert cr.status == status


# ============================================================================
# ComplianceCheck dataclass tests
# ============================================================================


class TestComplianceCheck:
    """Test ComplianceCheck dataclass with nist_controls field."""

    def test_nist_controls_default(self):
        check = ComplianceCheck(
            id="TEST-1",
            title="Test",
            severity="HIGH",
            category="management",
            command="show test",
            encoding="text",
            check_fn=lambda x: True,
            remediation="Fix it",
        )
        assert check.nist_controls == []

    def test_nist_controls_populated(self):
        check = ComplianceCheck(
            id="TEST-1",
            title="Test",
            severity="HIGH",
            category="management",
            command="show test",
            encoding="text",
            check_fn=lambda x: True,
            remediation="Fix it",
            nist_controls=["AC-2", "IA-5"],
        )
        assert check.nist_controls == ["AC-2", "IA-5"]


# ============================================================================
# SSH timeout value-based check tests
# ============================================================================


class TestSshTimeoutValueCheck:
    """Test value-based SSH timeout checking."""

    def test_ssh_timeout_pass(self):
        config = "management ssh\n   idle-timeout 15\n   no shutdown"
        result = _check_eos_ssh_timeout_value(config)
        assert result.status == "pass"
        assert "15s" in result.detail
        assert result.nist_controls == ["AC-12", "SC-10"]

    def test_ssh_timeout_fail_too_high(self):
        config = "management ssh\n   idle-timeout 600\n   no shutdown"
        result = _check_eos_ssh_timeout_value(config)
        assert result.status == "fail"
        assert "600s" in result.detail
        assert "300s maximum" in result.detail

    def test_ssh_timeout_fail_not_configured(self):
        config = "management ssh\n   no shutdown"
        result = _check_eos_ssh_timeout_value(config)
        assert result.status == "fail"
        assert "Not configured" in result.detail

    def test_ssh_timeout_fail_invalid_value(self):
        config = "management ssh\n   idle-timeout abc\n   no shutdown"
        result = _check_eos_ssh_timeout_value(config)
        assert result.status == "fail"
        assert "Invalid value" in result.detail

    def test_ssh_timeout_exactly_300(self):
        config = "management ssh\n   idle-timeout 300\n   no shutdown"
        result = _check_eos_ssh_timeout_value(config)
        assert result.status == "pass"

    def test_ssh_timeout_bool_wrapper_pass(self):
        config = "management ssh\n   idle-timeout 15\n   no shutdown"
        assert _check_eos_ssh_timeout_value_bool(config) is True

    def test_ssh_timeout_bool_wrapper_fail(self):
        config = "management ssh\n   idle-timeout 600\n   no shutdown"
        assert _check_eos_ssh_timeout_value_bool(config) is False


# ============================================================================
# New EOS check function tests
# ============================================================================


class TestNewEosCheckFunctions:
    """Test new EOS check functions added for expanded compliance."""

    def test_motd_banner_pass(self):
        assert _check_eos_motd_banner("banner motd\nAuthorized access only.\nEOF") is True

    def test_motd_banner_fail(self):
        assert _check_eos_motd_banner("banner login\nSome text\nEOF") is False

    def test_password_min_length_pass(self):
        assert _check_eos_password_min_length("aaa password policy\n   minimum-length 10") is True

    def test_password_min_length_too_short(self):
        assert _check_eos_password_min_length("aaa password policy\n   minimum-length 4") is False

    def test_password_min_length_not_configured(self):
        assert _check_eos_password_min_length("aaa authentication login default local") is False

    def test_ssh_version_2_pass(self):
        assert _check_eos_ssh_version_2("management ssh\n   no shutdown") is True

    def test_ssh_version_2_explicit(self):
        assert _check_eos_ssh_version_2("management ssh\n   protocol-version 2") is True

    def test_vty_access_class_pass(self):
        assert _check_eos_vty_access_class("management ssh\n   access-class SSH_ACL") is True

    def test_vty_access_class_fail(self):
        assert _check_eos_vty_access_class("management ssh\n   no shutdown") is False

    def test_ntp_authentication_pass(self):
        assert _check_eos_ntp_authentication("ntp authenticate\nntp server 10.10.10.1") is True

    def test_ntp_authentication_fail(self):
        assert _check_eos_ntp_authentication("ntp server 10.10.10.1") is False

    def test_ntp_trusted_key_pass(self):
        assert _check_eos_ntp_trusted_key("ntp trusted-key 1") is True

    def test_ntp_trusted_key_fail(self):
        assert _check_eos_ntp_trusted_key("ntp server 10.10.10.1") is False

    def test_aaa_accounting_pass(self):
        assert _check_eos_aaa_accounting("aaa accounting commands all default start-stop group tacacs+") is True

    def test_aaa_accounting_fail(self):
        assert _check_eos_aaa_accounting("aaa authentication login default local") is False

    def test_aaa_authorization_pass(self):
        assert _check_eos_aaa_authorization("aaa authorization exec default group tacacs+ local") is True

    def test_aaa_authorization_fail(self):
        assert _check_eos_aaa_authorization("aaa authentication login default local") is False

    def test_snmp_v3_pass(self):
        assert _check_eos_snmp_v3("snmp-server group MYGROUP v3 priv") is True

    def test_snmp_v3_fail(self):
        assert _check_eos_snmp_v3("snmp-server community public ro") is False

    def test_snmp_acl_pass(self):
        assert _check_eos_snmp_acl("snmp-server community mycomm ro access SNMP_ACL") is True

    def test_snmp_acl_no_community(self):
        # No SNMP community at all is fine
        assert _check_eos_snmp_acl("") is True

    def test_snmp_acl_fail(self):
        assert _check_eos_snmp_acl("snmp-server community mycomm ro") is False

    def test_console_timeout_value_pass(self):
        assert _check_eos_console_timeout_value("line con 0\n   exec-timeout 10 0") is True

    def test_console_timeout_value_fail_too_high(self):
        assert _check_eos_console_timeout_value("line con 0\n   exec-timeout 30 0") is False

    def test_console_timeout_value_fail_zero(self):
        assert _check_eos_console_timeout_value("line con 0\n   exec-timeout 0 0") is False

    def test_console_timeout_value_not_configured(self):
        assert _check_eos_console_timeout_value("line con 0") is False

    def test_logging_level_default_pass(self):
        # No explicit logging trap defaults to informational (compliant)
        assert _check_eos_logging_level("logging host 192.168.1.1") is True

    def test_logging_level_explicit_pass(self):
        assert _check_eos_logging_level("logging trap informational") is True

    def test_logging_buffered_pass(self):
        assert _check_eos_logging_buffered("logging buffered 100000") is True

    def test_logging_buffered_fail(self):
        assert _check_eos_logging_buffered("logging host 192.168.1.1") is False

    def test_copp_rate_limit_pass(self):
        assert _check_eos_copp_rate_limit("system control-plane\n   police rate 100") is True

    def test_copp_rate_limit_fail(self):
        assert _check_eos_copp_rate_limit("system control-plane") is False

    def test_storm_control_pass(self):
        assert _check_eos_storm_control("interface Ethernet1\n   storm-control broadcast level 10") is True

    def test_storm_control_fail(self):
        assert _check_eos_storm_control("interface Ethernet1\n   no shutdown") is False

    def test_stp_mode_mstp(self):
        assert _check_eos_stp_mode("spanning-tree mode mstp") is True

    def test_stp_mode_rapid_pvst(self):
        assert _check_eos_stp_mode("spanning-tree mode rapid-pvst") is True

    def test_stp_mode_fail(self):
        assert _check_eos_stp_mode("spanning-tree mode pvst") is False

    def test_arp_inspection_pass(self):
        assert _check_eos_arp_inspection("ip arp inspection vlan 100") is True

    def test_arp_inspection_fail(self):
        assert _check_eos_arp_inspection("ip dhcp snooping") is False

    def test_ip_source_guard_pass(self):
        assert _check_eos_ip_source_guard("interface Ethernet1\n   ip verify source") is True

    def test_ip_source_guard_fail(self):
        assert _check_eos_ip_source_guard("interface Ethernet1\n   no shutdown") is False

    def test_urpf_pass(self):
        assert _check_eos_urpf("interface Ethernet1\n   ip verify unicast source reachable-via rx") is True

    def test_urpf_fail(self):
        assert _check_eos_urpf("interface Ethernet1\n   no shutdown") is False

    def test_lldp_restricted_no_lldp(self):
        assert _check_eos_lldp_restricted("no lldp transmit") is True

    def test_lldp_restricted_no_run(self):
        # If lldp run is not present, it's considered restricted
        assert _check_eos_lldp_restricted("") is True

    def test_mgmt_vrf_pass(self):
        assert _check_eos_mgmt_vrf("vrf instance MGMT\ninterface Management1\n   vrf MGMT") is True

    def test_mgmt_vrf_fail(self):
        assert _check_eos_mgmt_vrf("interface Management1") is False

    def test_radius_source_interface_pass(self):
        assert _check_eos_radius_source_interface("ip radius source-interface Management1") is True

    def test_radius_source_interface_fail(self):
        assert _check_eos_radius_source_interface("radius-server host 10.10.10.1") is False

    def test_tacacs_source_interface_pass(self):
        assert _check_eos_tacacs_source_interface("ip tacacs source-interface Management1") is True

    def test_tacacs_source_interface_fail(self):
        assert _check_eos_tacacs_source_interface("tacacs-server host 10.10.10.1") is False

    def test_errdisable_recovery_pass(self):
        assert _check_eos_errdisable_recovery("errdisable recovery cause bpduguard") is True

    def test_errdisable_recovery_fail(self):
        assert _check_eos_errdisable_recovery("spanning-tree bpduguard enable") is False


# ============================================================================
# NIST mapping presence tests
# ============================================================================


class TestNistMappingPresence:
    """Verify every check across all frameworks has at least one NIST control."""

    def test_all_eos_checks_have_nist(self):
        for check in CIS_EOS_CHECKS:
            assert len(check.nist_controls) >= 1, f"{check.id} has no NIST controls"

    def test_all_iosxe_checks_have_nist(self):
        for check in CIS_IOSXE_CHECKS:
            assert len(check.nist_controls) >= 1, f"{check.id} has no NIST controls"

    def test_all_nxos_checks_have_nist(self):
        for check in CIS_NXOS_CHECKS:
            assert len(check.nist_controls) >= 1, f"{check.id} has no NIST controls"

    def test_nist_controls_are_strings(self):
        for fw_checks in _FRAMEWORK_CHECKS.values():
            for check in fw_checks:
                for ctrl in check.nist_controls:
                    assert isinstance(ctrl, str), f"{check.id} has non-string NIST control: {ctrl}"

    def test_nist_controls_format(self):
        """NIST controls should follow XX-N pattern (e.g., AC-2, SC-7)."""
        pattern = re.compile(r"^[A-Z]{2}-\d+$")
        for fw_checks in _FRAMEWORK_CHECKS.values():
            for check in fw_checks:
                for ctrl in check.nist_controls:
                    assert pattern.match(ctrl), f"{check.id} has invalid NIST control format: {ctrl}"

    def test_expected_nist_families_present(self):
        """Ensure key NIST families are referenced across all checks."""
        all_controls: set[str] = set()
        for fw_checks in _FRAMEWORK_CHECKS.values():
            for check in fw_checks:
                all_controls.update(check.nist_controls)

        families = {ctrl.split("-")[0] for ctrl in all_controls}
        assert "AC" in families  # Access Control
        assert "AU" in families  # Audit and Accountability
        assert "IA" in families  # Identification and Authentication
        assert "SC" in families  # System and Communications Protection


# ============================================================================
# Check count tests (50+ per platform)
# ============================================================================


class TestCheckCounts:
    """Verify that each platform has the required number of checks."""

    def test_eos_has_at_least_40_checks(self):
        assert len(CIS_EOS_CHECKS) >= 40, f"EOS has {len(CIS_EOS_CHECKS)} checks, need >= 40"

    def test_iosxe_has_at_least_30_checks(self):
        assert len(CIS_IOSXE_CHECKS) >= 30, f"IOSXE has {len(CIS_IOSXE_CHECKS)} checks, need >= 30"

    def test_nxos_has_at_least_30_checks(self):
        assert len(CIS_NXOS_CHECKS) >= 30, f"NXOS has {len(CIS_NXOS_CHECKS)} checks, need >= 30"

    def test_eos_check_ids_unique(self):
        ids = [c.id for c in CIS_EOS_CHECKS]
        assert len(ids) == len(set(ids)), f"Duplicate EOS check IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_iosxe_check_ids_unique(self):
        ids = [c.id for c in CIS_IOSXE_CHECKS]
        assert len(ids) == len(set(ids)), f"Duplicate IOSXE check IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_nxos_check_ids_unique(self):
        ids = [c.id for c in CIS_NXOS_CHECKS]
        assert len(ids) == len(set(ids)), f"Duplicate NXOS check IDs: {[x for x in ids if ids.count(x) > 1]}"


# ============================================================================
# eos_list_compliance_checks with NIST data tests
# ============================================================================


class TestListComplianceChecksNist:
    """Test that list tool includes NIST control data."""

    def test_list_eos_includes_nist(self):
        result = eos_list_compliance_checks(framework="cis-eos")
        assert result["status"] == "success"
        for check in result["data"]["checks"]:
            assert "nist_controls" in check
            assert isinstance(check["nist_controls"], list)
            assert len(check["nist_controls"]) >= 1

    def test_list_all_frameworks_includes_nist(self):
        result = eos_list_compliance_checks(framework=None)
        assert result["status"] == "success"
        for fw_data in result["data"]["frameworks"].values():
            for check in fw_data["checks"]:
                assert "nist_controls" in check


# ============================================================================
# Compliance report tool tests
# ============================================================================


def _make_text_response(text: str) -> list:
    """Build a mock run_commands return value for text encoding."""
    return [{"output": text}]


class TestComplianceReport:
    """Test eos_compliance_report tool."""

    def test_report_json_format(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_report("spine-01", framework="cis-eos", report_format="json")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

        data = result["data"]
        assert data["format"] == "json"
        assert data["framework"] == "cis-eos"
        assert "total_checks" in data
        assert "passed" in data
        assert "failed" in data
        assert "pass_rate" in data
        assert "severity_breakdown" in data
        assert "severity_failed" in data
        assert "nist_coverage" in data
        assert "results" in data

        # Severity breakdown has all levels
        for level in ("critical", "high", "medium", "low"):
            assert level in data["severity_breakdown"]
            assert level in data["severity_failed"]

    def test_report_summary_format(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_report("spine-01", framework="cis-eos", report_format="summary")
        assert result["status"] == "success"

        data = result["data"]
        assert data["format"] == "summary"
        assert "report" in data
        report_text = data["report"]
        assert "Compliance Report" in report_text
        assert "spine-01" in report_text
        assert "Failed Checks:" in report_text
        assert "Severity Breakdown" in report_text

    def test_report_invalid_framework(self):
        result = eos_compliance_report("spine-01", framework="nist")
        assert result["status"] == "error"
        assert "Unsupported framework" in result["error"]

    def test_report_invalid_format(self):
        result = eos_compliance_report("spine-01", framework="cis-eos", report_format="csv")
        assert result["status"] == "error"
        assert "Invalid format" in result["error"]

    def test_report_nist_coverage(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_report("spine-01", framework="cis-eos", report_format="json")
        assert result["status"] == "success"

        nist_coverage = result["data"]["nist_coverage"]
        assert len(nist_coverage) > 0
        for _ctrl, stats in nist_coverage.items():
            assert "total" in stats
            assert "passed" in stats
            assert "failed" in stats
            assert stats["total"] == stats["passed"] + stats["failed"]

    def test_report_results_have_nist_controls(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"output": ""}]

        result = eos_compliance_report("spine-01", framework="cis-eos", report_format="json")
        for r in result["data"]["results"]:
            assert "nist_controls" in r
            assert isinstance(r["nist_controls"], list)
            assert "check_id" in r
            assert "status" in r
            assert r["status"] in ("pass", "fail", "skip")

    def test_report_with_compliant_device(self, mock_conn_mgr, mock_node):
        """Test report where some checks pass."""
        responses = {
            "show running-config section management ssh": _make_text_response(
                "management ssh\n   idle-timeout 15\n   no shutdown\n   access-class SSH_ACL"
            ),
            "show running-config section ntp": _make_text_response(
                "ntp server 10.10.10.1\nntp authenticate\nntp authentication-key 1 md5 KEY\nntp trusted-key 1"
            ),
        }

        def side_effect(commands, encoding="json"):  # noqa: ARG001
            cmd = commands[0]
            if cmd in responses:
                return responses[cmd]
            return [{"output": ""}]

        mock_node.run_commands.side_effect = side_effect

        result = eos_compliance_report("spine-01", framework="cis-eos", report_format="json")
        assert result["status"] == "success"
        assert result["data"]["passed"] > 0

    def test_report_connection_error(self, mock_conn_mgr, mock_node):
        """Device connection error is propagated."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "Connection refused")

        result = eos_compliance_report("spine-01", framework="cis-eos")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"


# ============================================================================
# Check registry structure validation
# ============================================================================


class TestCheckRegistryExpanded:
    """Validate expanded check registries."""

    def test_all_eos_checks_valid(self):
        for check in CIS_EOS_CHECKS:
            assert check.id.startswith("CIS-EOS-")
            assert check.severity in ("HIGH", "MEDIUM", "LOW")
            assert check.category in ("management", "control", "data")
            assert callable(check.check_fn)
            assert len(check.remediation) > 0
            assert len(check.nist_controls) >= 1

    def test_all_iosxe_checks_valid(self):
        for check in CIS_IOSXE_CHECKS:
            assert check.id.startswith("CIS-IOSXE-")
            assert check.severity in ("HIGH", "MEDIUM", "LOW")
            assert check.category in ("management", "control", "data")
            assert callable(check.check_fn)
            assert len(check.remediation) > 0
            assert len(check.nist_controls) >= 1

    def test_all_nxos_checks_valid(self):
        for check in CIS_NXOS_CHECKS:
            assert check.id.startswith("CIS-NXOS-")
            assert check.severity in ("HIGH", "MEDIUM", "LOW")
            assert check.category in ("management", "control", "data")
            assert callable(check.check_fn)
            assert len(check.remediation) > 0
            assert len(check.nist_controls) >= 1

    def test_eos_category_coverage(self):
        categories = {c.category for c in CIS_EOS_CHECKS}
        assert categories == {"management", "control", "data"}

    def test_iosxe_category_coverage(self):
        categories = {c.category for c in CIS_IOSXE_CHECKS}
        assert categories == {"management", "control", "data"}

    def test_nxos_category_coverage(self):
        categories = {c.category for c in CIS_NXOS_CHECKS}
        assert categories == {"management", "control", "data"}
