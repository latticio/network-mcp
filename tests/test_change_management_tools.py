"""Tests for vendor-agnostic change management tools (net_approve_change, net_rollback_change,
net_list_pending_changes).

Covers:
- net_approve_change: valid, not found, already applied, read-only blocked
- net_rollback_change: valid, never applied, read-only blocked
- net_list_pending_changes: with changes, empty, filter by host
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from network_mcp.change_management import ChangeManager, ChangeStatus
from network_mcp.server import conn_mgr
from network_mcp.tools.common.changes import (
    net_approve_change,
    net_get_change_request,
    net_list_pending_changes,
    net_rollback_change,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_cm(enabled=True, require_approval=True):
    """Create a ChangeManager with test settings."""
    settings = MagicMock()
    settings.net_change_mgmt_enabled = enabled
    settings.net_change_require_approval = require_approval
    settings.net_auto_rollback_timeout = 0
    settings.net_maintenance_windows = "*"
    return ChangeManager(settings)


def _make_driver_mock():
    """Create a minimal driver mock for rollback execution."""
    driver = MagicMock()
    driver.platform = "eos"
    driver.vendor = "Arista"
    driver.run_config = MagicMock(return_value=[])
    driver.run_show = MagicMock(return_value=[{"output": "hostname test-switch"}])
    return driver


@contextmanager
def _patch_cm(cm):
    """Patch the change manager used by tool functions."""
    with patch("network_mcp.tools.common.changes.get_change_manager", return_value=cm):
        yield


@contextmanager
def _patch_conn_mgr_for_rollback(driver):
    """Patch conn_mgr for rollback execution."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield driver

    with (
        patch.object(conn_mgr, "get_driver", return_value=driver),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield


# ===========================================================================
# net_approve_change tests
# ===========================================================================


class TestNetApproveChange:
    """Tests for the net_approve_change tool."""

    def test_approve_valid_pending_change(self):
        """Successfully approve a pending change request."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
            user="operator",
        )
        assert cr.status == ChangeStatus.PENDING

        with _patch_cm(cm):
            result = net_approve_change("switch-01", cr.id, approved_by="admin")

        assert result["status"] == "success"
        assert result["action"] == "approved"
        assert result["approved_by"] == "admin"
        assert result["change_id"] == cr.id
        assert cr.status == ChangeStatus.APPROVED

    def test_approve_not_found_returns_error(self):
        """Approving a non-existent change ID should return error."""
        cm = _make_cm()

        with _patch_cm(cm):
            result = net_approve_change("switch-01", "nonexistent-uuid")

        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_approve_already_approved_returns_error(self):
        """Approving a change that is already approved should return error."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )
        # Approve once
        cm.approve_change(cr.id, approved_by="admin")
        assert cr.status == ChangeStatus.APPROVED

        with _patch_cm(cm):
            # Try to approve again
            result = net_approve_change("switch-01", cr.id, approved_by="admin2")

        assert result["status"] == "error"
        assert "not found" in result["error"] or "not in pending" in result["error"]

    def test_approve_completed_change_returns_error(self):
        """Approving a completed change should return error."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )
        cm.approve_change(cr.id, approved_by="admin")
        cm.mark_executing(cr)
        cm.mark_completed(cr)

        with _patch_cm(cm):
            result = net_approve_change("switch-01", cr.id)

        assert result["status"] == "error"

    def test_approve_with_default_approver(self):
        """Approve with default 'admin' approver name."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )

        with _patch_cm(cm):
            result = net_approve_change("switch-01", cr.id)

        assert result["status"] == "success"
        assert result["approved_by"] == "admin"

    def test_approve_disabled_change_mgmt_returns_error(self):
        """When change management is disabled, approve should return error."""
        cm = _make_cm(enabled=False)

        with _patch_cm(cm):
            result = net_approve_change("switch-01", "any-id")

        assert result["status"] == "error"
        assert "not enabled" in result["error"]

    def test_approve_returns_risk_level(self):
        """Approval response should include the risk level."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="net_push_config",
            device="switch-01",
            commands=["hostname new-name"],
        )

        with _patch_cm(cm):
            result = net_approve_change("switch-01", cr.id, approved_by="admin")

        assert result["status"] == "success"
        assert "risk" in result


# ===========================================================================
# net_rollback_change tests
# ===========================================================================


class TestNetRollbackChange:
    """Tests for the net_rollback_change tool."""

    def test_rollback_valid_completed_change(self):
        """Successfully roll back a completed change."""
        cm = _make_cm(require_approval=False)
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )
        cm.mark_completed(cr)

        driver = _make_driver_mock()

        with _patch_cm(cm), _patch_conn_mgr_for_rollback(driver):
            result = net_rollback_change("switch-01", cr.id)

        assert result["status"] == "success"
        assert result["action"] == "rolled_back"
        assert "rollback_commands" in result
        assert cr.status == ChangeStatus.ROLLED_BACK

    def test_rollback_not_found_returns_error(self):
        """Rolling back a non-existent change should return error."""
        cm = _make_cm()

        with _patch_cm(cm):
            result = net_rollback_change("switch-01", "nonexistent-uuid")

        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_rollback_no_rollback_commands(self):
        """Rolling back a change with no rollback commands should return error."""
        cm = _make_cm(require_approval=False)
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )
        # Clear rollback commands
        cr.rollback_commands = []
        cm.mark_completed(cr)

        driver = _make_driver_mock()

        with _patch_cm(cm), _patch_conn_mgr_for_rollback(driver):
            result = net_rollback_change("switch-01", cr.id)

        assert result["status"] == "error"
        assert "no rollback" in result["error"].lower()

    def test_rollback_disabled_change_mgmt_returns_error(self):
        """When change management is disabled, rollback should return error."""
        cm = _make_cm(enabled=False)

        with _patch_cm(cm):
            result = net_rollback_change("switch-01", "any-id")

        assert result["status"] == "error"
        assert "not enabled" in result["error"]

    def test_rollback_with_driver_exception(self):
        """Rollback that fails due to driver exception should return error."""
        cm = _make_cm(require_approval=False)
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )
        cm.mark_completed(cr)

        driver = _make_driver_mock()
        driver.run_config.side_effect = ConnectionError("Device unreachable")

        with _patch_cm(cm), _patch_conn_mgr_for_rollback(driver):
            result = net_rollback_change("switch-01", cr.id)

        assert result["status"] == "error"
        assert "failed" in result["error"].lower() or "unreachable" in result["error"].lower()


# ===========================================================================
# net_list_pending_changes tests
# ===========================================================================


class TestNetListPendingChanges:
    """Tests for the net_list_pending_changes tool."""

    def test_list_with_pending_changes(self):
        """List should return all pending changes."""
        cm = _make_cm()
        cr1 = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )
        cr2 = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-02",
            commands=["vlan 200", "name USERS"],
        )

        with _patch_cm(cm):
            result = net_list_pending_changes()

        assert result["status"] == "success"
        assert result["count"] == 2
        ids = {c["id"] for c in result["data"]}
        assert cr1.id in ids
        assert cr2.id in ids

    def test_list_empty_when_no_changes(self):
        """List should return empty results when no changes exist."""
        cm = _make_cm()

        with _patch_cm(cm):
            result = net_list_pending_changes()

        assert result["status"] == "success"
        assert result["count"] == 0
        assert result["data"] == []

    def test_list_filter_by_host(self):
        """List should filter changes by host when specified."""
        cm = _make_cm()
        cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )
        cr2 = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-02",
            commands=["vlan 200", "name USERS"],
        )

        with _patch_cm(cm):
            result = net_list_pending_changes(host="switch-02")

        assert result["status"] == "success"
        assert result["count"] == 1
        assert result["data"][0]["id"] == cr2.id
        assert result["data"][0]["device"] == "switch-02"

    def test_list_filter_by_host_no_match(self):
        """Filter by host that has no changes should return empty."""
        cm = _make_cm()
        cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )

        with _patch_cm(cm):
            result = net_list_pending_changes(host="switch-99")

        assert result["status"] == "success"
        assert result["count"] == 0

    def test_list_excludes_completed_changes(self):
        """Completed changes should not appear in pending list."""
        cm = _make_cm(require_approval=False)
        cr = cm.create_change_request(
            tool_name="net_rename_vlan",
            device="switch-01",
            commands=["vlan 100", "name NEW-NAME"],
        )
        cm.mark_executing(cr)
        cm.mark_completed(cr)

        with _patch_cm(cm):
            result = net_list_pending_changes()

        assert result["status"] == "success"
        assert result["count"] == 0

    def test_list_disabled_change_mgmt_returns_error(self):
        """When change management is disabled, list should return error."""
        cm = _make_cm(enabled=False)

        with _patch_cm(cm):
            result = net_list_pending_changes()

        assert result["status"] == "error"
        assert "not enabled" in result["error"]

    def test_list_includes_approved_changes(self):
        """Approved but not yet executed changes should still appear in pending list."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )
        cm.approve_change(cr.id, approved_by="admin")

        with _patch_cm(cm):
            result = net_list_pending_changes()

        assert result["status"] == "success"
        assert result["count"] == 1
        assert result["data"][0]["status"] == "approved"


# ===========================================================================
# net_get_change_request tests
# ===========================================================================


class TestNetGetChangeRequest:
    """Tests for the net_get_change_request tool."""

    def test_get_existing_change_request(self):
        """Should return details for an existing change request."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )

        with _patch_cm(cm):
            result = net_get_change_request("switch-01", cr.id)

        assert result["status"] == "success"
        assert result["data"]["id"] == cr.id
        assert result["data"]["device"] == "switch-01"
        assert result["data"]["tool_name"] == "net_create_vlan"

    def test_get_nonexistent_change_request(self):
        """Should return error for a non-existent change request."""
        cm = _make_cm()

        with _patch_cm(cm):
            result = net_get_change_request("switch-01", "nonexistent-id")

        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_get_disabled_change_mgmt(self):
        """When change management is disabled, should return error."""
        cm = _make_cm(enabled=False)

        with _patch_cm(cm):
            result = net_get_change_request("switch-01", "any-id")

        assert result["status"] == "error"
        assert "not enabled" in result["error"]
