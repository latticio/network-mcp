"""Tests for enterprise change management workflows."""

from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.change_management import (
    ChangeManager,
    ChangeRequest,
    ChangeRisk,
    ChangeStatus,
)
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Fixtures
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


@pytest.fixture()
def cm():
    """ChangeManager with defaults (enabled, require approval, always-open window)."""
    return ChangeManager(_make_settings())


@pytest.fixture()
def cm_no_approval():
    """ChangeManager with auto-approval for LOW risk."""
    return ChangeManager(_make_settings(net_change_require_approval=False))


@pytest.fixture()
def cm_disabled():
    """ChangeManager that is disabled."""
    return ChangeManager(_make_settings(net_change_mgmt_enabled=False))


@pytest.fixture()
def cm_with_windows():
    """ChangeManager with specific maintenance windows."""
    return ChangeManager(_make_settings(net_maintenance_windows="Sun 02:00-06:00,Sat 02:00-06:00"))


@pytest.fixture()
def mock_driver():
    """A mock driver that simulates device interaction."""
    driver = MagicMock()
    driver.run_show.return_value = [{"output": "running-config content"}]
    driver.run_config.return_value = []
    return driver


@pytest.fixture()
def mock_conn(mock_driver):
    """Patched conn_mgr that yields mock_driver."""

    @contextmanager
    def _acquire(host):
        yield mock_driver

    with (
        patch.object(conn_mgr, "acquire", side_effect=_acquire),
        patch.object(conn_mgr, "get_driver", return_value=mock_driver),
    ):
        yield conn_mgr


# ---------------------------------------------------------------------------
# ChangeManager: creation
# ---------------------------------------------------------------------------


class TestCreateChangeRequest:
    def test_create_basic(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100", "name SERVERS"])
        assert cr.id
        assert cr.tool_name == "net_create_vlan"
        assert cr.device == "spine-01"
        assert cr.commands == ["vlan 100", "name SERVERS"]
        assert cr.status == ChangeStatus.PENDING
        assert cr.risk == ChangeRisk.MEDIUM  # "create" in tool name

    def test_create_with_user(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"], user="admin@corp.com")
        assert cr.created_by == "admin@corp.com"

    def test_create_generates_uuid(self, cm):
        cr1 = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cr2 = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 200"])
        assert cr1.id != cr2.id

    def test_create_generates_rollback(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100", "name SERVERS"])
        assert cr.rollback_commands == ["no name SERVERS", "no vlan 100"]

    def test_create_rollback_for_no_prefix(self, cm):
        cr = cm.create_change_request("something", "spine-01", ["no vlan 100"])
        assert cr.rollback_commands == ["vlan 100"]

    def test_create_is_stored_in_pending(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        assert cm.get_change_request(cr.id) is cr


# ---------------------------------------------------------------------------
# Risk assessment
# ---------------------------------------------------------------------------


class TestRiskAssessment:
    def test_critical_shutdown_tool(self, cm):
        cr = cm.create_change_request("shutdown_interface", "spine-01", ["interface Ethernet1", "shutdown"])
        assert cr.risk == ChangeRisk.CRITICAL

    def test_critical_shutdown_in_commands(self, cm):
        cr = cm.create_change_request("configure_interface", "spine-01", ["interface Ethernet1", "shutdown"])
        assert cr.risk == ChangeRisk.CRITICAL

    def test_critical_no_router_bgp(self, cm):
        cr = cm.create_change_request("something", "spine-01", ["no router bgp 65000"])
        assert cr.risk == ChangeRisk.CRITICAL

    def test_high_delete_tool(self, cm):
        cr = cm.create_change_request("net_delete_vlan", "spine-01", ["no vlan 100"])
        assert cr.risk == ChangeRisk.HIGH

    def test_high_push_config_tool(self, cm):
        cr = cm.create_change_request("net_push_config", "spine-01", ["hostname test"])
        assert cr.risk == ChangeRisk.HIGH

    def test_high_bgp_command(self, cm):
        cr = cm.create_change_request("something", "spine-01", ["router bgp 65000"])
        assert cr.risk == ChangeRisk.HIGH

    def test_high_acl_command(self, cm):
        cr = cm.create_change_request("something", "spine-01", ["ip access-list MGMT"])
        assert cr.risk == ChangeRisk.HIGH

    def test_medium_create_tool(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        assert cr.risk == ChangeRisk.MEDIUM

    def test_medium_configure_tool(self, cm):
        cr = cm.create_change_request("net_configure_interface", "spine-01", ["description test"])
        assert cr.risk == ChangeRisk.MEDIUM

    def test_medium_add_tool(self, cm):
        cr = cm.create_change_request("net_add_static_route", "spine-01", ["ip route 10.0.0.0/8 10.1.1.1"])
        assert cr.risk == ChangeRisk.MEDIUM

    def test_low_default(self, cm):
        cr = cm.create_change_request("net_rename_thing", "spine-01", ["description new-name"])
        assert cr.risk == ChangeRisk.LOW


# ---------------------------------------------------------------------------
# Auto-approval
# ---------------------------------------------------------------------------


class TestAutoApproval:
    def test_auto_approve_low_risk(self, cm_no_approval):
        cr = cm_no_approval.create_change_request("net_rename_thing", "spine-01", ["description test"])
        assert cr.risk == ChangeRisk.LOW
        assert cr.status == ChangeStatus.APPROVED
        assert cr.approved_by == "auto"

    def test_no_auto_approve_medium_risk(self, cm_no_approval):
        cr = cm_no_approval.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        assert cr.risk == ChangeRisk.MEDIUM
        assert cr.status == ChangeStatus.PENDING

    def test_no_auto_approve_when_required(self, cm):
        cr = cm.create_change_request("net_rename_thing", "spine-01", ["description test"])
        assert cr.risk == ChangeRisk.LOW
        assert cr.status == ChangeStatus.PENDING  # require_approval=True


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class TestApproveChange:
    def test_approve_pending(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        result = cm.approve_change(cr.id, approved_by="admin")
        assert result is not None
        assert result.status == ChangeStatus.APPROVED
        assert result.approved_by == "admin"

    def test_approve_nonexistent(self, cm):
        result = cm.approve_change("fake-id", approved_by="admin")
        assert result is None

    def test_approve_already_approved(self, cm_no_approval):
        cr = cm_no_approval.create_change_request("net_rename_thing", "spine-01", ["description test"])
        assert cr.status == ChangeStatus.APPROVED
        # Try to approve again
        result = cm_no_approval.approve_change(cr.id, approved_by="admin2")
        assert result is None  # already approved, not pending


# ---------------------------------------------------------------------------
# Maintenance windows
# ---------------------------------------------------------------------------


class TestMaintenanceWindows:
    def test_wildcard_always_allowed(self, cm):
        assert cm.check_maintenance_window() is True

    def test_empty_string_always_allowed(self):
        cm = ChangeManager(_make_settings(net_maintenance_windows=""))
        assert cm.check_maintenance_window() is True

    def test_inside_window(self, cm_with_windows):
        # Sunday 03:00 UTC should be inside Sun 02:00-06:00
        dt = datetime(2025, 1, 5, 3, 0)  # 2025-01-05 is a Sunday
        assert cm_with_windows.check_maintenance_window(now=dt) is True

    def test_outside_window(self, cm_with_windows):
        # Monday 10:00 UTC should be outside
        dt = datetime(2025, 1, 6, 10, 0)  # 2025-01-06 is a Monday
        assert cm_with_windows.check_maintenance_window(now=dt) is False

    def test_at_window_start(self, cm_with_windows):
        # Sunday 02:00 exactly
        dt = datetime(2025, 1, 5, 2, 0)
        assert cm_with_windows.check_maintenance_window(now=dt) is True

    def test_at_window_end(self, cm_with_windows):
        # Sunday 06:00 exactly
        dt = datetime(2025, 1, 5, 6, 0)
        assert cm_with_windows.check_maintenance_window(now=dt) is True

    def test_saturday_in_window(self, cm_with_windows):
        # Saturday 04:00
        dt = datetime(2025, 1, 4, 4, 0)  # 2025-01-04 is a Saturday
        assert cm_with_windows.check_maintenance_window(now=dt) is True

    def test_overnight_window(self):
        cm = ChangeManager(_make_settings(net_maintenance_windows="Sat 22:00-04:00"))
        # Saturday 23:00 — should be in window
        dt_in = datetime(2025, 1, 4, 23, 0)
        assert cm.check_maintenance_window(now=dt_in) is True
        # Saturday 01:00 — should be in window (overnight)
        dt_early = datetime(2025, 1, 4, 1, 0)
        assert cm.check_maintenance_window(now=dt_early) is True
        # Saturday 10:00 — should be outside
        dt_out = datetime(2025, 1, 4, 10, 0)
        assert cm.check_maintenance_window(now=dt_out) is False

    def test_wildcard_day_window(self):
        cm = ChangeManager(_make_settings(net_maintenance_windows="* 02:00-04:00"))
        # Any day at 03:00 should be inside
        dt = datetime(2025, 1, 6, 3, 0)  # Monday
        assert cm.check_maintenance_window(now=dt) is True
        # Any day at 10:00 should be outside
        dt2 = datetime(2025, 1, 6, 10, 0)
        assert cm.check_maintenance_window(now=dt2) is False

    def test_invalid_window_format_ignored(self):
        cm = ChangeManager(_make_settings(net_maintenance_windows="invalid,Sun 02:00-06:00"))
        # Only the valid window should be parsed
        assert len(cm.maintenance_windows) == 1
        dt = datetime(2025, 1, 5, 3, 0)  # Sunday
        assert cm.check_maintenance_window(now=dt) is True


# ---------------------------------------------------------------------------
# Snapshot capture
# ---------------------------------------------------------------------------


class TestSnapshotCapture:
    def test_capture_pre_snapshot(self, cm, mock_conn, mock_driver):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cm.capture_pre_snapshot(cr, mock_conn)
        assert "running_config" in cr.pre_snapshot
        mock_driver.run_show.assert_called_once_with(["show running-config"], encoding="text")

    def test_capture_post_snapshot(self, cm, mock_conn, mock_driver):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cm.capture_post_snapshot(cr, mock_conn)
        assert "running_config" in cr.post_snapshot

    def test_capture_pre_snapshot_error(self, cm, mock_conn, mock_driver):
        mock_driver.run_show.side_effect = Exception("Connection failed")
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cm.capture_pre_snapshot(cr, mock_conn)
        assert "error" in cr.pre_snapshot
        assert "Connection failed" in cr.pre_snapshot["error"]

    def test_capture_post_snapshot_error(self, cm, mock_conn, mock_driver):
        mock_driver.run_show.side_effect = Exception("Timeout")
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cm.capture_post_snapshot(cr, mock_conn)
        assert "error" in cr.post_snapshot


# ---------------------------------------------------------------------------
# Change validation
# ---------------------------------------------------------------------------


class TestValidateChange:
    def test_valid_when_config_changed(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cr.pre_snapshot = {"running_config": "old config"}
        cr.post_snapshot = {"running_config": "new config with vlan 100"}
        assert cm.validate_change(cr) is True

    def test_invalid_when_config_unchanged(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cr.pre_snapshot = {"running_config": "same config"}
        cr.post_snapshot = {"running_config": "same config"}
        assert cm.validate_change(cr) is False

    def test_invalid_when_pre_has_error(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cr.pre_snapshot = {"error": "Connection failed"}
        cr.post_snapshot = {"running_config": "some config"}
        assert cm.validate_change(cr) is False

    def test_invalid_when_post_has_error(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cr.pre_snapshot = {"running_config": "some config"}
        cr.post_snapshot = {"error": "Timeout"}
        assert cm.validate_change(cr) is False

    def test_invalid_when_empty_snapshots(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        assert cm.validate_change(cr) is False


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_successful_rollback(self, cm, mock_conn, mock_driver):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100", "name SERVERS"])
        cr.status = ChangeStatus.FAILED
        result = cm.rollback(cr, mock_conn)
        assert result["status"] == "success"
        assert result["action"] == "rolled_back"
        assert cr.status == ChangeStatus.ROLLED_BACK
        mock_driver.run_config.assert_called_once_with(["no name SERVERS", "no vlan 100"])

    def test_rollback_no_commands(self, cm, mock_conn):
        cr = cm.create_change_request("net_rename_thing", "spine-01", [])
        cr.rollback_commands = []
        result = cm.rollback(cr, mock_conn)
        assert result["status"] == "error"
        assert "No rollback commands" in result["error"]

    def test_rollback_failure(self, cm, mock_conn, mock_driver):
        mock_driver.run_config.side_effect = Exception("Device unreachable")
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        result = cm.rollback(cr, mock_conn)
        assert result["status"] == "error"
        assert "Rollback failed" in result["error"]
        assert cr.status == ChangeStatus.FAILED

    def test_rollback_moves_to_completed(self, cm, mock_conn, mock_driver):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cm.rollback(cr, mock_conn)
        assert cm._completed.get(cr.id) is cr
        assert cr.id not in cm._pending


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    def test_mark_executing(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cm.mark_executing(cr)
        assert cr.status == ChangeStatus.EXECUTING

    def test_mark_completed(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cm.mark_completed(cr)
        assert cr.status == ChangeStatus.COMPLETED
        assert cm._completed.get(cr.id) is cr
        assert cr.id not in cm._pending

    def test_mark_failed(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cm.mark_failed(cr, "Something went wrong")
        assert cr.status == ChangeStatus.FAILED
        assert cr.error == "Something went wrong"
        assert cm._completed.get(cr.id) is cr


# ---------------------------------------------------------------------------
# Listing and lookup
# ---------------------------------------------------------------------------


class TestListAndLookup:
    def test_list_pending_all(self, cm):
        cm.create_change_request("tool1", "spine-01", ["cmd1"])
        cm.create_change_request("tool2", "leaf-01", ["cmd2"])
        pending = cm.list_pending()
        assert len(pending) == 2

    def test_list_pending_by_device(self, cm):
        cm.create_change_request("tool1", "spine-01", ["cmd1"])
        cm.create_change_request("tool2", "leaf-01", ["cmd2"])
        pending = cm.list_pending(device="spine-01")
        assert len(pending) == 1
        assert pending[0].device == "spine-01"

    def test_list_pending_excludes_completed(self, cm):
        cr = cm.create_change_request("tool1", "spine-01", ["cmd1"])
        cm.mark_completed(cr)
        pending = cm.list_pending()
        assert len(pending) == 0

    def test_get_completed_change(self, cm):
        cr = cm.create_change_request("tool1", "spine-01", ["cmd1"])
        cm.mark_completed(cr)
        found = cm.get_change_request(cr.id)
        assert found is cr

    def test_get_nonexistent(self, cm):
        assert cm.get_change_request("no-such-id") is None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict(self, cm):
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        d = cm.to_dict(cr)
        assert d["id"] == cr.id
        assert d["tool_name"] == "net_create_vlan"
        assert d["device"] == "spine-01"
        assert d["commands"] == ["vlan 100"]
        assert d["risk"] == "medium"
        assert d["status"] == "pending"
        assert "created_at" in d
        assert isinstance(d["rollback_commands"], list)


# ---------------------------------------------------------------------------
# Integration with run_config_command (helpers.py)
# ---------------------------------------------------------------------------


class TestRunConfigCommandIntegration:
    """Test that run_config_command integrates with change management."""

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

    def test_disabled_bypasses_change_mgmt(self, mock_conn_mgr, mock_node):
        """When change management is disabled, run_config_command works as before."""
        from network_mcp.helpers import run_config_command

        mock_node.run_commands.return_value = []
        result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "success"
        assert "change_management" not in result

    def test_enabled_creates_change_request(self, mock_conn_mgr, mock_node):
        """When enabled with require_approval=True, returns pending_approval."""
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings(net_change_mgmt_enabled=True, net_change_require_approval=True))
        helpers_mod._change_manager = cm

        # The tool is "create_vlan" which maps to MEDIUM risk, so approval required
        result = helpers_mod.run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "pending_approval"
        assert "change_id" in result

    def test_enabled_auto_approve_executes(self, mock_conn_mgr, mock_node):
        """When enabled with no approval required, LOW risk auto-approves and executes."""
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(
            _make_settings(
                net_change_mgmt_enabled=True,
                net_change_require_approval=False,
                net_auto_rollback_timeout=0,
            )
        )
        helpers_mod._change_manager = cm

        mock_node.run_commands.return_value = []
        # "update_description" has LOW risk (no matching patterns)
        result = helpers_mod.run_config_command(conn_mgr, "spine-01", ["description test"], "update_description")
        assert result["status"] == "success"
        assert "change_management" in result
        assert result["change_management"]["change_id"]

    def test_maintenance_window_rejection(self, mock_conn_mgr, mock_node):
        """When outside maintenance window, change is rejected."""
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(
            _make_settings(
                net_change_mgmt_enabled=True,
                net_change_require_approval=False,
                net_maintenance_windows="Sun 02:00-03:00",
            )
        )
        helpers_mod._change_manager = cm

        # Mock datetime to be outside the window (Monday 10:00)
        with patch("network_mcp.change_management.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2025, 1, 6, 10, 0)  # Monday
            result = helpers_mod.run_config_command(conn_mgr, "spine-01", ["description test"], "update_description")

        assert result["status"] == "error"
        assert "maintenance window" in result["error"].lower()


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


class TestMCPTools:
    """Test the MCP tool wrappers in tools/common/changes.py."""

    @pytest.fixture(autouse=True)
    def _reset_change_manager(self):
        """Reset the global change manager between tests."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._change_manager
        helpers_mod._change_manager = None
        yield
        helpers_mod._change_manager = original

    def test_list_pending_disabled(self):
        from network_mcp.tools.common.changes import net_list_pending_changes

        result = net_list_pending_changes()
        assert result["status"] == "error"
        assert "not enabled" in result["error"]

    def test_list_pending_enabled(self):
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings())
        helpers_mod._change_manager = cm
        cm.create_change_request("tool1", "spine-01", ["cmd1"])

        from network_mcp.tools.common.changes import net_list_pending_changes

        result = net_list_pending_changes()
        assert result["status"] == "success"
        assert result["count"] == 1

    def test_list_pending_filter_by_host(self):
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings())
        helpers_mod._change_manager = cm
        cm.create_change_request("tool1", "spine-01", ["cmd1"])
        cm.create_change_request("tool2", "leaf-01", ["cmd2"])

        from network_mcp.tools.common.changes import net_list_pending_changes

        result = net_list_pending_changes(host="leaf-01")
        assert result["count"] == 1
        assert result["data"][0]["device"] == "leaf-01"

    def test_get_change_request_disabled(self):
        from network_mcp.tools.common.changes import net_get_change_request

        result = net_get_change_request("spine-01", "fake-id")
        assert result["status"] == "error"

    def test_get_change_request_not_found(self):
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings())
        helpers_mod._change_manager = cm

        from network_mcp.tools.common.changes import net_get_change_request

        result = net_get_change_request("spine-01", "nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_get_change_request_found(self):
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings())
        helpers_mod._change_manager = cm
        cr = cm.create_change_request("tool1", "spine-01", ["cmd1"])

        from network_mcp.tools.common.changes import net_get_change_request

        result = net_get_change_request("spine-01", cr.id)
        assert result["status"] == "success"
        assert result["data"]["id"] == cr.id

    def test_approve_change_disabled(self):
        from network_mcp.tools.common.changes import net_approve_change

        result = net_approve_change("spine-01", "fake-id")
        assert result["status"] == "error"

    def test_approve_change_success(self):
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings())
        helpers_mod._change_manager = cm
        cr = cm.create_change_request("tool1", "spine-01", ["cmd1"])

        from network_mcp.tools.common.changes import net_approve_change

        result = net_approve_change("spine-01", cr.id, approved_by="admin")
        assert result["status"] == "success"
        assert result["action"] == "approved"

    def test_approve_change_not_found(self):
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings())
        helpers_mod._change_manager = cm

        from network_mcp.tools.common.changes import net_approve_change

        result = net_approve_change("spine-01", "fake-id")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_rollback_change_disabled(self):
        from network_mcp.tools.common.changes import net_rollback_change

        result = net_rollback_change("spine-01", "fake-id")
        assert result["status"] == "error"

    def test_rollback_change_success(self, mock_conn, mock_driver):
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings())
        helpers_mod._change_manager = cm
        cr = cm.create_change_request("net_create_vlan", "spine-01", ["vlan 100"])
        cr.status = ChangeStatus.FAILED

        from network_mcp.tools.common.changes import net_rollback_change

        result = net_rollback_change("spine-01", cr.id)
        assert result["status"] == "success"
        assert result["action"] == "rolled_back"

    def test_rollback_change_not_found(self):
        import network_mcp.helpers as helpers_mod

        cm = ChangeManager(_make_settings())
        helpers_mod._change_manager = cm

        from network_mcp.tools.common.changes import net_rollback_change

        result = net_rollback_change("spine-01", "fake-id")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_disabled_manager_properties(self, cm_disabled):
        assert cm_disabled.enabled is False

    def test_multiple_windows(self):
        cm = ChangeManager(_make_settings(net_maintenance_windows="Mon 00:00-04:00,Fri 22:00-23:59"))
        assert len(cm.maintenance_windows) == 2

    def test_empty_commands_rollback(self, cm):
        cr = cm.create_change_request("tool", "dev", [""])
        # Empty string commands are skipped in rollback generation
        assert cr.rollback_commands == []

    def test_change_request_dataclass_defaults(self):
        cr = ChangeRequest(
            id="test-id",
            tool_name="test_tool",
            device="test-device",
            commands=["cmd1"],
            risk=ChangeRisk.LOW,
        )
        assert cr.status == ChangeStatus.PENDING
        assert cr.created_by == ""
        assert cr.approved_by == ""
        assert cr.pre_snapshot == {}
        assert cr.post_snapshot == {}
        assert cr.rollback_commands == []
        assert cr.snow_incident_id == ""
        assert cr.error == ""

    def test_enum_values(self):
        assert ChangeRisk.LOW.value == "low"
        assert ChangeRisk.MEDIUM.value == "medium"
        assert ChangeRisk.HIGH.value == "high"
        assert ChangeRisk.CRITICAL.value == "critical"
        assert ChangeStatus.PENDING.value == "pending"
        assert ChangeStatus.APPROVED.value == "approved"
        assert ChangeStatus.EXECUTING.value == "executing"
        assert ChangeStatus.COMPLETED.value == "completed"
        assert ChangeStatus.FAILED.value == "failed"
        assert ChangeStatus.ROLLED_BACK.value == "rolled_back"
