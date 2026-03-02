"""Tests for post-rollback validation in change management."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.change_management import ChangeManager, ChangeRequest, ChangeRisk, ChangeStatus
from network_mcp.server import conn_mgr
from tests.conftest import make_test_settings


def _make_change_request(
    device="spine-01",
    commands=None,
    rollback_commands=None,
    pre_snapshot=None,
    status=ChangeStatus.COMPLETED,
):
    """Helper to create a ChangeRequest for testing."""
    cr = ChangeRequest(
        id="test-change-001",
        tool_name="net_push_config",
        device=device,
        commands=commands or ["vlan 100", "name SERVERS"],
        risk=ChangeRisk.MEDIUM,
        status=status,
    )
    cr.rollback_commands = rollback_commands if rollback_commands is not None else ["no vlan 100"]
    if pre_snapshot is not None:
        cr.pre_snapshot = pre_snapshot
    return cr


@pytest.fixture
def change_manager():
    """Create a ChangeManager with change management enabled."""
    settings = make_test_settings(
        net_change_mgmt_enabled=True,
        net_change_require_approval=True,
    )
    return ChangeManager(settings)


@pytest.fixture
def mock_driver():
    """Create a mock driver for connection manager."""
    driver = MagicMock()
    driver.run_config = MagicMock(return_value=[])
    driver.run_show = MagicMock(return_value=["vlan 100\n  name SERVERS\n"])
    driver.get_lldp_neighbors = MagicMock(return_value={})
    driver.vendor = "arista"
    driver.platform = "eos"
    return driver


@pytest.fixture
def mock_conn_mgr_for_rollback(mock_driver):
    """Patch conn_mgr to use mock_driver via acquire context manager."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield mock_driver

    with (
        patch.object(conn_mgr, "get_driver", return_value=mock_driver),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield mock_driver


class TestPostRollbackValidation:
    """Tests for post-rollback config validation."""

    def test_rollback_validation_success_when_state_matches(self, change_manager, mock_conn_mgr_for_rollback):
        """Post-rollback validation succeeds when state matches pre-change snapshot."""
        pre_config = "vlan 1\n  name default\n"
        cr = _make_change_request(
            pre_snapshot={"running_config": pre_config},
        )
        # Make post-rollback config match pre-change snapshot
        mock_conn_mgr_for_rollback.run_show.return_value = [pre_config]

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "success"
        assert result["action"] == "rolled_back"
        assert "Rollback validated successfully" in result.get("message", "")
        assert cr.status == ChangeStatus.ROLLED_BACK

    def test_rollback_validation_warning_when_state_differs(self, change_manager, mock_conn_mgr_for_rollback):
        """Post-rollback validation returns warning when state differs from pre-change."""
        pre_config = "vlan 1\n  name default\n"
        post_config = "vlan 1\n  name default\nvlan 200\n  name EXTRA\n"
        cr = _make_change_request(
            pre_snapshot={"running_config": pre_config},
        )
        mock_conn_mgr_for_rollback.run_show.return_value = [post_config]

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "warning"
        assert result["action"] == "rolled_back"
        assert "differs from pre-change snapshot" in result["message"]
        assert "validation" in result
        validation = result["validation"]
        assert "expected_sections" in validation
        assert "actual_sections" in validation
        assert "diff_summary" in validation
        assert cr.status == ChangeStatus.ROLLED_BACK

    def test_pre_change_snapshot_stored_in_change_request(self, change_manager, mock_conn_mgr_for_rollback):
        """Pre-change snapshot is stored correctly in ChangeRequest."""
        pre_config = "interface Ethernet1\n  description uplink\n"
        mock_conn_mgr_for_rollback.run_show.return_value = [pre_config]

        cr = ChangeRequest(
            id="test-snap-001",
            tool_name="net_push_config",
            device="spine-01",
            commands=["interface Ethernet1", "shutdown"],
            risk=ChangeRisk.HIGH,
        )
        change_manager.capture_pre_snapshot(cr, conn_mgr)

        assert "running_config" in cr.pre_snapshot
        assert cr.pre_snapshot["running_config"] == pre_config

    def test_rollback_without_pre_snapshot_still_works(self, change_manager, mock_conn_mgr_for_rollback):
        """Rollback with no pre-change snapshot still works (graceful degradation)."""
        cr = _make_change_request(
            pre_snapshot={},  # No pre-change snapshot
        )
        mock_conn_mgr_for_rollback.run_show.return_value = ["some config"]

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "success"
        assert result["action"] == "rolled_back"
        assert cr.status == ChangeStatus.ROLLED_BACK

    def test_rollback_with_snapshot_error_skips_validation(self, change_manager, mock_conn_mgr_for_rollback):
        """Rollback with snapshot error skips validation gracefully."""
        cr = _make_change_request(
            pre_snapshot={"error": "Failed to capture snapshot"},
        )

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "success"
        assert result["action"] == "rolled_back"

    def test_rollback_no_commands_returns_error(self, change_manager, mock_conn_mgr_for_rollback):
        """Rollback with no rollback commands returns error."""
        cr = _make_change_request(rollback_commands=[])

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "error"
        assert "No rollback commands available" in result["error"]

    def test_rollback_execution_failure_returns_error(self, change_manager, mock_conn_mgr_for_rollback):
        """Rollback execution failure returns error."""
        cr = _make_change_request()
        mock_conn_mgr_for_rollback.run_config.side_effect = Exception("Connection lost")

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "error"
        assert "Connection lost" in result["error"]
        assert cr.status == ChangeStatus.FAILED

    def test_rollback_post_snapshot_failure_skips_validation(self, change_manager, mock_conn_mgr_for_rollback):
        """If post-rollback snapshot fails, validation is skipped."""
        pre_config = "vlan 1\n  name default\n"
        cr = _make_change_request(
            pre_snapshot={"running_config": pre_config},
        )
        # run_config succeeds but run_show fails after rollback
        call_count = 0

        def run_show_side_effect(commands, encoding="json"):
            nonlocal call_count
            call_count += 1
            raise Exception("Device unreachable after rollback")

        mock_conn_mgr_for_rollback.run_show.side_effect = run_show_side_effect

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        # Should still succeed (rollback commands were applied), validation is skipped
        assert result["status"] == "success"
        assert result["action"] == "rolled_back"


class TestRollbackViaToolFunction:
    """Tests for net_rollback_change tool function."""

    def test_net_rollback_change_disabled(self):
        """net_rollback_change returns error when change management is disabled."""
        from network_mcp.tools.common.changes import net_rollback_change

        with patch("network_mcp.tools.common.changes.get_change_manager") as mock_gcm:
            mock_cm = MagicMock()
            mock_cm.enabled = False
            mock_gcm.return_value = mock_cm

            result = net_rollback_change("spine-01", "test-id")
            assert result["status"] == "error"
            assert "not enabled" in result["error"]

    def test_net_rollback_change_not_found(self):
        """net_rollback_change returns error when change ID not found."""
        from network_mcp.tools.common.changes import net_rollback_change

        with patch("network_mcp.tools.common.changes.get_change_manager") as mock_gcm:
            mock_cm = MagicMock()
            mock_cm.enabled = True
            mock_cm.get_change_request.return_value = None
            mock_gcm.return_value = mock_cm

            result = net_rollback_change("spine-01", "nonexistent")
            assert result["status"] == "error"
            assert "not found" in result["error"]
