"""Tests for configuration drift detection and remediation."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.config import NetworkSettings
from network_mcp.drift import DriftDetector, DriftItem, DriftReport, DriftSeverity
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> NetworkSettings:
    defaults = {"net_username": "admin", "net_password": "", "net_transport": "https"}
    defaults.update(overrides)
    return NetworkSettings(**defaults)


def _make_detector(golden_dir: str = "") -> DriftDetector:
    return DriftDetector(_make_settings(net_golden_config_dir=golden_dir))


@pytest.fixture
def mock_driver():
    """Create an unconstrained mock NetworkDriver for drift detection tests.

    Unlike the spec-constrained mock_node from conftest.py, this mock supports
    all NetworkDriver methods including get_config, get_facts, etc.
    """
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    return driver


@pytest.fixture
def mock_driver_conn_mgr(mock_driver):
    """Patch conn_mgr to return the mock driver for both get_driver and acquire."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield mock_driver

    with (
        patch.object(conn_mgr, "get_driver", return_value=mock_driver),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield mock_driver


# ---------------------------------------------------------------------------
# Sample configs
# ---------------------------------------------------------------------------

GOLDEN_CONFIG = """\
hostname spine-01
!
interface Ethernet1
   description to-leaf-01
   no shutdown
   ip address 10.0.0.1/31
!
interface Ethernet2
   description to-leaf-02
   no shutdown
   ip address 10.0.0.3/31
!
router bgp 65000
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65001
   neighbor 10.0.0.4 remote-as 65002
!
ip access-list MGMT
   permit 10.0.0.0/8
   deny any
!
ntp server 10.100.0.1
logging host 10.100.0.2
"""

RUNNING_CONFIG_NO_DRIFT = GOLDEN_CONFIG

RUNNING_CONFIG_WITH_DRIFT = """\
hostname spine-01
!
interface Ethernet1
   description to-leaf-01-CHANGED
   no shutdown
   ip address 10.0.0.1/31
!
interface Ethernet2
   description to-leaf-02
   no shutdown
   ip address 10.0.0.3/31
!
router bgp 65000
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65001
   neighbor 10.0.0.4 remote-as 65099
!
ip access-list MGMT
   permit 10.0.0.0/8
   permit 192.168.0.0/16
   deny any
!
ntp server 10.100.0.1
logging host 10.100.0.3
snmp-server community public ro
"""

RUNNING_CONFIG_LINES_REMOVED = """\
hostname spine-01
!
interface Ethernet1
   description to-leaf-01
   no shutdown
   ip address 10.0.0.1/31
!
router bgp 65000
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65001
!
ntp server 10.100.0.1
"""

RUNNING_CONFIG_LINES_ADDED = """\
hostname spine-01
!
interface Ethernet1
   description to-leaf-01
   no shutdown
   ip address 10.0.0.1/31
!
interface Ethernet2
   description to-leaf-02
   no shutdown
   ip address 10.0.0.3/31
!
interface Ethernet3
   description new-link
   no shutdown
   ip address 10.0.0.5/31
!
router bgp 65000
   router-id 10.0.0.1
   neighbor 10.0.0.2 remote-as 65001
   neighbor 10.0.0.4 remote-as 65002
   neighbor 10.0.0.6 remote-as 65003
!
ip access-list MGMT
   permit 10.0.0.0/8
   deny any
!
ntp server 10.100.0.1
logging host 10.100.0.2
"""


# ---------------------------------------------------------------------------
# DriftSeverity enum tests
# ---------------------------------------------------------------------------


class TestDriftSeverity:
    def test_severity_values(self):
        assert DriftSeverity.INFO.value == "info"
        assert DriftSeverity.WARNING.value == "warning"
        assert DriftSeverity.CRITICAL.value == "critical"

    def test_severity_from_value(self):
        assert DriftSeverity("info") == DriftSeverity.INFO
        assert DriftSeverity("warning") == DriftSeverity.WARNING
        assert DriftSeverity("critical") == DriftSeverity.CRITICAL


# ---------------------------------------------------------------------------
# DriftItem tests
# ---------------------------------------------------------------------------


class TestDriftItem:
    def test_create_drift_item(self):
        item = DriftItem(
            section="router bgp 65000",
            expected="neighbor 10.0.0.2 remote-as 65001",
            actual="neighbor 10.0.0.2 remote-as 65099",
            severity=DriftSeverity.CRITICAL,
            description="BGP neighbor AS changed",
            change_type="modified",
        )
        assert item.section == "router bgp 65000"
        assert item.severity == DriftSeverity.CRITICAL
        assert item.change_type == "modified"

    def test_default_change_type(self):
        item = DriftItem(
            section="global",
            expected="",
            actual="",
            severity=DriftSeverity.INFO,
            description="test",
        )
        assert item.change_type == ""


# ---------------------------------------------------------------------------
# DriftReport tests
# ---------------------------------------------------------------------------


class TestDriftReport:
    def test_empty_report(self):
        report = DriftReport(device="spine-01", timestamp="2024-01-01T00:00:00Z", golden_source="file:test.conf")
        assert report.total_drifts == 0
        assert report.items == []

    def test_to_dict(self):
        report = DriftReport(
            device="spine-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="file:test.conf",
            total_drifts=1,
            critical_drifts=1,
            items=[
                DriftItem(
                    section="router bgp",
                    expected="neighbor 10.0.0.2 remote-as 65001",
                    actual="neighbor 10.0.0.2 remote-as 65099",
                    severity=DriftSeverity.CRITICAL,
                    description="BGP changed",
                    change_type="modified",
                )
            ],
        )
        d = report.to_dict()
        assert d["device"] == "spine-01"
        assert d["total_drifts"] == 1
        assert d["critical_drifts"] == 1
        assert len(d["items"]) == 1
        assert d["items"][0]["severity"] == "critical"
        assert d["items"][0]["change_type"] == "modified"

    def test_to_dict_multiple_items(self):
        report = DriftReport(
            device="leaf-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="file:test.conf",
            total_drifts=3,
            critical_drifts=1,
            warning_drifts=1,
            info_drifts=1,
            items=[
                DriftItem("bgp", "a", "b", DriftSeverity.CRITICAL, "crit", "modified"),
                DriftItem("intf", "c", "d", DriftSeverity.WARNING, "warn", "modified"),
                DriftItem("banner", "e", "f", DriftSeverity.INFO, "info", "modified"),
            ],
        )
        d = report.to_dict()
        assert len(d["items"]) == 3
        severities = [i["severity"] for i in d["items"]]
        assert severities == ["critical", "warning", "info"]


# ---------------------------------------------------------------------------
# DriftDetector.detect_drift tests
# ---------------------------------------------------------------------------


class TestDetectDrift:
    def test_no_drift(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", GOLDEN_CONFIG, GOLDEN_CONFIG)
        assert report.total_drifts == 0
        assert report.critical_drifts == 0
        assert report.items == []

    def test_detect_modified_lines(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", RUNNING_CONFIG_WITH_DRIFT, GOLDEN_CONFIG)
        assert report.total_drifts > 0
        assert report.device == "spine-01"

    def test_detect_added_lines(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", RUNNING_CONFIG_LINES_ADDED, GOLDEN_CONFIG)
        assert report.total_drifts > 0
        added = [i for i in report.items if i.change_type == "added"]
        assert len(added) > 0

    def test_detect_removed_lines(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", RUNNING_CONFIG_LINES_REMOVED, GOLDEN_CONFIG)
        assert report.total_drifts > 0
        removed = [i for i in report.items if i.change_type == "removed"]
        assert len(removed) > 0

    def test_severity_counts_sum(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", RUNNING_CONFIG_WITH_DRIFT, GOLDEN_CONFIG)
        assert report.total_drifts == report.critical_drifts + report.warning_drifts + report.info_drifts

    def test_empty_configs(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", "", "")
        assert report.total_drifts == 0

    def test_empty_running_vs_populated_golden(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", "", GOLDEN_CONFIG)
        assert report.total_drifts > 0
        # Everything in golden is "removed" from running perspective
        removed = [i for i in report.items if i.change_type == "removed"]
        assert len(removed) > 0

    def test_populated_running_vs_empty_golden(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", GOLDEN_CONFIG, "")
        assert report.total_drifts > 0
        added = [i for i in report.items if i.change_type == "added"]
        assert len(added) > 0

    def test_report_timestamp_populated(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", GOLDEN_CONFIG, GOLDEN_CONFIG)
        assert report.timestamp != ""

    def test_report_golden_source_set(self):
        detector = _make_detector("/configs")
        report = detector.detect_drift("spine-01", GOLDEN_CONFIG, GOLDEN_CONFIG)
        assert "spine-01" in report.golden_source


# ---------------------------------------------------------------------------
# Severity classification tests
# ---------------------------------------------------------------------------


class TestSeverityClassification:
    def test_acl_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "ip access-group MGMT in")
        assert sev == DriftSeverity.CRITICAL

    def test_bgp_neighbor_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("router bgp 65000", "neighbor 10.0.0.2 remote-as 65001")
        assert sev == DriftSeverity.CRITICAL

    def test_ospf_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("router ospf 1", "network 10.0.0.0/24 area 0")
        assert sev == DriftSeverity.CRITICAL

    def test_aaa_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "aaa authentication login default local")
        assert sev == DriftSeverity.CRITICAL

    def test_radius_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "radius-server host 10.0.0.100")
        assert sev == DriftSeverity.CRITICAL

    def test_crypto_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "crypto isakmp policy 10")
        assert sev == DriftSeverity.CRITICAL

    def test_access_list_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "access-list 100 permit ip any any")
        assert sev == DriftSeverity.CRITICAL

    def test_ip_route_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "ip route 0.0.0.0/0 10.0.0.1")
        assert sev == DriftSeverity.CRITICAL

    def test_route_map_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "route-map IMPORT permit 10")
        assert sev == DriftSeverity.CRITICAL

    def test_prefix_list_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "prefix-list PL-DEFAULT seq 10 permit 0.0.0.0/0")
        assert sev == DriftSeverity.CRITICAL

    def test_management_api_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("management api http-commands", "no shutdown")
        assert sev == DriftSeverity.CRITICAL

    def test_description_is_warning(self):
        detector = _make_detector()
        sev = detector._classify_severity("interface Ethernet1", "description link-to-spine")
        assert sev == DriftSeverity.WARNING

    def test_logging_is_warning(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "logging host 10.100.0.1")
        assert sev == DriftSeverity.WARNING

    def test_ntp_is_warning(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "ntp server 10.100.0.1")
        assert sev == DriftSeverity.WARNING

    def test_snmp_is_warning(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "snmp-server community public ro")
        assert sev == DriftSeverity.WARNING

    def test_banner_is_warning(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "banner motd Authorized users only")
        assert sev == DriftSeverity.WARNING

    def test_interface_section_is_warning(self):
        detector = _make_detector()
        sev = detector._classify_severity("interface Ethernet1", "mtu 9214")
        assert sev == DriftSeverity.WARNING

    def test_vlan_is_warning(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "vlan 100")
        assert sev == DriftSeverity.WARNING

    def test_spanning_tree_is_warning(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "spanning-tree mode mstp")
        assert sev == DriftSeverity.WARNING

    def test_unknown_is_info(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "some unknown config line")
        assert sev == DriftSeverity.INFO

    def test_alias_is_info(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "alias sxi show version")
        assert sev == DriftSeverity.INFO

    def test_section_context_matters(self):
        """Section name contributes to severity classification."""
        detector = _make_detector()
        sev = detector._classify_severity("router bgp 65000", "no shutdown")
        assert sev == DriftSeverity.CRITICAL

    def test_junos_policy_statement_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "policy-statement EXPORT-BGP")
        assert sev == DriftSeverity.CRITICAL

    def test_junos_firewall_filter_is_critical(self):
        detector = _make_detector()
        sev = detector._classify_severity("global", "firewall filter PROTECT-RE term ssh")
        assert sev == DriftSeverity.CRITICAL


# ---------------------------------------------------------------------------
# Golden config loading tests
# ---------------------------------------------------------------------------


class TestLoadGoldenConfig:
    def test_load_from_file(self, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        config_file = config_dir / "spine-01.conf"
        config_file.write_text(GOLDEN_CONFIG)

        detector = _make_detector(str(config_dir))
        result = detector.load_golden_config("spine-01", source="file")
        assert result == GOLDEN_CONFIG

    def test_load_file_not_found(self, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()

        detector = _make_detector(str(config_dir))
        result = detector.load_golden_config("nonexistent-device", source="file")
        assert result is None

    def test_load_no_dir_configured(self):
        detector = _make_detector("")
        result = detector.load_golden_config("spine-01", source="file")
        assert result is None

    def test_unsupported_source(self):
        detector = _make_detector("/configs")
        result = detector.load_golden_config("spine-01", source="git")
        assert result is None

    def test_load_from_netbox_not_configured(self):
        detector = _make_detector("")
        with patch("network_mcp.drift.DriftDetector._load_from_netbox", return_value=None):
            result = detector.load_golden_config("spine-01", source="netbox")
            assert result is None

    def test_load_from_netbox_success(self):
        detector = _make_detector("")
        mock_config = '{"interfaces": {"Ethernet1": {"description": "uplink"}}}'
        with patch.object(detector, "_load_from_netbox", return_value=mock_config):
            result = detector.load_golden_config("spine-01", source="netbox")
            assert result == mock_config


# ---------------------------------------------------------------------------
# Remediation command generation tests
# ---------------------------------------------------------------------------


class TestRemediationCommands:
    def test_no_drift_no_commands(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", GOLDEN_CONFIG, GOLDEN_CONFIG)
        commands = detector.generate_remediation_commands(report, DriftSeverity.CRITICAL)
        assert commands == []

    def test_remediation_for_modified_lines(self):
        detector = _make_detector()
        report = DriftReport(
            device="spine-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="test",
            total_drifts=1,
            critical_drifts=1,
            items=[
                DriftItem(
                    section="router bgp 65000",
                    expected="neighbor 10.0.0.4 remote-as 65002",
                    actual="neighbor 10.0.0.4 remote-as 65099",
                    severity=DriftSeverity.CRITICAL,
                    description="BGP changed",
                    change_type="modified",
                )
            ],
        )
        commands = detector.generate_remediation_commands(report, DriftSeverity.CRITICAL)
        assert "neighbor 10.0.0.4 remote-as 65002" in commands

    def test_remediation_for_removed_lines(self):
        detector = _make_detector()
        report = DriftReport(
            device="spine-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="test",
            total_drifts=1,
            critical_drifts=1,
            items=[
                DriftItem(
                    section="router bgp 65000",
                    expected="neighbor 10.0.0.4 remote-as 65002",
                    actual="",
                    severity=DriftSeverity.CRITICAL,
                    description="BGP removed",
                    change_type="removed",
                )
            ],
        )
        commands = detector.generate_remediation_commands(report, DriftSeverity.CRITICAL)
        assert "neighbor 10.0.0.4 remote-as 65002" in commands

    def test_remediation_for_added_lines(self):
        detector = _make_detector()
        report = DriftReport(
            device="spine-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="test",
            total_drifts=1,
            warning_drifts=1,
            items=[
                DriftItem(
                    section="global",
                    expected="",
                    actual="snmp-server community public ro",
                    severity=DriftSeverity.WARNING,
                    description="SNMP added",
                    change_type="added",
                )
            ],
        )
        commands = detector.generate_remediation_commands(report, DriftSeverity.WARNING)
        assert "no snmp-server community public ro" in commands

    def test_severity_filter_critical_only(self):
        detector = _make_detector()
        report = DriftReport(
            device="spine-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="test",
            total_drifts=2,
            critical_drifts=1,
            warning_drifts=1,
            items=[
                DriftItem("bgp", "neighbor 10.0.0.2 remote-as 65001", "", DriftSeverity.CRITICAL, "c", "removed"),
                DriftItem("global", "", "logging host 10.100.0.5", DriftSeverity.WARNING, "w", "added"),
            ],
        )
        commands = detector.generate_remediation_commands(report, DriftSeverity.CRITICAL)
        assert "neighbor 10.0.0.2 remote-as 65001" in commands
        assert not any("logging" in c for c in commands)

    def test_severity_filter_warning_includes_critical(self):
        detector = _make_detector()
        report = DriftReport(
            device="spine-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="test",
            total_drifts=2,
            critical_drifts=1,
            warning_drifts=1,
            items=[
                DriftItem("bgp", "neighbor 10.0.0.2 remote-as 65001", "", DriftSeverity.CRITICAL, "c", "removed"),
                DriftItem("global", "", "logging host 10.100.0.5", DriftSeverity.WARNING, "w", "added"),
            ],
        )
        commands = detector.generate_remediation_commands(report, DriftSeverity.WARNING)
        assert len(commands) == 2

    def test_severity_filter_info_includes_all(self):
        detector = _make_detector()
        report = DriftReport(
            device="spine-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="test",
            total_drifts=3,
            critical_drifts=1,
            warning_drifts=1,
            info_drifts=1,
            items=[
                DriftItem("bgp", "neighbor 10.0.0.2 remote-as 65001", "", DriftSeverity.CRITICAL, "c", "removed"),
                DriftItem("global", "", "logging host 10.100.0.5", DriftSeverity.WARNING, "w", "added"),
                DriftItem("global", "", "alias sxi show ip interface brief", DriftSeverity.INFO, "i", "added"),
            ],
        )
        commands = detector.generate_remediation_commands(report, DriftSeverity.INFO)
        assert len(commands) == 3

    def test_skips_comment_lines(self):
        detector = _make_detector()
        report = DriftReport(
            device="spine-01",
            timestamp="2024-01-01T00:00:00Z",
            golden_source="test",
            total_drifts=1,
            info_drifts=1,
            items=[
                DriftItem("global", "! this is a comment", "", DriftSeverity.INFO, "comment", "removed"),
            ],
        )
        commands = detector.generate_remediation_commands(report, DriftSeverity.INFO)
        assert commands == []


# ---------------------------------------------------------------------------
# MCP Tool tests — net_check_drift
# ---------------------------------------------------------------------------


class TestNetCheckDrift:
    def test_check_drift_no_drift(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        (config_dir / "spine-01.conf").write_text(GOLDEN_CONFIG)

        mock_driver_conn_mgr.get_config.return_value = {"running": GOLDEN_CONFIG, "startup": GOLDEN_CONFIG}

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with patch.object(drift, "_detector", detector):
            result = drift.net_check_drift("spine-01", golden_source="file")
        assert result["status"] == "success"
        assert result["data"]["has_drift"] is False

    def test_check_drift_with_drift(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        (config_dir / "spine-01.conf").write_text(GOLDEN_CONFIG)

        mock_driver_conn_mgr.get_config.return_value = {
            "running": RUNNING_CONFIG_WITH_DRIFT,
            "startup": GOLDEN_CONFIG,
        }

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with patch.object(drift, "_detector", detector):
            result = drift.net_check_drift("spine-01", golden_source="file")
        assert result["status"] == "success"
        assert result["data"]["has_drift"] is True
        assert result["data"]["report"]["total_drifts"] > 0

    def test_check_drift_invalid_host(self):
        from network_mcp.tools.common import drift

        result = drift.net_check_drift("", golden_source="file")
        assert result["status"] == "error"

    def test_check_drift_invalid_source(self):
        from network_mcp.tools.common import drift

        result = drift.net_check_drift("spine-01", golden_source="git")
        assert result["status"] == "error"
        assert "Invalid golden_source" in result["error"]

    def test_check_drift_golden_not_found(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        # No config file created

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with patch.object(drift, "_detector", detector):
            result = drift.net_check_drift("spine-01", golden_source="file")
        assert result["status"] == "error"
        assert "Golden config not found" in result["error"]

    def test_check_drift_device_not_supported(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        (config_dir / "spine-01.conf").write_text(GOLDEN_CONFIG)

        from network_mcp.drivers.base import NotSupportedError

        mock_driver_conn_mgr.vendor = "juniper"
        mock_driver_conn_mgr.platform = "junos"
        mock_driver_conn_mgr.get_config.side_effect = NotSupportedError("not supported")

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with patch.object(drift, "_detector", detector):
            result = drift.net_check_drift("spine-01", golden_source="file")
        assert result["status"] == "not_supported"


# ---------------------------------------------------------------------------
# MCP Tool tests — net_drift_report
# ---------------------------------------------------------------------------


class TestNetDriftReport:
    def test_report_with_devices(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        (config_dir / "spine-01.conf").write_text(GOLDEN_CONFIG)

        mock_driver_conn_mgr.get_config.return_value = {
            "running": RUNNING_CONFIG_WITH_DRIFT,
            "startup": GOLDEN_CONFIG,
        }

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with patch.object(drift, "_detector", detector):
            result = drift.net_drift_report(hosts=["spine-01"])
        assert result["status"] == "success"
        assert result["summary"]["succeeded"] == 1
        assert result["summary"]["devices_with_drift"] == 1

    def test_report_no_drift(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        (config_dir / "spine-01.conf").write_text(GOLDEN_CONFIG)

        mock_driver_conn_mgr.get_config.return_value = {"running": GOLDEN_CONFIG, "startup": GOLDEN_CONFIG}

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with patch.object(drift, "_detector", detector):
            result = drift.net_drift_report(hosts=["spine-01"])
        assert result["status"] == "success"
        assert result["summary"]["devices_with_drift"] == 0

    def test_report_missing_golden(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        # No golden config file

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with patch.object(drift, "_detector", detector):
            result = drift.net_drift_report(hosts=["spine-01"])
        assert result["status"] == "error"
        assert result["summary"]["succeeded"] == 0
        assert result["summary"]["failed"] == 1

    def test_report_invalid_host_skipped(self):
        from network_mcp.tools.common import drift

        detector = _make_detector("/nonexistent")
        with patch.object(drift, "_detector", detector):
            result = drift.net_drift_report(hosts=[""])
        assert result["status"] == "error"
        assert result["summary"]["failed"] == 1

    def test_report_empty_inventory(self):
        from network_mcp.tools.common import drift

        detector = _make_detector("/nonexistent")
        with (
            patch.object(drift, "_detector", detector),
            patch.object(conn_mgr, "list_devices", return_value=[]),
        ):
            result = drift.net_drift_report(hosts=None)
        assert result["status"] == "error"
        assert "No devices" in result["error"]


# ---------------------------------------------------------------------------
# MCP Tool tests — net_remediate_drift
# ---------------------------------------------------------------------------


class TestNetRemediateDrift:
    def test_remediate_blocked_read_only(self, mock_driver_conn_mgr):
        from network_mcp.tools.common import drift

        with patch("network_mcp.tools.common.drift.check_read_only", return_value="Read-only mode"):
            result = drift.net_remediate_drift("spine-01")
        assert result["status"] == "error"
        assert "Read-only" in result["error"]

    def test_remediate_invalid_host(self):
        from network_mcp.tools.common import drift

        with patch("network_mcp.tools.common.drift.check_read_only", return_value=None):
            result = drift.net_remediate_drift("")
        assert result["status"] == "error"

    def test_remediate_invalid_source(self):
        from network_mcp.tools.common import drift

        with patch("network_mcp.tools.common.drift.check_read_only", return_value=None):
            result = drift.net_remediate_drift("spine-01", golden_source="git")
        assert result["status"] == "error"
        assert "Invalid golden_source" in result["error"]

    def test_remediate_invalid_severity(self):
        from network_mcp.tools.common import drift

        with patch("network_mcp.tools.common.drift.check_read_only", return_value=None):
            result = drift.net_remediate_drift("spine-01", severity="extreme")
        assert result["status"] == "error"
        assert "Invalid severity" in result["error"]

    def test_remediate_no_drift(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        (config_dir / "spine-01.conf").write_text(GOLDEN_CONFIG)

        mock_driver_conn_mgr.get_config.return_value = {"running": GOLDEN_CONFIG, "startup": GOLDEN_CONFIG}

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with (
            patch.object(drift, "_detector", detector),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
        ):
            result = drift.net_remediate_drift("spine-01")
        assert result["status"] == "success"
        assert result["data"]["commands_applied"] == 0

    def test_remediate_with_drift(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()
        (config_dir / "spine-01.conf").write_text(GOLDEN_CONFIG)

        mock_driver_conn_mgr.get_config.return_value = {
            "running": RUNNING_CONFIG_WITH_DRIFT,
            "startup": GOLDEN_CONFIG,
        }

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with (
            patch.object(drift, "_detector", detector),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
        ):
            result = drift.net_remediate_drift("spine-01", severity="info")
        assert result["status"] == "success"
        # net_remediate_drift now creates a change request instead of direct apply
        assert "change_id" in result
        assert len(result["commands"]) > 0
        assert result["source"] == "drift_remediation"

    def test_remediate_golden_not_found(self, mock_driver_conn_mgr, tmp_path):
        config_dir = tmp_path / "golden"
        config_dir.mkdir()

        from network_mcp.tools.common import drift

        detector = DriftDetector(_make_settings(net_golden_config_dir=str(config_dir)))
        with (
            patch.object(drift, "_detector", detector),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
        ):
            result = drift.net_remediate_drift("spine-01")
        assert result["status"] == "error"
        assert "Golden config not found" in result["error"]


# ---------------------------------------------------------------------------
# Vendor-specific syntax tests
# ---------------------------------------------------------------------------


class TestVendorSyntax:
    """Test drift detection handles vendor-specific config syntax."""

    def test_eos_config(self):
        golden = "interface Ethernet1\n   description uplink\n   ip address 10.0.0.1/31\n"
        running = "interface Ethernet1\n   description uplink-modified\n   ip address 10.0.0.1/31\n"
        detector = _make_detector()
        report = detector.detect_drift("eos-switch", running, golden)
        assert report.total_drifts > 0

    def test_iosxe_config(self):
        golden = "interface GigabitEthernet0/0\n description WAN\n ip address 10.0.0.1 255.255.255.254\n"
        running = "interface GigabitEthernet0/0\n description WAN-NEW\n ip address 10.0.0.1 255.255.255.254\n"
        detector = _make_detector()
        report = detector.detect_drift("iosxe-router", running, golden)
        assert report.total_drifts > 0

    def test_nxos_config(self):
        golden = "interface Ethernet1/1\n  description SPINE\n  ip address 10.0.0.1/31\n"
        running = "interface Ethernet1/1\n  description SPINE-CHANGED\n  ip address 10.0.0.1/31\n"
        detector = _make_detector()
        report = detector.detect_drift("nxos-switch", running, golden)
        assert report.total_drifts > 0

    def test_junos_config(self):
        golden = "interfaces {\n    ge-0/0/0 {\n        description uplink;\n    }\n}\n"
        running = "interfaces {\n    ge-0/0/0 {\n        description modified;\n    }\n}\n"
        detector = _make_detector()
        report = detector.detect_drift("junos-router", running, golden)
        assert report.total_drifts > 0

    def test_identical_configs_across_vendors(self):
        """Same config should produce no drift regardless of vendor syntax."""
        configs = [
            "hostname test\ninterface Ethernet1\n   ip address 10.0.0.1/31\n",
            "hostname test\n interface GigabitEthernet0/0\n  ip address 10.0.0.1 255.255.255.254\n",
        ]
        detector = _make_detector()
        for config in configs:
            report = detector.detect_drift("device", config, config)
            assert report.total_drifts == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_whitespace_only_diff(self):
        golden = "hostname test\n\n\ninterface Ethernet1\n"
        running = "hostname test\ninterface Ethernet1\n"
        detector = _make_detector()
        report = detector.detect_drift("device", running, golden)
        # Whitespace differences may or may not be flagged, but shouldn't crash
        assert isinstance(report.total_drifts, int)

    def test_very_long_config(self):
        """Drift detection handles large configs without errors."""
        golden_lines = [f"interface Ethernet{i}\n   description port-{i}\n" for i in range(500)]
        golden = "hostname big-switch\n" + "".join(golden_lines)
        running_lines = list(golden_lines)
        running_lines[250] = "interface Ethernet250\n   description CHANGED\n"
        running = "hostname big-switch\n" + "".join(running_lines)

        detector = _make_detector()
        report = detector.detect_drift("big-switch", running, golden)
        assert report.total_drifts > 0

    def test_config_with_special_characters(self):
        """Config with regex-special characters doesn't crash classification."""
        golden = 'banner motd "Welcome to $(hostname)"\n'
        running = 'banner motd "Welcome to the network"\n'
        detector = _make_detector()
        report = detector.detect_drift("device", running, golden)
        assert isinstance(report.total_drifts, int)

    def test_report_serialization_roundtrip(self):
        detector = _make_detector()
        report = detector.detect_drift("spine-01", RUNNING_CONFIG_WITH_DRIFT, GOLDEN_CONFIG)
        d = report.to_dict()
        # Ensure it's JSON-serializable
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["device"] == "spine-01"
        assert isinstance(parsed["items"], list)
