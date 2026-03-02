"""Tests for drift remediation routed through change management.

Validates that:
- net_remediate_drift creates a ChangeRequest instead of direct apply
- Created ChangeRequest contains correct commands and risk level
- Change ID is returned in the response
- net_remediate_drift_direct still applies directly (emergency use)
- Drift remediation ChangeRequest source is "drift_remediation"
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr

MOCK_RUNNING_CONFIG = """\
hostname spine-01
interface Ethernet1
   description link-to-leaf-01
   no shutdown
router bgp 65001
   router-id 10.0.0.1
end
"""

MOCK_GOLDEN_CONFIG = """\
hostname spine-01
interface Ethernet1
   description link-to-leaf-01-UPDATED
   no shutdown
interface Ethernet2
   description new-link
   no shutdown
router bgp 65001
   router-id 10.0.0.1
end
"""


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_config.return_value = {
        "running": MOCK_RUNNING_CONFIG,
        "startup": MOCK_RUNNING_CONFIG,
    }
    driver.run_config.return_value = []
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return mock driver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


@pytest.fixture
def mock_detector():
    """Create a mock DriftDetector that returns drift with remediation commands."""
    mock_report = MagicMock()
    mock_report.total_drifts = 3
    mock_report.critical_drifts = 1
    mock_report.warning_drifts = 1
    mock_report.info_drifts = 1
    mock_report.to_dict.return_value = {
        "total": 3,
        "critical": 1,
        "warning": 1,
        "info": 1,
        "sections": [],
    }

    detector = MagicMock()
    detector.load_golden_config.return_value = MOCK_GOLDEN_CONFIG
    detector.detect_drift.return_value = mock_report
    detector.generate_remediation_commands.return_value = [
        "interface Ethernet1",
        "description link-to-leaf-01-UPDATED",
        "interface Ethernet2",
        "description new-link",
        "no shutdown",
    ]
    return detector


@pytest.fixture
def mock_detector_no_drift():
    """Create a mock DriftDetector that returns no drift."""
    mock_report = MagicMock()
    mock_report.total_drifts = 0
    mock_report.to_dict.return_value = {"total": 0}

    detector = MagicMock()
    detector.load_golden_config.return_value = MOCK_GOLDEN_CONFIG
    detector.detect_drift.return_value = mock_report
    return detector


class TestNetRemediateDriftChangeManagement:
    """Tests that net_remediate_drift routes through change management."""

    def test_creates_change_request_when_cm_enabled(self, mock_common_conn_mgr, mock_driver, mock_detector):
        """net_remediate_drift should create a ChangeRequest instead of direct apply."""
        mock_cm = MagicMock()
        mock_cm.enabled = True
        mock_cr = MagicMock()
        mock_cr.id = "test-change-id-123"
        mock_cm.create_change_request.return_value = mock_cr

        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.get_change_manager", return_value=mock_cm),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift

            result = net_remediate_drift("spine-01", golden_source="file", severity="critical")

        assert result["status"] == "success"
        assert result["change_id"] == "test-change-id-123"
        assert result["message"] == "Drift remediation change request created. Approve to apply."
        # Should NOT have applied commands directly
        mock_driver.run_config.assert_not_called()

    def test_change_request_contains_correct_commands(self, mock_common_conn_mgr, mock_driver, mock_detector):
        mock_cm = MagicMock()
        mock_cm.enabled = True
        mock_cr = MagicMock()
        mock_cr.id = "cr-456"
        mock_cm.create_change_request.return_value = mock_cr

        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.get_change_manager", return_value=mock_cm),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift

            result = net_remediate_drift("spine-01")

        assert "commands" in result
        assert len(result["commands"]) == 5
        assert result["commands"][0] == "interface Ethernet1"

    def test_risk_level_in_response(self, mock_common_conn_mgr, mock_driver, mock_detector):
        mock_cm = MagicMock()
        mock_cm.enabled = True
        mock_cr = MagicMock()
        mock_cr.id = "cr-789"
        mock_cm.create_change_request.return_value = mock_cr

        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.get_change_manager", return_value=mock_cm),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift

            result = net_remediate_drift("spine-01")

        assert "risk_level" in result
        assert result["risk_level"] in ("LOW", "MEDIUM", "HIGH")

    def test_source_is_drift_remediation(self, mock_common_conn_mgr, mock_driver, mock_detector):
        mock_cm = MagicMock()
        mock_cm.enabled = True
        mock_cr = MagicMock()
        mock_cr.id = "cr-source"
        mock_cm.create_change_request.return_value = mock_cr

        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.get_change_manager", return_value=mock_cm),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift

            result = net_remediate_drift("spine-01")

        assert result["source"] == "drift_remediation"
        # Verify the tool name passed to create_change_request
        mock_cm.create_change_request.assert_called_once()
        call_args = mock_cm.create_change_request.call_args
        assert call_args[0][0] == "drift_remediation"

    def test_change_id_returned(self, mock_common_conn_mgr, mock_driver, mock_detector):
        mock_cm = MagicMock()
        mock_cm.enabled = True
        mock_cr = MagicMock()
        mock_cr.id = "unique-change-id-999"
        mock_cm.create_change_request.return_value = mock_cr

        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.get_change_manager", return_value=mock_cm),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift

            result = net_remediate_drift("spine-01")

        assert result["change_id"] == "unique-change-id-999"

    def test_no_drift_returns_success(self, mock_common_conn_mgr, mock_driver, mock_detector_no_drift):
        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector_no_drift),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift

            result = net_remediate_drift("spine-01")

        assert result["status"] == "success"
        assert "No drift detected" in result["message"]

    def test_fallback_when_cm_disabled(self, mock_common_conn_mgr, mock_driver, mock_detector):
        """When change management is disabled, still create a tracking entry."""
        mock_cm = MagicMock()
        mock_cm.enabled = False

        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.get_change_manager", return_value=mock_cm),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift

            result = net_remediate_drift("spine-01")

        assert result["status"] == "success"
        assert "change_id" in result
        assert result["source"] == "drift_remediation"
        # Should NOT have applied commands directly
        mock_driver.run_config.assert_not_called()


class TestNetRemediateDriftDirect:
    """Tests that net_remediate_drift_direct applies commands directly."""

    def test_direct_applies_commands(self, mock_common_conn_mgr, mock_driver, mock_detector):
        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift_direct

            result = net_remediate_drift_direct("spine-01")

        assert result["status"] == "success"
        assert result["action"] == "remediate_drift"
        mock_driver.run_config.assert_called_once()

    def test_direct_returns_commands_applied(self, mock_common_conn_mgr, mock_driver, mock_detector):
        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift_direct

            result = net_remediate_drift_direct("spine-01")

        assert result["data"]["commands_applied"] == 5
        assert len(result["data"]["commands"]) == 5

    def test_direct_handles_not_supported(self, mock_common_conn_mgr, mock_driver, mock_detector):
        from network_mcp.drivers.base import NotSupportedError

        mock_driver.run_config.side_effect = NotSupportedError("not supported")

        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch("network_mcp.tools.common.drift.check_read_only", return_value=None),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift_direct

            result = net_remediate_drift_direct("spine-01")

        assert result["status"] == "not_supported"

    def test_direct_respects_read_only(self, mock_common_conn_mgr, mock_driver, mock_detector):
        with (
            patch("network_mcp.tools.common.drift._get_detector", return_value=mock_detector),
            patch(
                "network_mcp.tools.common.drift.check_read_only",
                return_value="Write operations are disabled.",
            ),
            patch("network_mcp.tools.common.drift.config_sanitizer") as mock_sanitizer,
        ):
            mock_sanitizer.sanitize.side_effect = lambda x: x

            from network_mcp.tools.common.drift import net_remediate_drift_direct

            result = net_remediate_drift_direct("spine-01")

        assert result["status"] == "error"
        assert "disabled" in result["error"].lower() or "Write" in result["error"]


class TestDriftRiskCalculation:
    """Tests for drift risk level calculation."""

    def test_low_risk_few_drifts(self):
        from network_mcp.tools.common.drift import _calculate_drift_risk

        assert _calculate_drift_risk(1) == "LOW"
        assert _calculate_drift_risk(2) == "LOW"

    def test_medium_risk_moderate_drifts(self):
        from network_mcp.tools.common.drift import _calculate_drift_risk

        assert _calculate_drift_risk(3) == "MEDIUM"
        assert _calculate_drift_risk(9) == "MEDIUM"

    def test_high_risk_many_drifts(self):
        from network_mcp.tools.common.drift import _calculate_drift_risk

        assert _calculate_drift_risk(10) == "HIGH"
        assert _calculate_drift_risk(50) == "HIGH"
