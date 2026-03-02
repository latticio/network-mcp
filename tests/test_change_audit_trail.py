"""Tests for change audit trail (approval_chain and execution_log)."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.change_management import ChangeManager, ChangeStatus
from network_mcp.server import conn_mgr
from tests.conftest import make_test_settings


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
    driver.run_show = MagicMock(return_value=["config output"])
    driver.vendor = "arista"
    driver.platform = "eos"
    return driver


@pytest.fixture
def mock_conn_mgr_for_changes(mock_driver):
    """Patch conn_mgr to use mock_driver."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield mock_driver

    with (
        patch.object(conn_mgr, "get_driver", return_value=mock_driver),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield mock_driver


class TestApprovalChain:
    """Tests for approval_chain tracking."""

    def test_approval_records_user_timestamp_reason_action(self, change_manager):
        """approval_chain records user, timestamp, reason, and action."""
        cr = change_manager.create_change_request(
            tool_name="net_create_vlan",
            device="spine-01",
            commands=["vlan 100", "name SERVERS"],
            user="engineer-1",
        )

        change_manager.approve_change(cr.id, approved_by="admin-1", reason="Approved for maintenance")

        assert len(cr.approval_chain) == 1
        entry = cr.approval_chain[0]
        assert entry["user"] == "admin-1"
        assert entry["reason"] == "Approved for maintenance"
        assert entry["action"] == "approved"
        assert "timestamp" in entry
        # Verify it's an ISO-format timestamp
        assert "T" in entry["timestamp"]

    def test_multiple_approvals_tracked_in_order(self, change_manager):
        """Multiple approvals are tracked in order in approval_chain."""
        cr = change_manager.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["shutdown"],
            user="engineer-1",
        )

        # First approval
        change_manager.approve_change(cr.id, approved_by="admin-1", reason="First review")

        # Reset status to PENDING for second approval (simulate four-eyes workflow)
        cr.status = ChangeStatus.PENDING

        # Second approval
        change_manager.approve_change(cr.id, approved_by="admin-2", reason="Second review")

        assert len(cr.approval_chain) == 2
        assert cr.approval_chain[0]["user"] == "admin-1"
        assert cr.approval_chain[0]["reason"] == "First review"
        assert cr.approval_chain[1]["user"] == "admin-2"
        assert cr.approval_chain[1]["reason"] == "Second review"

    def test_approval_chain_empty_for_auto_approved(self, change_manager):
        """Auto-approved LOW-risk changes without require_approval have empty chain
        (since approve_change is not called for auto-approval)."""
        settings = make_test_settings(
            net_change_mgmt_enabled=True,
            net_change_require_approval=False,
        )
        cm = ChangeManager(settings)
        cr = cm.create_change_request(
            tool_name="net_rename_vlan",
            device="spine-01",
            commands=["vlan 100", "name NEW-NAME"],
            user="engineer-1",
        )

        # LOW risk + require_approval=False -> auto-approved
        assert cr.status == ChangeStatus.APPROVED
        assert cr.approved_by == "auto"
        # approval_chain is empty since approve_change() wasn't called
        assert cr.approval_chain == []

    def test_approval_with_empty_reason(self, change_manager):
        """Approval with empty reason still records entry."""
        cr = change_manager.create_change_request(
            tool_name="net_create_vlan",
            device="spine-01",
            commands=["vlan 200", "name TEST"],
        )

        change_manager.approve_change(cr.id, approved_by="admin-1", reason="")

        assert len(cr.approval_chain) == 1
        assert cr.approval_chain[0]["reason"] == ""


class TestExecutionLog:
    """Tests for execution_log tracking."""

    def test_execution_log_records_executing_event(self, change_manager):
        """execution_log records when change starts executing."""
        cr = change_manager.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["vlan 100", "name SERVERS"],
        )

        change_manager.mark_executing(cr)

        assert len(cr.execution_log) == 1
        entry = cr.execution_log[0]
        assert entry["action"] == "executing"
        assert entry["result"] == "in_progress"
        assert "timestamp" in entry
        assert "2 command(s)" in entry["details"]

    def test_execution_log_records_completed_event(self, change_manager):
        """execution_log records when change completes successfully."""
        cr = change_manager.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["vlan 100"],
        )

        change_manager.mark_executing(cr)
        change_manager.mark_completed(cr)

        assert len(cr.execution_log) == 2
        assert cr.execution_log[0]["action"] == "executing"
        assert cr.execution_log[1]["action"] == "completed"
        assert cr.execution_log[1]["result"] == "success"

    def test_execution_log_records_failed_event(self, change_manager):
        """execution_log records when change fails."""
        cr = change_manager.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["vlan 100"],
        )

        change_manager.mark_executing(cr)
        change_manager.mark_failed(cr, "Connection timeout")

        assert len(cr.execution_log) == 2
        assert cr.execution_log[1]["action"] == "failed"
        assert cr.execution_log[1]["result"] == "error"
        assert "Connection timeout" in cr.execution_log[1]["details"]

    def test_rollback_events_recorded_in_execution_log(self, change_manager, mock_conn_mgr_for_changes):
        """Rollback events are recorded in execution_log."""
        cr = change_manager.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["vlan 100", "name SERVERS"],
        )
        cr.rollback_commands = ["no vlan 100"]
        cr.status = ChangeStatus.COMPLETED

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "success"
        assert any(e["action"] == "rolled_back" for e in cr.execution_log)

    def test_rollback_failure_recorded_in_execution_log(self, change_manager, mock_conn_mgr_for_changes):
        """Failed rollback events are recorded in execution_log."""
        cr = change_manager.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["vlan 100"],
        )
        cr.rollback_commands = ["no vlan 100"]
        cr.status = ChangeStatus.COMPLETED
        mock_conn_mgr_for_changes.run_config.side_effect = Exception("Device unreachable")

        change_manager._pending[cr.id] = cr
        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "error"
        assert any(e["action"] == "rollback_failed" for e in cr.execution_log)

    def test_no_rollback_commands_recorded_in_execution_log(self, change_manager):
        """Attempting rollback with no commands is recorded in execution_log."""
        cr = change_manager.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["vlan 100"],
        )
        cr.rollback_commands = []
        cr.status = ChangeStatus.COMPLETED

        result = change_manager.rollback(cr, conn_mgr)

        assert result["status"] == "error"
        assert any(e["action"] == "rollback_failed" for e in cr.execution_log)


class TestToDictIncludesAuditFields:
    """Tests that to_dict() includes approval_chain and execution_log."""

    def test_to_dict_includes_approval_chain(self, change_manager):
        """to_dict() includes approval_chain in serialization."""
        cr = change_manager.create_change_request(
            tool_name="net_create_vlan",
            device="spine-01",
            commands=["vlan 100"],
        )
        change_manager.approve_change(cr.id, approved_by="admin-1", reason="OK")

        result = change_manager.to_dict(cr)
        assert "approval_chain" in result
        assert len(result["approval_chain"]) == 1
        assert result["approval_chain"][0]["user"] == "admin-1"

    def test_to_dict_includes_execution_log(self, change_manager):
        """to_dict() includes execution_log in serialization."""
        cr = change_manager.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["vlan 100"],
        )
        change_manager.mark_executing(cr)
        change_manager.mark_completed(cr)

        result = change_manager.to_dict(cr)
        assert "execution_log" in result
        assert len(result["execution_log"]) == 2


class TestNetGetChangeHistory:
    """Tests for net_get_change_history tool function."""

    def test_get_change_history_returns_full_audit_trail(self):
        """net_get_change_history returns full audit trail."""
        from network_mcp.tools.common.changes import net_get_change_history

        settings = make_test_settings(
            net_change_mgmt_enabled=True,
            net_change_require_approval=True,
        )
        cm = ChangeManager(settings)
        cr = cm.create_change_request(
            tool_name="net_push_config",
            device="spine-01",
            commands=["vlan 100"],
        )
        cm.approve_change(cr.id, approved_by="admin-1", reason="Maintenance window")
        cm.mark_executing(cr)
        cm.mark_completed(cr)

        with patch("network_mcp.tools.common.changes.get_change_manager", return_value=cm):
            result = net_get_change_history("spine-01", cr.id)

        assert result["status"] == "success"
        assert result["change_id"] == cr.id
        data = result["data"]
        assert len(data["approval_chain"]) == 1
        assert data["approval_chain"][0]["user"] == "admin-1"
        assert data["approval_chain"][0]["reason"] == "Maintenance window"
        assert len(data["execution_log"]) == 2
        assert data["execution_log"][0]["action"] == "executing"
        assert data["execution_log"][1]["action"] == "completed"

    def test_get_change_history_not_found(self):
        """net_get_change_history returns error when change ID not found."""
        from network_mcp.tools.common.changes import net_get_change_history

        settings = make_test_settings(net_change_mgmt_enabled=True)
        cm = ChangeManager(settings)

        with patch("network_mcp.tools.common.changes.get_change_manager", return_value=cm):
            result = net_get_change_history("spine-01", "nonexistent-id")

        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_get_change_history_disabled(self):
        """net_get_change_history returns error when change management is disabled."""
        from network_mcp.tools.common.changes import net_get_change_history

        settings = make_test_settings(net_change_mgmt_enabled=False)
        cm = ChangeManager(settings)

        with patch("network_mcp.tools.common.changes.get_change_manager", return_value=cm):
            result = net_get_change_history("spine-01", "test-id")

        assert result["status"] == "error"
        assert "not enabled" in result["error"]
