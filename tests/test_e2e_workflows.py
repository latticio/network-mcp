"""End-to-end workflow tests: VLAN lifecycle, config change approval, and multi-device fabric operations.

These tests exercise multi-step scenarios that span multiple tool calls,
verifying stateful behavior and cross-tool consistency.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.change_management import ChangeManager, ChangeRisk, ChangeStatus
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver_mock(platform="eos", vendor="Arista"):
    """Create a mock driver that tracks stateful VLAN operations."""
    driver = MagicMock()
    driver.platform = platform
    driver.vendor = vendor
    # Internal VLAN state for stateful mocking
    driver._vlans = {
        1: {"vlan_id": 1, "name": "default", "status": "active", "interfaces": []},
    }

    def _get_vlans():
        return [
            {"vlan_id": vid, "name": v["name"], "status": v["status"], "interfaces": v["interfaces"]}
            for vid, v in sorted(driver._vlans.items())
        ]

    def _get_vlan_detail(vlan_id):
        v = driver._vlans.get(vlan_id)
        if v is None:
            return None
        return {"vlan_id": v["vlan_id"], "name": v["name"], "status": v["status"], "interfaces": v["interfaces"]}

    def _run_config(commands):
        """Statefully apply VLAN create/delete/rename commands."""
        i = 0
        while i < len(commands):
            cmd = commands[i]
            if cmd.startswith("vlan ") and not cmd.startswith("no vlan"):
                vlan_id = int(cmd.split()[1])
                if vlan_id not in driver._vlans:
                    driver._vlans[vlan_id] = {
                        "vlan_id": vlan_id,
                        "name": f"VLAN{vlan_id:04d}",
                        "status": "active",
                        "interfaces": [],
                    }
                # Check if next command is a name command
                if i + 1 < len(commands) and commands[i + 1].startswith("name "):
                    name = commands[i + 1].split(" ", 1)[1]
                    driver._vlans[vlan_id]["name"] = name
                    i += 2
                    continue
            elif cmd.startswith("no vlan "):
                vlan_id = int(cmd.split()[2])
                driver._vlans.pop(vlan_id, None)
            i += 1

    driver.get_vlans = MagicMock(side_effect=_get_vlans)
    driver.get_vlan_detail = MagicMock(side_effect=_get_vlan_detail)
    driver.run_config = MagicMock(side_effect=_run_config)
    # Also support run_show for config retrieval
    driver.get_config = MagicMock(
        return_value={"running": "hostname test-switch\n!\nvlan 1\n   name default", "startup": "hostname test-switch"}
    )
    driver.run_show = MagicMock(return_value=[{"output": "hostname test-switch"}])
    return driver


@contextmanager
def _patch_conn_mgr(driver):
    """Patch conn_mgr so both get_driver and acquire return the same driver mock."""

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield driver

    with (
        patch.object(conn_mgr, "get_driver", return_value=driver),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield


@contextmanager
def _disable_read_only():
    """Temporarily disable read-only mode for write tests."""
    with patch("network_mcp.helpers._get_settings") as mock_settings:
        settings = MagicMock()
        settings.net_read_only = False
        settings.net_change_mgmt_enabled = False
        settings.config_retry_enabled = False
        settings.net_distributed_backend = "local"
        settings.net_redis_url = ""
        mock_settings.return_value = settings
        yield


# ===========================================================================
# Task 2: VLAN Lifecycle E2E Test
# ===========================================================================


class TestVlanLifecycleE2E:
    """Full VLAN lifecycle: create -> verify -> rename -> verify -> delete -> verify."""

    def test_full_vlan_lifecycle(self):
        """Test complete VLAN lifecycle through vendor-agnostic tools."""
        from network_mcp.tools.common.vlans import (
            net_create_vlan,
            net_delete_vlan,
            net_get_vlan_detail,
            net_get_vlans,
            net_rename_vlan,
        )

        driver = _make_driver_mock()

        with _patch_conn_mgr(driver), _disable_read_only():
            # Step 1: Verify initial state -- only default VLAN
            result = net_get_vlans("switch-01")
            assert result["status"] == "success"
            assert len(result["data"]) == 1
            assert result["data"][0]["vlan_id"] == 1

            # Step 2: Create VLAN 100
            result = net_create_vlan("switch-01", 100, "SERVERS")
            assert result["status"] == "success"
            assert result["action"] == "create_vlan"
            assert result["data"]["vlan_id"] == 100
            assert result["data"]["name"] == "SERVERS"

            # Step 3: Verify VLAN 100 exists
            result = net_get_vlan_detail("switch-01", 100)
            assert result["status"] == "success"
            assert result["data"]["vlan_id"] == 100
            assert result["data"]["name"] == "SERVERS"

            # Step 4: List all VLANs -- should have 2
            result = net_get_vlans("switch-01")
            assert result["status"] == "success"
            assert len(result["data"]) == 2
            vlan_ids = [v["vlan_id"] for v in result["data"]]
            assert 1 in vlan_ids
            assert 100 in vlan_ids

            # Step 5: Rename VLAN 100
            result = net_rename_vlan("switch-01", 100, "WEB-SERVERS")
            assert result["status"] == "success"
            assert result["action"] == "rename_vlan"

            # Step 6: Verify rename
            result = net_get_vlan_detail("switch-01", 100)
            assert result["status"] == "success"
            assert result["data"]["name"] == "WEB-SERVERS"

            # Step 7: Delete VLAN 100
            result = net_delete_vlan("switch-01", 100)
            assert result["status"] == "success"
            assert result["action"] == "delete_vlan"

            # Step 8: Verify VLAN 100 is gone
            result = net_get_vlan_detail("switch-01", 100)
            assert result["status"] == "error" or result["data"] is None

            # Step 9: List all VLANs -- should be back to 1
            result = net_get_vlans("switch-01")
            assert result["status"] == "success"
            assert len(result["data"]) == 1

    def test_create_duplicate_vlan_updates_name(self):
        """Creating a VLAN that already exists should update its name."""
        from network_mcp.tools.common.vlans import net_create_vlan, net_get_vlan_detail

        driver = _make_driver_mock()

        with _patch_conn_mgr(driver), _disable_read_only():
            # Create VLAN 200
            result = net_create_vlan("switch-01", 200, "FIRST-NAME")
            assert result["status"] == "success"

            # Re-create with different name
            result = net_create_vlan("switch-01", 200, "SECOND-NAME")
            assert result["status"] == "success"

            # Verify the name was updated
            result = net_get_vlan_detail("switch-01", 200)
            assert result["status"] == "success"
            assert result["data"]["name"] == "SECOND-NAME"

    def test_delete_nonexistent_vlan_succeeds(self):
        """Deleting a VLAN that does not exist should still succeed (idempotent)."""
        from network_mcp.tools.common.vlans import net_delete_vlan

        driver = _make_driver_mock()

        with _patch_conn_mgr(driver), _disable_read_only():
            result = net_delete_vlan("switch-01", 999)
            # run_config is called and succeeds even when VLAN doesn't exist
            assert result["status"] == "success"

    def test_lifecycle_blocked_in_read_only_mode(self):
        """Write operations should be blocked in read-only mode."""
        from network_mcp.tools.common.vlans import net_create_vlan, net_delete_vlan, net_rename_vlan

        driver = _make_driver_mock()

        with _patch_conn_mgr(driver):
            # Default mode is read-only
            with patch("network_mcp.helpers._get_settings") as mock_settings:
                settings = MagicMock()
                settings.net_read_only = True
                mock_settings.return_value = settings

                for fn, args in [
                    (net_create_vlan, ("switch-01", 100, "TEST")),
                    (net_rename_vlan, ("switch-01", 100, "NEW")),
                    (net_delete_vlan, ("switch-01", 100)),
                ]:
                    result = fn(*args)
                    assert result["status"] == "error"
                    assert "read" in result["error"].lower() or "write" in result["error"].lower()


# ===========================================================================
# Task 3: Config Change Approval Flow E2E Test
# ===========================================================================


class TestConfigChangeApprovalE2E:
    """Push config -> list pending -> approve -> verify running-config."""

    def _make_change_manager(self, require_approval=True):
        """Create a ChangeManager with test settings."""
        settings = MagicMock()
        settings.net_change_mgmt_enabled = True
        settings.net_change_require_approval = require_approval
        settings.net_auto_rollback_timeout = 0
        settings.net_maintenance_windows = "*"
        return ChangeManager(settings)

    def test_full_change_approval_flow(self):
        """Test the complete change management workflow: create -> approve -> execute."""
        cm = self._make_change_manager(require_approval=True)

        # Step 1: Create a change request
        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
            user="operator",
        )
        assert cr.status == ChangeStatus.PENDING
        assert cr.risk == ChangeRisk.MEDIUM  # 'create' in tool name
        assert cr.device == "switch-01"

        # Step 2: List pending changes
        pending = cm.list_pending()
        assert len(pending) == 1
        assert pending[0].id == cr.id

        # Step 3: Approve the change
        approved = cm.approve_change(cr.id, approved_by="admin")
        assert approved is not None
        assert approved.status == ChangeStatus.APPROVED
        assert approved.approved_by == "admin"

        # Step 4: Mark as executing and completed
        cm.mark_executing(cr)
        assert cr.status == ChangeStatus.EXECUTING

        cm.mark_completed(cr)
        assert cr.status == ChangeStatus.COMPLETED

        # Step 5: Pending list should be empty now
        pending = cm.list_pending()
        assert len(pending) == 0

        # Step 6: Can still retrieve completed change
        retrieved = cm.get_change_request(cr.id)
        assert retrieved is not None
        assert retrieved.status == ChangeStatus.COMPLETED

    def test_change_approval_via_tools(self):
        """Test approval flow through the MCP tool functions."""
        from network_mcp.tools.common.changes import (
            net_approve_change,
            net_list_pending_changes,
        )

        cm = self._make_change_manager(require_approval=True)

        # Create a change request directly on the manager
        cr = cm.create_change_request(
            tool_name="net_push_config",
            device="switch-01",
            commands=["hostname new-name"],
            user="operator",
        )

        with patch("network_mcp.tools.common.changes.get_change_manager", return_value=cm):
            # List pending changes
            result = net_list_pending_changes()
            assert result["status"] == "success"
            assert result["count"] == 1
            assert result["data"][0]["id"] == cr.id

            # Approve the change
            result = net_approve_change("switch-01", cr.id, approved_by="admin")
            assert result["status"] == "success"
            assert result["action"] == "approved"
            assert result["approved_by"] == "admin"

            # List pending again -- should still be listed (approved is still pending)
            result = net_list_pending_changes()
            assert result["status"] == "success"
            assert result["count"] == 1
            assert result["data"][0]["status"] == "approved"

    def test_change_request_not_found(self):
        """Approving a non-existent change should return error."""
        from network_mcp.tools.common.changes import net_approve_change

        cm = self._make_change_manager()

        with patch("network_mcp.tools.common.changes.get_change_manager", return_value=cm):
            result = net_approve_change("switch-01", "non-existent-id")
            assert result["status"] == "error"
            assert "not found" in result["error"]

    def test_low_risk_auto_approved(self):
        """Low-risk changes should be auto-approved when require_approval=False."""
        cm = self._make_change_manager(require_approval=False)

        cr = cm.create_change_request(
            tool_name="net_rename_vlan",
            device="switch-01",
            commands=["vlan 100", "name NEW-NAME"],
            user="operator",
        )
        # rename is LOW risk (doesn't match create/delete/push patterns)
        assert cr.status == ChangeStatus.APPROVED
        assert cr.approved_by == "auto"

    def test_change_rollback_with_snapshots(self):
        """Test rollback through the change manager with pre/post snapshots."""
        cm = self._make_change_manager(require_approval=False)
        driver = _make_driver_mock()

        cr = cm.create_change_request(
            tool_name="net_create_vlan",
            device="switch-01",
            commands=["vlan 100", "name SERVERS"],
        )

        with _patch_conn_mgr(driver):
            # Capture pre-snapshot
            cm.capture_pre_snapshot(cr, conn_mgr)
            assert "running_config" in cr.pre_snapshot or "error" not in cr.pre_snapshot

            # Mark completed
            cm.mark_completed(cr)

            # Rollback
            result = cm.rollback(cr, conn_mgr)
            assert result["status"] == "success"
            assert result["action"] == "rolled_back"
            assert cr.status == ChangeStatus.ROLLED_BACK

    def test_change_disabled_returns_error(self):
        """When change management is disabled, all tools should return error."""
        from network_mcp.tools.common.changes import (
            net_approve_change,
            net_list_pending_changes,
            net_rollback_change,
        )

        settings = MagicMock()
        settings.net_change_mgmt_enabled = False
        settings.net_change_require_approval = False
        settings.net_auto_rollback_timeout = 0
        settings.net_maintenance_windows = "*"
        cm = ChangeManager(settings)

        with patch("network_mcp.tools.common.changes.get_change_manager", return_value=cm):
            result = net_list_pending_changes()
            assert result["status"] == "error"
            assert "not enabled" in result["error"]

            result = net_approve_change("switch-01", "some-id")
            assert result["status"] == "error"
            assert "not enabled" in result["error"]

            result = net_rollback_change("switch-01", "some-id")
            assert result["status"] == "error"
            assert "not enabled" in result["error"]


# ===========================================================================
# Task 4: Multi-Device Fabric Operation E2E Test
# ===========================================================================


class TestMultiDeviceFabricE2E:
    """Health check across 3 devices with 1 failure -> overall 'degraded' (partial)."""

    @pytest.mark.asyncio
    async def test_fabric_health_three_devices_one_failure(self):
        """Fabric health with 3 devices where 1 fails should report partial status."""
        from network_mcp.executor import execute_on_devices

        # Set up 3 mock drivers
        def health_op(conn_mgr_, host):
            if host == "leaf-03":
                return {
                    "status": "error",
                    "device": host,
                    "error": "Connection refused",
                }
            return {
                "status": "success",
                "device": host,
                "data": {
                    "hostname": host,
                    "model": "vEOS-lab",
                    "eos_version": "4.32.1F",
                    "uptime_seconds": 86400,
                    "memory_used_percent": 50.0,
                },
            }

        hosts = ["spine-01", "leaf-01", "leaf-03"]
        result = await execute_on_devices(conn_mgr, hosts, health_op)

        # Should have partial status
        output = result.to_dict()
        assert output["status"] == "partial"
        assert output["summary"]["total"] == 3
        assert output["summary"]["succeeded"] == 2
        assert output["summary"]["failed"] == 1

        # Check which succeeded and which failed
        assert "spine-01" in result.successes
        assert "leaf-01" in result.successes
        assert "leaf-03" in result.errors
        assert "Connection refused" in result.errors["leaf-03"]

    @pytest.mark.asyncio
    async def test_fabric_health_all_succeed(self):
        """Fabric health with all devices healthy should report success."""
        from network_mcp.executor import execute_on_devices

        def health_op(conn_mgr_, host):
            return {
                "status": "success",
                "device": host,
                "data": {
                    "hostname": host,
                    "model": "vEOS-lab",
                    "eos_version": "4.32.1F",
                    "uptime_seconds": 86400,
                    "memory_used_percent": 50.0,
                },
            }

        hosts = ["spine-01", "leaf-01", "leaf-02"]
        result = await execute_on_devices(conn_mgr, hosts, health_op)

        output = result.to_dict()
        assert output["status"] == "success"
        assert output["summary"]["total"] == 3
        assert output["summary"]["succeeded"] == 3
        assert output["summary"]["failed"] == 0

    @pytest.mark.asyncio
    async def test_fabric_health_all_fail(self):
        """Fabric health with all devices failing should report error."""
        from network_mcp.executor import execute_on_devices

        def health_op(conn_mgr_, host):
            return {"status": "error", "device": host, "error": "Network unreachable"}

        hosts = ["spine-01", "leaf-01", "leaf-02"]
        result = await execute_on_devices(conn_mgr, hosts, health_op)

        output = result.to_dict()
        assert output["status"] == "error"
        assert output["summary"]["total"] == 3
        assert output["summary"]["succeeded"] == 0
        assert output["summary"]["failed"] == 3

    @pytest.mark.asyncio
    async def test_fabric_health_with_exception(self):
        """Fabric health where a device raises an exception should capture error."""
        from network_mcp.executor import execute_on_devices

        def health_op(conn_mgr_, host):
            if host == "leaf-02":
                raise TimeoutError("Connection timed out")
            return {
                "status": "success",
                "device": host,
                "data": {"hostname": host},
            }

        hosts = ["spine-01", "leaf-02"]
        result = await execute_on_devices(conn_mgr, hosts, health_op)

        output = result.to_dict()
        assert output["status"] == "partial"
        assert "spine-01" in result.successes
        assert "leaf-02" in result.errors

    @pytest.mark.asyncio
    async def test_multi_device_result_counts(self):
        """Verify MultiDeviceResult properties track counts correctly."""
        from network_mcp.executor import MultiDeviceResult

        result = MultiDeviceResult()
        assert result.total == 0
        assert result.success_count == 0
        assert result.error_count == 0

        result.add_success("host-1", {"data": "ok"})
        assert result.total == 1
        assert result.success_count == 1

        result.add_error("host-2", "Connection refused")
        assert result.total == 2
        assert result.error_count == 1

        result.add_success("host-3", {"data": "ok"})
        assert result.total == 3
        assert result.success_count == 2
        assert result.error_count == 1

    @pytest.mark.asyncio
    async def test_fabric_bgp_status_mixed(self):
        """Fabric BGP status across devices with mixed results."""
        from network_mcp.tools.fabric import _bgp_operation

        # Create mock node that responds to run_commands
        mock_node = MagicMock()
        mock_node.run_commands.return_value = [
            {
                "vrfs": {
                    "default": {
                        "routerId": "10.0.0.1",
                        "asn": "65000",
                        "peers": {
                            "10.0.0.2": {
                                "peerState": "Established",
                                "prefixReceived": 150,
                                "upDownTime": 1234567,
                                "asn": "65001",
                            },
                            "10.0.0.3": {
                                "peerState": "Idle",
                                "prefixReceived": 0,
                                "upDownTime": 100,
                                "asn": "65002",
                            },
                        },
                    }
                }
            }
        ]
        mock_node.run_show = mock_node.run_commands
        mock_node.run_config = mock_node.config

        @contextmanager
        def _mock_acquire(host):  # noqa: ARG001
            yield mock_node

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_node),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            result = _bgp_operation(conn_mgr, "spine-01")
            assert result["status"] == "success"
            assert result["data"]["established"] == 1
            assert result["data"]["down"] == 1
