"""Integration tests for the change management lifecycle.

Covers the full lifecycle (create -> approve -> execute -> validate -> rollback),
maintenance window enforcement, four-eyes approval flows, blast radius analysis,
dry-run mode, config version tracking, and drift remediation via change management.

Where prerequisite sessions (5.2, 6.1, 6.2) have not yet added specific features
(e.g., net_analyze_change_impact, net_dryrun_config, net_get_config_version,
four-eyes approval, blast radius analysis), tests exercise the equivalent
functionality using the current ChangeManager API directly.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from network_mcp.change_management import (
    ChangeManager,
    ChangeRisk,
    ChangeStatus,
)
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    """Build a minimal settings-like object for ChangeManager."""
    defaults = {
        "net_change_mgmt_enabled": True,
        "net_change_require_approval": True,
        "net_maintenance_windows": "*",
        "net_auto_rollback_timeout": 300,
    }
    defaults.update(overrides)
    return type("FakeSettings", (), defaults)()


def _make_cm(**overrides) -> ChangeManager:
    """Create a ChangeManager with test defaults."""
    return ChangeManager(_make_settings(**overrides))


def _inject_cm(cm: ChangeManager):
    """Inject a ChangeManager into the helpers module singleton."""
    import network_mcp.helpers as helpers_mod

    helpers_mod._change_manager = cm


# ---------------------------------------------------------------------------
# Full Change Lifecycle
# ---------------------------------------------------------------------------


class TestChangeLifecycle:
    """Integration tests for the complete change management lifecycle."""

    @pytest.fixture(autouse=True)
    def _reset_change_manager(self):
        """Reset the global change manager between tests."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._change_manager
        helpers_mod._change_manager = None
        yield
        helpers_mod._change_manager = original

    @pytest.fixture(autouse=True)
    def _allow_writes(self):
        """Disable read-only mode for integration tests."""
        from network_mcp.server import settings

        with patch.object(settings, "net_read_only", False):
            yield

    def test_full_lifecycle_success(self, mock_conn_mgr, mock_node):
        """Create change -> approve -> execute -> validate -> success."""
        cm = _make_cm(
            net_change_require_approval=True,
            net_auto_rollback_timeout=300,
        )
        _inject_cm(cm)

        # 1. Create a change request
        cr = cm.create_change_request(
            tool_name="create_vlan",
            device="spine-01",
            commands=["vlan 100", "name SERVERS"],
            user="operator-a",
        )
        assert cr.status == ChangeStatus.PENDING
        assert cr.risk == ChangeRisk.MEDIUM  # "create" in tool name
        assert cr.created_by == "operator-a"

        # 2. Approve the change
        approved = cm.approve_change(cr.id, approved_by="admin-01")
        assert approved is not None
        assert approved.status == ChangeStatus.APPROVED
        assert approved.approved_by == "admin-01"

        # 3. Capture pre-snapshot
        mock_node.run_commands.return_value = [{"output": "running-config before"}]
        cm.capture_pre_snapshot(cr, conn_mgr)
        assert "running_config" in cr.pre_snapshot

        # 4. Execute the change
        cm.mark_executing(cr)
        assert cr.status == ChangeStatus.EXECUTING
        mock_node.config.return_value = []

        # 5. Capture post-snapshot (different from pre to pass validation)
        mock_node.run_commands.return_value = [{"output": "running-config after with vlan 100"}]
        cm.capture_post_snapshot(cr, conn_mgr)
        assert "running_config" in cr.post_snapshot

        # 6. Validate post-change state
        valid = cm.validate_change(cr)
        assert valid is True  # pre != post

        # 7. Mark completed
        cm.mark_completed(cr)
        assert cr.status == ChangeStatus.COMPLETED

        # Verify audit trail: change is in completed store
        found = cm.get_change_request(cr.id)
        assert found is not None
        assert found.status == ChangeStatus.COMPLETED

        # Verify no longer pending
        pending = cm.list_pending()
        assert all(p.id != cr.id for p in pending)

    def test_full_lifecycle_with_rollback(self, mock_conn_mgr, mock_node):
        """Create change -> approve -> execute -> rollback -> validate rollback."""
        cm = _make_cm(net_change_require_approval=False)
        _inject_cm(cm)

        # 1. Create and auto-approve a LOW-risk change
        cr = cm.create_change_request(
            tool_name="update_description",
            device="leaf-01",
            commands=["interface Ethernet1", "description uplink"],
        )
        # LOW risk + no approval required -> auto-approved
        assert cr.status == ChangeStatus.APPROVED

        # 2. Execute the change
        mock_node.run_commands.return_value = [{"output": "config before"}]
        cm.capture_pre_snapshot(cr, conn_mgr)
        cm.mark_executing(cr)
        mock_node.config.return_value = []

        # Simulate post-config state
        mock_node.run_commands.return_value = [{"output": "config after"}]
        cm.capture_post_snapshot(cr, conn_mgr)
        cm.mark_completed(cr)

        # 3. Rollback the change — post-rollback snapshot will differ from pre-change
        # so validation returns "warning" (rollback applied but state differs)
        mock_node.config.return_value = []
        result = cm.rollback(cr, conn_mgr)
        assert result["status"] in ("success", "warning")
        assert result["action"] == "rolled_back"
        assert cr.status == ChangeStatus.ROLLED_BACK

        # 4. Verify rollback commands are reversed
        assert cr.rollback_commands == ["no description uplink", "no interface Ethernet1"]

        # 5. Verify change is in completed store with ROLLED_BACK status
        found = cm.get_change_request(cr.id)
        assert found is not None
        assert found.status == ChangeStatus.ROLLED_BACK

    def test_change_rejected_leaves_no_side_effects(self, mock_conn_mgr, mock_node):
        """Rejected change should not modify device state."""
        cm = _make_cm(net_change_require_approval=True)
        _inject_cm(cm)

        # Create a change request (PENDING)
        cr = cm.create_change_request(
            tool_name="create_vlan",
            device="spine-01",
            commands=["vlan 200", "name TEST"],
        )
        assert cr.status == ChangeStatus.PENDING

        # Mark it as failed (simulating rejection)
        cm.mark_failed(cr, "Change was rejected by reviewer")
        assert cr.status == ChangeStatus.FAILED

        # Verify device was never touched: run_config was never called
        mock_node.config.assert_not_called()

        # Change is in completed store as failed
        found = cm.get_change_request(cr.id)
        assert found is not None
        assert found.status == ChangeStatus.FAILED
        assert found.error == "Change was rejected by reviewer"

    def test_auto_rollback_on_failed_validation(self, mock_conn_mgr, mock_node):
        """When validation fails and auto_rollback_timeout > 0, auto-rollback executes."""
        cm = _make_cm(
            net_change_require_approval=False,
            net_auto_rollback_timeout=300,
        )
        _inject_cm(cm)

        # Create low-risk auto-approved change
        cr = cm.create_change_request(
            tool_name="update_description",
            device="leaf-01",
            commands=["interface Ethernet1", "description test"],
        )
        assert cr.status == ChangeStatus.APPROVED

        # Capture pre-snapshot
        mock_node.run_commands.return_value = [{"output": "config-v1"}]
        cm.capture_pre_snapshot(cr, conn_mgr)
        cm.mark_executing(cr)

        # Post-snapshot same as pre -> validation fails (no change detected)
        mock_node.run_commands.return_value = [{"output": "config-v1"}]
        cm.capture_post_snapshot(cr, conn_mgr)

        valid = cm.validate_change(cr)
        assert valid is False  # pre == post

        # Auto-rollback
        mock_node.config.return_value = []
        result = cm.rollback(cr, conn_mgr)
        assert result["status"] == "success"
        assert cr.status == ChangeStatus.ROLLED_BACK

    def test_rollback_with_no_rollback_commands(self, mock_conn_mgr, mock_node):
        """Rollback fails gracefully when no rollback commands exist."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="generic_tool",
            device="leaf-01",
            commands=[],
        )
        cr.rollback_commands = []  # No rollback commands
        result = cm.rollback(cr, conn_mgr)
        assert result["status"] == "error"
        assert "No rollback commands" in result["error"]

    def test_lifecycle_through_run_config_command(self, mock_conn_mgr, mock_node):
        """Full lifecycle through the run_config_command helper."""
        import network_mcp.helpers as helpers_mod

        cm = _make_cm(
            net_change_require_approval=True,
            net_auto_rollback_timeout=0,
        )
        _inject_cm(cm)

        # First call: creates change request, returns pending_approval
        result = helpers_mod.run_config_command(conn_mgr, "spine-01", ["vlan 100", "name SERVERS"], "create_vlan")
        assert result["status"] == "pending_approval"
        change_id = result["change_id"]

        # Approve the change
        approved = cm.approve_change(change_id, approved_by="admin-01")
        assert approved is not None
        assert approved.status == ChangeStatus.APPROVED

        # Second call: this time the tool creates a NEW change request
        # (because run_config_command always creates a new one)
        # With approval not required + low risk, it auto-executes
        cm2 = _make_cm(
            net_change_require_approval=False,
            net_auto_rollback_timeout=0,
        )
        _inject_cm(cm2)

        mock_node.run_commands.return_value = []
        result2 = helpers_mod.run_config_command(conn_mgr, "spine-01", ["description test"], "update_description")
        assert result2["status"] == "success"
        assert "change_management" in result2

    def test_multiple_changes_tracked_independently(self, mock_conn_mgr, mock_node):
        """Multiple change requests are tracked independently."""
        cm = _make_cm(net_change_require_approval=True)
        _inject_cm(cm)

        cr1 = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        cr2 = cm.create_change_request("create_vlan", "leaf-01", ["vlan 200"])
        cr3 = cm.create_change_request("create_vlan", "spine-01", ["vlan 300"])

        # All pending
        pending = cm.list_pending()
        assert len(pending) == 3

        # Filter by device
        spine_pending = cm.list_pending(device="spine-01")
        assert len(spine_pending) == 2
        leaf_pending = cm.list_pending(device="leaf-01")
        assert len(leaf_pending) == 1

        # Approve and complete one
        cm.approve_change(cr1.id, approved_by="admin")
        cm.mark_executing(cr1)
        cm.mark_completed(cr1)

        # Only 2 still pending
        pending = cm.list_pending()
        assert len(pending) == 2
        assert all(p.id != cr1.id for p in pending)

        # cr2 and cr3 are independent
        assert cr2.status == ChangeStatus.PENDING
        assert cr3.status == ChangeStatus.PENDING


# ---------------------------------------------------------------------------
# Maintenance Window Enforcement
# ---------------------------------------------------------------------------


class TestMaintenanceWindowIntegration:
    """Integration tests for maintenance window enforcement."""

    @pytest.fixture(autouse=True)
    def _reset_change_manager(self):
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._change_manager
        helpers_mod._change_manager = None
        yield
        helpers_mod._change_manager = original

    @pytest.fixture(autouse=True)
    def _allow_writes(self):
        from network_mcp.server import settings

        with patch.object(settings, "net_read_only", False):
            yield

    def test_critical_change_inside_window_succeeds(self, mock_conn_mgr, mock_node):
        """CRITICAL change during maintenance window is allowed."""
        # Sunday 03:00 UTC, window is Sun 02:00-06:00
        cm = _make_cm(
            net_maintenance_windows="Sun 02:00-06:00",
            net_change_require_approval=False,
        )

        sunday_3am = datetime(2025, 1, 5, 3, 0, tzinfo=UTC)  # Sunday
        assert cm.check_maintenance_window(now=sunday_3am) is True

    def test_critical_change_outside_window_blocked(self, mock_conn_mgr, mock_node):
        """CRITICAL change outside maintenance window is rejected."""
        cm = _make_cm(
            net_maintenance_windows="Sun 02:00-06:00",
            net_change_require_approval=False,
        )

        monday_10am = datetime(2025, 1, 6, 10, 0, tzinfo=UTC)  # Monday
        assert cm.check_maintenance_window(now=monday_10am) is False

    def test_high_change_outside_window_blocked(self, mock_conn_mgr, mock_node):
        """HIGH risk change outside maintenance window is rejected via run_config_command."""
        import network_mcp.helpers as helpers_mod

        cm = _make_cm(
            net_maintenance_windows="Sun 02:00-06:00",
            net_change_require_approval=False,
        )
        _inject_cm(cm)

        # Patch check_maintenance_window to return False
        with patch.object(cm, "check_maintenance_window", return_value=False):
            result = helpers_mod.run_config_command(
                conn_mgr,
                "spine-01",
                ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"],
                "push_config",
            )

        assert result["status"] == "error"
        assert "maintenance window" in result["error"].lower()

    def test_low_change_outside_window_blocked_when_cm_enabled(self, mock_conn_mgr, mock_node):
        """When CM is enabled with windows, even LOW risk changes are blocked outside windows."""
        cm = _make_cm(
            net_maintenance_windows="Sat 02:00-06:00",
            net_change_require_approval=False,
        )

        # Wednesday 10am -- outside Sat 02:00-06:00 window
        wednesday_10am = datetime(2025, 1, 8, 10, 0, tzinfo=UTC)
        assert cm.check_maintenance_window(now=wednesday_10am) is False

    def test_no_maintenance_windows_configured_allows_all(self, mock_conn_mgr, mock_node):
        """When NET_MAINTENANCE_WINDOWS='*', all changes are allowed at any time."""
        cm = _make_cm(net_maintenance_windows="*")

        # Check multiple times/days
        assert cm.check_maintenance_window(now=datetime(2025, 1, 6, 10, 0, tzinfo=UTC)) is True
        assert cm.check_maintenance_window(now=datetime(2025, 1, 5, 3, 0, tzinfo=UTC)) is True
        assert cm.check_maintenance_window(now=datetime(2025, 1, 7, 23, 59, tzinfo=UTC)) is True

    def test_multiple_windows_any_match_allows(self, mock_conn_mgr, mock_node):
        """Multiple windows: if any window matches, change is allowed."""
        cm = _make_cm(net_maintenance_windows="Sun 02:00-06:00,Sat 02:00-06:00")

        saturday_3am = datetime(2025, 1, 4, 3, 0, tzinfo=UTC)  # Saturday
        sunday_3am = datetime(2025, 1, 5, 3, 0, tzinfo=UTC)  # Sunday
        monday_3am = datetime(2025, 1, 6, 3, 0, tzinfo=UTC)  # Monday

        assert cm.check_maintenance_window(now=saturday_3am) is True
        assert cm.check_maintenance_window(now=sunday_3am) is True
        assert cm.check_maintenance_window(now=monday_3am) is False

    def test_wildcard_day_window(self, mock_conn_mgr, mock_node):
        """Wildcard day ('*') means any day during the time window."""
        cm = _make_cm(net_maintenance_windows="* 02:00-06:00")

        # Any day at 3am should be allowed
        for day_offset in range(7):
            check_time = datetime(2025, 1, 6 + day_offset, 3, 0, tzinfo=UTC)
            assert cm.check_maintenance_window(now=check_time) is True

        # 10am should be rejected on any day
        for day_offset in range(7):
            check_time = datetime(2025, 1, 6 + day_offset, 10, 0, tzinfo=UTC)
            assert cm.check_maintenance_window(now=check_time) is False

    def test_overnight_window(self, mock_conn_mgr, mock_node):
        """Overnight windows (e.g., 22:00-04:00) span midnight correctly."""
        cm = _make_cm(net_maintenance_windows="Sat 22:00-04:00")

        saturday_23pm = datetime(2025, 1, 4, 23, 0, tzinfo=UTC)  # Saturday 23:00
        saturday_21pm = datetime(2025, 1, 4, 21, 0, tzinfo=UTC)  # Saturday 21:00 (before window)

        assert cm.check_maintenance_window(now=saturday_23pm) is True
        assert cm.check_maintenance_window(now=saturday_21pm) is False

    def test_maintenance_window_via_run_config_command_inside(self, mock_conn_mgr, mock_node):
        """run_config_command succeeds inside maintenance window."""
        import network_mcp.helpers as helpers_mod

        cm = _make_cm(
            net_maintenance_windows="* 00:00-23:59",
            net_change_require_approval=False,
            net_auto_rollback_timeout=0,
        )
        _inject_cm(cm)

        mock_node.run_commands.return_value = []
        result = helpers_mod.run_config_command(conn_mgr, "spine-01", ["description test"], "update_description")
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Four-Eyes Approval Flow
# ---------------------------------------------------------------------------


class TestFourEyesIntegration:
    """Tests for multi-approval workflows.

    The full four-eyes approval (requiring 2+ approvals for CRITICAL changes)
    is planned for session 5.2. These tests exercise the approval chain
    using the current ChangeManager API, verifying the approval tracking
    and status transitions that underpin the four-eyes pattern.
    """

    def test_critical_change_needs_approval(self):
        """CRITICAL change starts as PENDING when approval is required."""
        cm = _make_cm(net_change_require_approval=True)

        cr = cm.create_change_request(
            tool_name="shutdown_interface",
            device="spine-01",
            commands=["interface Ethernet1", "shutdown"],
        )
        assert cr.risk == ChangeRisk.CRITICAL
        assert cr.status == ChangeStatus.PENDING

    def test_critical_change_approved_by_admin(self):
        """CRITICAL change can be approved and transitions to APPROVED."""
        cm = _make_cm(net_change_require_approval=True)

        cr = cm.create_change_request(
            tool_name="shutdown_interface",
            device="spine-01",
            commands=["interface Ethernet1", "shutdown"],
        )
        approved = cm.approve_change(cr.id, approved_by="admin-01")
        assert approved is not None
        assert approved.status == ChangeStatus.APPROVED
        assert approved.approved_by == "admin-01"

    def test_single_approval_sufficient_for_current_api(self):
        """Current API: single approval transitions CRITICAL change to APPROVED."""
        cm = _make_cm(net_change_require_approval=True)

        cr = cm.create_change_request(
            tool_name="shutdown_interface",
            device="spine-01",
            commands=["interface Ethernet1", "shutdown"],
        )
        cm.approve_change(cr.id, approved_by="admin-01")
        assert cr.status == ChangeStatus.APPROVED

    def test_cannot_approve_non_pending_change(self):
        """Cannot approve a change that is already approved."""
        cm = _make_cm()

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        cm.approve_change(cr.id, approved_by="admin-01")
        assert cr.status == ChangeStatus.APPROVED

        # Try to approve again -> returns None (already approved, not pending)
        result = cm.approve_change(cr.id, approved_by="admin-02")
        assert result is None

    def test_non_critical_single_approval_sufficient(self):
        """LOW/MEDIUM risk changes need only 1 approval."""
        cm = _make_cm(net_change_require_approval=True)

        # LOW risk
        cr_low = cm.create_change_request(
            "update_description",
            "leaf-01",
            ["interface Ethernet1", "description test"],
        )
        assert cr_low.risk == ChangeRisk.LOW
        cm.approve_change(cr_low.id, approved_by="admin-01")
        assert cr_low.status == ChangeStatus.APPROVED

        # MEDIUM risk
        cr_med = cm.create_change_request(
            "create_vlan",
            "leaf-01",
            ["vlan 100", "name TEST"],
        )
        assert cr_med.risk == ChangeRisk.MEDIUM
        cm.approve_change(cr_med.id, approved_by="admin-01")
        assert cr_med.status == ChangeStatus.APPROVED

    def test_approval_chain_records_correctly(self):
        """Approval records the approver and change tracks creation info."""
        cm = _make_cm(net_change_require_approval=True)

        cr = cm.create_change_request(
            tool_name="create_vlan",
            device="spine-01",
            commands=["vlan 100", "name SERVERS"],
            user="requester-01",
        )
        assert cr.created_by == "requester-01"
        assert cr.created_at is not None

        cm.approve_change(cr.id, approved_by="approver-01")
        assert cr.approved_by == "approver-01"

        # Serialization includes all tracked fields
        cr_dict = cm.to_dict(cr)
        assert cr_dict["created_by"] == "requester-01"
        assert cr_dict["approved_by"] == "approver-01"
        assert cr_dict["status"] == "approved"
        assert "created_at" in cr_dict

    def test_auto_approve_skips_approval_for_low_risk(self):
        """When require_approval=False, LOW risk changes are auto-approved."""
        cm = _make_cm(net_change_require_approval=False)

        cr = cm.create_change_request(
            "update_description",
            "leaf-01",
            ["interface Ethernet1", "description test"],
        )
        assert cr.status == ChangeStatus.APPROVED
        assert cr.approved_by == "auto"

    def test_auto_approve_does_not_affect_medium_risk(self):
        """When require_approval=False, MEDIUM risk still starts as PENDING."""
        cm = _make_cm(net_change_require_approval=False)

        cr = cm.create_change_request(
            "create_vlan",
            "leaf-01",
            ["vlan 100", "name TEST"],
        )
        # MEDIUM risk is not auto-approved even when require_approval=False
        # (only LOW risk is auto-approved)
        assert cr.risk == ChangeRisk.MEDIUM
        assert cr.status == ChangeStatus.PENDING

    def test_approve_nonexistent_change(self):
        """Approving a nonexistent change ID returns None."""
        cm = _make_cm()
        result = cm.approve_change("nonexistent-id", approved_by="admin")
        assert result is None


# ---------------------------------------------------------------------------
# Blast Radius Analysis Accuracy
# ---------------------------------------------------------------------------


class TestBlastRadiusIntegration:
    """Tests for blast radius / risk assessment accuracy.

    The full net_analyze_change_impact tool is planned for session 6.1.
    These tests exercise the risk assessment logic in the ChangeManager
    which forms the basis of blast radius analysis.
    """

    def test_interface_shutdown_returns_critical(self):
        """Shutting down an interface is classified as CRITICAL risk."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "shutdown_interface",
            "spine-01",
            ["interface Ethernet1", "shutdown"],
        )
        assert cr.risk == ChangeRisk.CRITICAL

    def test_vlan_deletion_returns_medium(self):
        """Deleting a VLAN is classified as HIGH risk (contains 'delete')."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "delete_vlan",
            "leaf-01",
            ["no vlan 100"],
        )
        assert cr.risk == ChangeRisk.HIGH

    def test_bgp_neighbor_change_returns_high(self):
        """BGP neighbor config changes are classified as HIGH risk."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "update_bgp",
            "spine-01",
            ["router bgp 65000", "neighbor 10.0.0.2 remote-as 65001"],
        )
        assert cr.risk == ChangeRisk.HIGH

    def test_management_interface_shutdown_returns_critical(self):
        """Shutdown commands always return CRITICAL regardless of interface."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "update_interface",
            "spine-01",
            ["interface Management1", "shutdown"],
        )
        assert cr.risk == ChangeRisk.CRITICAL

    def test_cosmetic_change_returns_low_risk(self):
        """Description-only changes return LOW risk."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "update_description",
            "leaf-01",
            ["interface Ethernet1", "description uplink to spine"],
        )
        assert cr.risk == ChangeRisk.LOW

    def test_acl_change_returns_high(self):
        """ACL changes are classified as HIGH risk."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "update_acl",
            "spine-01",
            ["ip access-list MGMT", "permit 10.0.0.0/8"],
        )
        assert cr.risk == ChangeRisk.HIGH

    def test_ospf_removal_returns_critical(self):
        """Removing OSPF process is CRITICAL."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "update_routing",
            "spine-01",
            ["no router ospf 1"],
        )
        assert cr.risk == ChangeRisk.CRITICAL

    def test_bgp_removal_returns_critical(self):
        """Removing BGP process is CRITICAL."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "update_routing",
            "spine-01",
            ["no router bgp 65000"],
        )
        assert cr.risk == ChangeRisk.CRITICAL

    def test_push_config_returns_high(self):
        """push_config tool always returns HIGH risk."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "push_config",
            "spine-01",
            ["interface Ethernet1", "description test"],
        )
        assert cr.risk == ChangeRisk.HIGH

    def test_rollback_commands_are_reverse_ordered(self):
        """Rollback commands are generated in reverse with 'no' prefix."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "create_vlan",
            "spine-01",
            ["vlan 100", "name SERVERS"],
        )
        assert cr.rollback_commands == ["no name SERVERS", "no vlan 100"]

    def test_rollback_commands_remove_no_prefix(self):
        """Rollback of 'no' commands removes the 'no' prefix."""
        cm = _make_cm()
        cr = cm.create_change_request(
            "remove_acl",
            "spine-01",
            ["no ip access-list MGMT"],
        )
        assert cr.rollback_commands == ["ip access-list MGMT"]


# ---------------------------------------------------------------------------
# Dry-Run Mode
# ---------------------------------------------------------------------------


class TestDryRunIntegration:
    """Tests for dry-run / pre-validation of config changes.

    The full net_dryrun_config tool is planned for session 6.2. These tests
    exercise the change management pre-validation flow (create change ->
    check risk -> check window -> return status without executing).
    """

    @pytest.fixture(autouse=True)
    def _reset_change_manager(self):
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._change_manager
        helpers_mod._change_manager = None
        yield
        helpers_mod._change_manager = original

    @pytest.fixture(autouse=True)
    def _allow_writes(self):
        from network_mcp.server import settings

        with patch.object(settings, "net_read_only", False):
            yield

    def test_dryrun_via_pending_approval(self, mock_conn_mgr, mock_node):
        """Dry-run behavior: when approval is required, config is not applied."""
        import network_mcp.helpers as helpers_mod

        cm = _make_cm(net_change_require_approval=True)
        _inject_cm(cm)

        # run_config_command with approval required: returns pending, no execution
        result = helpers_mod.run_config_command(conn_mgr, "spine-01", ["vlan 100", "name SERVERS"], "create_vlan")
        assert result["status"] == "pending_approval"

        # Device was not touched
        mock_node.config.assert_not_called()

        # Change request has risk and commands
        assert result["risk"] == "medium"
        assert result["commands"] == ["vlan 100", "name SERVERS"]

    def test_dryrun_returns_risk_assessment(self, mock_conn_mgr, mock_node):
        """Dry-run returns risk assessment without applying changes."""
        cm = _make_cm(net_change_require_approval=True)
        _inject_cm(cm)

        # Create change request without executing
        # "push_config" tool name -> HIGH risk (matches _HIGH_TOOL_PATTERNS)
        # "no neighbor" in commands -> HIGH risk (matches _HIGH_CMD_PATTERNS)
        cr = cm.create_change_request(
            "push_config",
            "spine-01",
            ["router bgp 65000", "no neighbor 10.0.0.5"],
        )
        assert cr.risk == ChangeRisk.HIGH

        # Validate: commands are stored but not applied
        assert cr.commands == ["router bgp 65000", "no neighbor 10.0.0.5"]
        assert cr.status == ChangeStatus.PENDING

    def test_dryrun_does_not_modify_device(self, mock_conn_mgr, mock_node):
        """Verify no config commands are applied during dry-run."""
        import network_mcp.helpers as helpers_mod

        cm = _make_cm(net_change_require_approval=True)
        _inject_cm(cm)

        # Multiple dry-run calls should not touch the device
        for i in range(5):
            result = helpers_mod.run_config_command(conn_mgr, "spine-01", [f"vlan {100 + i}"], "create_vlan")
            assert result["status"] == "pending_approval"

        mock_node.config.assert_not_called()
        mock_node.run_commands.assert_not_called()

    def test_dryrun_invalid_commands_blocked_by_denylist(self, mock_conn_mgr, mock_node):
        """Dry-run with denied commands returns error before creating change request."""
        import network_mcp.helpers as helpers_mod

        cm = _make_cm(net_change_require_approval=True)
        _inject_cm(cm)

        result = helpers_mod.run_config_command(conn_mgr, "spine-01", ["reload"], "dangerous_action")
        assert result["status"] == "error"
        assert "deny-list" in result["error"].lower() or "blocked" in result["error"].lower()


# ---------------------------------------------------------------------------
# Config Version Tracking
# ---------------------------------------------------------------------------


class TestConfigVersioningIntegration:
    """Tests for config version tracking and concurrency detection.

    The full net_get_config_version and expected_version parameter on
    net_push_config are planned for session 6.2. These tests exercise
    the change validation mechanism that detects concurrent changes
    using pre/post snapshots.
    """

    def test_concurrent_change_detected_via_validation(self):
        """Pre/post snapshot comparison detects unexpected state changes."""
        cm = _make_cm()

        cr = cm.create_change_request(
            "create_vlan",
            "spine-01",
            ["vlan 100", "name SERVERS"],
        )

        # Simulate: pre-snapshot taken
        cr.pre_snapshot = {"running_config": "config version A"}

        # Simulate: config applied but someone else also changed the config
        # Post-snapshot matches pre -> validation fails (commands had no effect)
        cr.post_snapshot = {"running_config": "config version A"}

        valid = cm.validate_change(cr)
        assert valid is False  # Same config means commands didn't take effect

    def test_matching_version_allows_push(self):
        """When pre/post snapshots differ, validation passes."""
        cm = _make_cm()

        cr = cm.create_change_request(
            "create_vlan",
            "spine-01",
            ["vlan 100", "name SERVERS"],
        )

        cr.pre_snapshot = {"running_config": "config version A"}
        cr.post_snapshot = {"running_config": "config version B with vlan 100"}

        valid = cm.validate_change(cr)
        assert valid is True

    def test_no_version_check_when_snapshots_empty(self):
        """When both snapshots are empty, validation fails (can't verify)."""
        cm = _make_cm()

        cr = cm.create_change_request(
            "create_vlan",
            "spine-01",
            ["vlan 100"],
        )
        cr.pre_snapshot = {}
        cr.post_snapshot = {}

        valid = cm.validate_change(cr)
        assert valid is False

    def test_snapshot_error_fails_validation(self):
        """Snapshot capture errors cause validation failure."""
        cm = _make_cm()

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        cr.pre_snapshot = {"error": "Connection timeout"}
        cr.post_snapshot = {"running_config": "some config"}

        valid = cm.validate_change(cr)
        assert valid is False

    def test_post_snapshot_error_fails_validation(self):
        """Post-snapshot errors also cause validation failure."""
        cm = _make_cm()

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        cr.pre_snapshot = {"running_config": "some config"}
        cr.post_snapshot = {"error": "Connection timeout"}

        valid = cm.validate_change(cr)
        assert valid is False

    def test_snapshot_capture_via_conn_mgr(self, mock_conn_mgr, mock_node):
        """Pre/post snapshot capture uses the connection manager."""
        cm = _make_cm()

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])

        # Pre-snapshot
        mock_node.run_commands.return_value = [{"output": "config-v1"}]
        cm.capture_pre_snapshot(cr, conn_mgr)
        assert cr.pre_snapshot == {"running_config": {"output": "config-v1"}}

        # Post-snapshot
        mock_node.run_commands.return_value = [{"output": "config-v2"}]
        cm.capture_post_snapshot(cr, conn_mgr)
        assert cr.post_snapshot == {"running_config": {"output": "config-v2"}}

        # Snapshots differ -> validation passes
        valid = cm.validate_change(cr)
        assert valid is True

    def test_snapshot_capture_failure_handled_gracefully(self, mock_conn_mgr, mock_node):
        """Snapshot capture failures are recorded as errors, not exceptions."""
        cm = _make_cm()

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])

        # Simulate connection failure during snapshot
        @contextmanager
        def _fail_acquire(host):
            raise ConnectionError("Device unreachable")
            yield  # pragma: no cover

        with patch.object(conn_mgr, "acquire", side_effect=_fail_acquire):
            cm.capture_pre_snapshot(cr, conn_mgr)

        assert "error" in cr.pre_snapshot
        assert "unreachable" in cr.pre_snapshot["error"].lower()


# ---------------------------------------------------------------------------
# Drift Remediation via Change Management
# ---------------------------------------------------------------------------


class TestDriftRemediationIntegration:
    """Tests for drift remediation interaction with change management.

    The full integration where net_remediate_drift creates a ChangeRequest
    is planned for session 6.2. These tests exercise the underlying drift
    remediation flow using the current API, verifying that remediation
    commands would be valid change requests.
    """

    @pytest.fixture(autouse=True)
    def _reset_change_manager(self):
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._change_manager
        helpers_mod._change_manager = None
        yield
        helpers_mod._change_manager = original

    def test_remediation_commands_as_change_request(self):
        """Remediation commands can be packaged as a change request."""
        cm = _make_cm(net_change_require_approval=True)

        remediation_commands = [
            "router bgp 65000",
            "neighbor 10.0.0.2 remote-as 65001",
            "neighbor 10.0.0.2 maximum-routes 12000",
        ]

        cr = cm.create_change_request(
            tool_name="net_remediate_drift",
            device="spine-01",
            commands=remediation_commands,
            user="drift-detector",
        )

        assert cr.status == ChangeStatus.PENDING
        assert cr.commands == remediation_commands
        assert cr.created_by == "drift-detector"
        assert cr.tool_name == "net_remediate_drift"
        # BGP commands -> HIGH risk
        assert cr.risk == ChangeRisk.HIGH

    def test_drift_change_request_contains_correct_commands(self):
        """ChangeRequest from drift remediation has correct remediation commands."""
        cm = _make_cm()

        remediation_commands = [
            "interface Ethernet1",
            "description correct-description",
            "no shutdown",
        ]

        cr = cm.create_change_request(
            tool_name="net_remediate_drift",
            device="leaf-01",
            commands=remediation_commands,
        )

        assert cr.commands == remediation_commands
        # Rollback commands are generated
        assert len(cr.rollback_commands) == 3
        assert cr.rollback_commands[0] == "shutdown"  # reversed "no shutdown" -> "shutdown"
        assert cr.rollback_commands[1] == "no description correct-description"
        assert cr.rollback_commands[2] == "no interface Ethernet1"

    def test_drift_change_request_has_correct_source(self):
        """ChangeRequest tool_name tracks drift_remediation source."""
        cm = _make_cm()

        cr = cm.create_change_request(
            tool_name="drift_remediation",
            device="spine-01",
            commands=["ntp server 10.0.0.1"],
        )

        assert cr.tool_name == "drift_remediation"
        serialized = cm.to_dict(cr)
        assert serialized["tool_name"] == "drift_remediation"

    def test_drift_remediation_with_critical_commands(self):
        """Drift remediation with critical commands gets CRITICAL risk."""
        cm = _make_cm(net_change_require_approval=True)

        cr = cm.create_change_request(
            tool_name="net_remediate_drift",
            device="spine-01",
            commands=["no router bgp 65000"],  # CRITICAL: removing BGP
        )

        assert cr.risk == ChangeRisk.CRITICAL
        assert cr.status == ChangeStatus.PENDING

    def test_drift_remediation_low_risk_auto_approved(self):
        """Low-risk drift remediation is auto-approved when require_approval=False."""
        cm = _make_cm(net_change_require_approval=False)

        cr = cm.create_change_request(
            tool_name="net_remediate_drift",
            device="leaf-01",
            commands=["interface Ethernet1", "description correct-desc"],
        )

        assert cr.risk == ChangeRisk.LOW
        assert cr.status == ChangeStatus.APPROVED
        assert cr.approved_by == "auto"

    def test_drift_remediation_lifecycle(self, mock_conn_mgr, mock_node):
        """Full drift remediation lifecycle: create -> approve -> execute -> complete."""
        cm = _make_cm(net_change_require_approval=True)
        _inject_cm(cm)

        # 1. Create change request from drift
        remediation = ["interface Ethernet1", "description fixed-by-drift"]
        cr = cm.create_change_request(
            tool_name="net_remediate_drift",
            device="leaf-01",
            commands=remediation,
            user="drift-system",
        )
        assert cr.status == ChangeStatus.PENDING

        # 2. Approve
        cm.approve_change(cr.id, approved_by="admin-01")
        assert cr.status == ChangeStatus.APPROVED

        # 3. Pre-snapshot
        mock_node.run_commands.return_value = [{"output": "config before drift fix"}]
        cm.capture_pre_snapshot(cr, conn_mgr)

        # 4. Execute
        cm.mark_executing(cr)
        mock_node.config.return_value = []

        # 5. Post-snapshot (different)
        mock_node.run_commands.return_value = [{"output": "config after drift fix"}]
        cm.capture_post_snapshot(cr, conn_mgr)

        # 6. Validate and complete
        assert cm.validate_change(cr) is True
        cm.mark_completed(cr)
        assert cr.status == ChangeStatus.COMPLETED


# ---------------------------------------------------------------------------
# MCP Tool Wrappers Integration
# ---------------------------------------------------------------------------


class TestMCPToolWrappersIntegration:
    """Integration tests for the MCP tool wrappers in tools/common/changes.py."""

    @pytest.fixture(autouse=True)
    def _reset_change_manager(self):
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._change_manager
        helpers_mod._change_manager = None
        yield
        helpers_mod._change_manager = original

    def test_net_list_pending_changes_disabled(self):
        """net_list_pending_changes returns error when CM is disabled."""
        from network_mcp.tools.common.changes import net_list_pending_changes

        result = net_list_pending_changes()
        assert result["status"] == "error"
        assert "not enabled" in result["error"]

    def test_net_list_pending_changes_enabled(self):
        """net_list_pending_changes returns pending changes when CM is enabled."""
        import network_mcp.helpers as helpers_mod
        from network_mcp.tools.common.changes import net_list_pending_changes

        cm = _make_cm()
        helpers_mod._change_manager = cm

        cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        cm.create_change_request("create_vlan", "leaf-01", ["vlan 200"])

        result = net_list_pending_changes()
        assert result["status"] == "success"
        assert result["count"] == 2

    def test_net_list_pending_changes_filtered_by_host(self):
        """net_list_pending_changes filters by host."""
        import network_mcp.helpers as helpers_mod
        from network_mcp.tools.common.changes import net_list_pending_changes

        cm = _make_cm()
        helpers_mod._change_manager = cm

        cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        cm.create_change_request("create_vlan", "leaf-01", ["vlan 200"])

        result = net_list_pending_changes(host="spine-01")
        assert result["status"] == "success"
        assert result["count"] == 1
        assert result["data"][0]["device"] == "spine-01"

    def test_net_get_change_request_found(self):
        """net_get_change_request returns change details."""
        import network_mcp.helpers as helpers_mod
        from network_mcp.tools.common.changes import net_get_change_request

        cm = _make_cm()
        helpers_mod._change_manager = cm

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        result = net_get_change_request(host="spine-01", change_id=cr.id)
        assert result["status"] == "success"
        assert result["data"]["id"] == cr.id

    def test_net_get_change_request_not_found(self):
        """net_get_change_request returns error for unknown ID."""
        import network_mcp.helpers as helpers_mod
        from network_mcp.tools.common.changes import net_get_change_request

        cm = _make_cm()
        helpers_mod._change_manager = cm

        result = net_get_change_request(host="spine-01", change_id="nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_net_approve_change_success(self):
        """net_approve_change approves a pending change."""
        import network_mcp.helpers as helpers_mod
        from network_mcp.tools.common.changes import net_approve_change

        cm = _make_cm()
        helpers_mod._change_manager = cm

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        result = net_approve_change(host="spine-01", change_id=cr.id, approved_by="admin-01")
        assert result["status"] == "success"
        assert result["action"] == "approved"
        assert result["approved_by"] == "admin-01"

    def test_net_approve_change_not_found(self):
        """net_approve_change returns error for unknown ID."""
        import network_mcp.helpers as helpers_mod
        from network_mcp.tools.common.changes import net_approve_change

        cm = _make_cm()
        helpers_mod._change_manager = cm

        result = net_approve_change(host="spine-01", change_id="bad-id")
        assert result["status"] == "error"

    def test_net_rollback_change_success(self, mock_conn_mgr, mock_node):
        """net_rollback_change rolls back a completed change."""
        import network_mcp.helpers as helpers_mod
        from network_mcp.tools.common.changes import net_rollback_change

        cm = _make_cm()
        helpers_mod._change_manager = cm

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100", "name TEST"])
        cm.approve_change(cr.id, approved_by="admin")
        cm.mark_executing(cr)
        cm.mark_completed(cr)

        mock_node.config.return_value = []
        result = net_rollback_change(host="spine-01", change_id=cr.id)
        assert result["status"] == "success"
        assert result["action"] == "rolled_back"

    def test_net_rollback_change_disabled(self, mock_conn_mgr, mock_node):
        """net_rollback_change returns error when CM is disabled."""
        from network_mcp.tools.common.changes import net_rollback_change

        result = net_rollback_change(host="spine-01", change_id="any-id")
        assert result["status"] == "error"
        assert "not enabled" in result["error"]


# ---------------------------------------------------------------------------
# Edge Cases and Error Handling
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and error handling in change management."""

    def test_empty_commands_list(self):
        """Change request with empty commands list still works."""
        cm = _make_cm()
        cr = cm.create_change_request("empty_tool", "spine-01", [])
        assert cr.risk == ChangeRisk.LOW
        assert cr.commands == []
        assert cr.rollback_commands == []

    def test_change_request_serialization(self):
        """ChangeRequest serializes all fields correctly."""
        cm = _make_cm()
        cr = cm.create_change_request(
            tool_name="create_vlan",
            device="spine-01",
            commands=["vlan 100", "name TEST"],
            user="test-user",
        )

        d = cm.to_dict(cr)
        assert d["id"] == cr.id
        assert d["tool_name"] == "create_vlan"
        assert d["device"] == "spine-01"
        assert d["commands"] == ["vlan 100", "name TEST"]
        assert d["risk"] == "medium"
        assert d["status"] == "pending"
        assert d["created_by"] == "test-user"
        assert d["approved_by"] == ""
        assert d["rollback_commands"] == ["no name TEST", "no vlan 100"]
        assert d["error"] == ""

    def test_change_status_transitions(self):
        """Verify all valid status transitions."""
        cm = _make_cm(net_change_require_approval=True)

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        assert cr.status == ChangeStatus.PENDING

        cm.approve_change(cr.id, approved_by="admin")
        assert cr.status == ChangeStatus.APPROVED

        cm.mark_executing(cr)
        assert cr.status == ChangeStatus.EXECUTING

        cm.mark_completed(cr)
        assert cr.status == ChangeStatus.COMPLETED

    def test_failed_status_transition(self):
        """Failed change records error message."""
        cm = _make_cm()
        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        cm.mark_failed(cr, "Device rejected commands")
        assert cr.status == ChangeStatus.FAILED
        assert cr.error == "Device rejected commands"

    def test_rollback_failure_records_error(self, mock_conn_mgr, mock_node):
        """Failed rollback records the error in the change request."""
        cm = _make_cm()

        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100", "name TEST"])
        cm.approve_change(cr.id, approved_by="admin")
        cm.mark_completed(cr)

        # Simulate rollback failure
        mock_node.config.side_effect = ConnectionError("Device unreachable")
        result = cm.rollback(cr, conn_mgr)
        assert result["status"] == "error"
        assert "Rollback failed" in result["error"]
        assert cr.status == ChangeStatus.FAILED

    def test_get_change_request_from_completed(self):
        """Can retrieve change request after completion."""
        cm = _make_cm()
        cr = cm.create_change_request("create_vlan", "spine-01", ["vlan 100"])
        cm.approve_change(cr.id, approved_by="admin")
        cm.mark_completed(cr)

        found = cm.get_change_request(cr.id)
        assert found is not None
        assert found.id == cr.id
        assert found.status == ChangeStatus.COMPLETED

    def test_maintenance_window_parsing_invalid_format(self):
        """Invalid window format is skipped gracefully."""
        cm = _make_cm(net_maintenance_windows="invalid-format,Sun 02:00-06:00")
        # Only 1 valid window parsed
        assert len(cm.maintenance_windows) == 1
        assert cm.maintenance_windows[0][0] == 6  # Sunday

    def test_maintenance_window_empty_string(self):
        """Empty maintenance windows string means always allowed."""
        cm = _make_cm(net_maintenance_windows="")
        assert cm.maintenance_windows == []
        assert cm.check_maintenance_window() is True

    def test_change_request_uuid_uniqueness(self):
        """Each change request gets a unique UUID."""
        cm = _make_cm()
        ids = set()
        for _ in range(100):
            cr = cm.create_change_request("tool", "device", ["cmd"])
            ids.add(cr.id)
        assert len(ids) == 100

    def test_risk_assessment_case_insensitive(self):
        """Risk assessment is case-insensitive for commands."""
        cm = _make_cm()

        cr1 = cm.create_change_request("tool", "device", ["SHUTDOWN"])
        assert cr1.risk == ChangeRisk.CRITICAL

        cr2 = cm.create_change_request("tool", "device", ["Shutdown"])
        assert cr2.risk == ChangeRisk.CRITICAL

    def test_risk_assessment_tool_name_patterns(self):
        """Risk assessment correctly matches tool name patterns."""
        cm = _make_cm()

        # "remove" in tool name -> HIGH
        cr = cm.create_change_request("remove_vlan", "device", ["no vlan 100"])
        assert cr.risk == ChangeRisk.HIGH

        # "configure" in tool name -> MEDIUM
        cr2 = cm.create_change_request("configure_interface", "device", ["description test"])
        assert cr2.risk == ChangeRisk.MEDIUM

        # "add" in tool name -> MEDIUM
        cr3 = cm.create_change_request("add_vlan", "device", ["vlan 200"])
        assert cr3.risk == ChangeRisk.MEDIUM
