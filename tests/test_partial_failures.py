"""Tests for partial failure response pattern in multi-device operations."""

import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.executor import MultiDeviceResult, execute_on_devices
from network_mcp.server import conn_mgr


class TestMultiDeviceResultToResponse:
    """Test MultiDeviceResult.to_response() with various success/failure combinations."""

    def test_all_success(self):
        """3 devices succeed, verify status='success' and failed_hosts=[]."""
        result = MultiDeviceResult()
        result.add_success("spine-01", {"version": "4.32.1F"})
        result.add_success("spine-02", {"version": "4.32.1F"})
        result.add_success("leaf-01", {"version": "4.31.0F"})

        response = result.to_response(action="fabric_health")
        assert response["status"] == "success"
        assert response["action"] == "fabric_health"
        assert response["summary"]["total"] == 3
        assert response["summary"]["succeeded"] == 3
        assert response["summary"]["failed"] == 0
        assert response["failed_hosts"] == []
        assert sorted(response["succeeded_hosts"]) == ["leaf-01", "spine-01", "spine-02"]
        assert len(response["results"]) == 3
        assert response["errors"] == {}

    def test_all_failure(self):
        """3 devices fail, verify status='error' and succeeded_hosts=[]."""
        result = MultiDeviceResult()
        result.add_error("spine-01", "Connection refused")
        result.add_error("spine-02", "Timeout after 30s")
        result.add_error("leaf-01", "Authentication failed")

        response = result.to_response(action="fabric_health")
        assert response["status"] == "error"
        assert response["summary"]["total"] == 3
        assert response["summary"]["succeeded"] == 0
        assert response["summary"]["failed"] == 3
        assert response["succeeded_hosts"] == []
        assert sorted(response["failed_hosts"]) == ["leaf-01", "spine-01", "spine-02"]
        assert response["results"] == {}
        assert len(response["errors"]) == 3

    def test_partial_failure(self):
        """2 succeed, 1 fails, verify status='partial' and both lists populated."""
        result = MultiDeviceResult()
        result.add_success("spine-01", {"version": "4.32.1F"})
        result.add_success("spine-02", {"version": "4.32.1F"})
        result.add_error("leaf-01", "Connection refused")

        response = result.to_response(action="fabric_bgp_status")
        assert response["status"] == "partial"
        assert response["action"] == "fabric_bgp_status"
        assert response["summary"]["total"] == 3
        assert response["summary"]["succeeded"] == 2
        assert response["summary"]["failed"] == 1
        assert sorted(response["succeeded_hosts"]) == ["spine-01", "spine-02"]
        assert response["failed_hosts"] == ["leaf-01"]
        assert len(response["results"]) == 2
        assert len(response["errors"]) == 1

    def test_summary_counts_correct(self):
        """Verify summary.total, summary.succeeded, summary.failed are correct."""
        result = MultiDeviceResult()
        for i in range(5):
            result.add_success(f"device-{i}", {"ok": True})
        for i in range(3):
            result.add_error(f"failed-{i}", f"Error {i}")

        response = result.to_response()
        assert response["summary"]["total"] == 8
        assert response["summary"]["succeeded"] == 5
        assert response["summary"]["failed"] == 3

    def test_empty_result(self):
        """Empty result returns status='success' with zero counts."""
        result = MultiDeviceResult()
        response = result.to_response(action="test")
        assert response["status"] == "success"
        assert response["summary"]["total"] == 0
        assert response["summary"]["succeeded"] == 0
        assert response["summary"]["failed"] == 0
        assert response["succeeded_hosts"] == []
        assert response["failed_hosts"] == []

    def test_to_dict_delegates_to_to_response(self):
        """to_dict() returns same structure as to_response()."""
        result = MultiDeviceResult()
        result.add_success("host-a", {"data": 1})
        result.add_error("host-b", "fail")

        dict_output = result.to_dict()
        resp_output = result.to_response()
        assert dict_output["status"] == resp_output["status"]
        assert dict_output["summary"] == resp_output["summary"]

    def test_sorted_host_lists(self):
        """succeeded_hosts and failed_hosts are sorted alphabetically."""
        result = MultiDeviceResult()
        result.add_success("zebra", {})
        result.add_success("alpha", {})
        result.add_success("middle", {})
        result.add_error("zulu", "err")
        result.add_error("bravo", "err")

        response = result.to_response()
        assert response["succeeded_hosts"] == ["alpha", "middle", "zebra"]
        assert response["failed_hosts"] == ["bravo", "zulu"]


class TestExecuteOnDevicesContinuesOnError:
    """Verify execute_on_devices always tries all devices even when some fail."""

    @pytest.fixture
    def mock_driver(self):
        """Create a mock driver for acquire context manager."""
        driver = MagicMock()
        driver.platform = "eos"
        driver.is_connected.return_value = True
        return driver

    def test_continues_on_error(self, mock_driver):
        """All devices attempted even when some fail."""
        call_count = {"value": 0}

        def operation(conn, host):
            call_count["value"] += 1
            if host == "fail-device":
                raise ConnectionError("Connection refused")
            return {"status": "success", "device": host, "data": "ok"}

        hosts = ["good-1", "fail-device", "good-2"]
        result = asyncio.run(execute_on_devices(conn_mgr, hosts, operation))

        assert call_count["value"] == 3
        assert result.success_count == 2
        assert result.error_count == 1
        assert "fail-device" in result.errors
        assert "good-1" in result.successes
        assert "good-2" in result.successes

    def test_timeout_recorded_as_error(self, mock_driver):
        """Timeout for a device is recorded as an error, others continue."""

        async def slow_operation(conn, host):
            if host == "slow-device":
                await asyncio.sleep(10)
            return {"status": "success", "device": host}

        def sync_op(conn, host):
            if host == "slow-device":
                import time

                time.sleep(10)
            return {"status": "success", "device": host}

        hosts = ["fast-1", "slow-device", "fast-2"]
        result = asyncio.run(execute_on_devices(conn_mgr, hosts, sync_op, timeout=0.1))

        assert result.error_count >= 1
        assert "slow-device" in result.errors
        assert "timed out" in result.errors["slow-device"].lower()

    def test_empty_device_list(self):
        """Empty device list returns empty result with success status."""

        def operation(conn, host):
            return {"status": "success"}

        result = asyncio.run(execute_on_devices(conn_mgr, [], operation))

        response = result.to_response(action="test")
        assert response["status"] == "success"
        assert response["summary"]["total"] == 0

    def test_error_status_from_operation(self, mock_driver):
        """Operation returning error dict is recorded in errors."""

        def operation(conn, host):
            if host == "error-device":
                return {"status": "error", "device": host, "error": "Command failed"}
            return {"status": "success", "device": host, "data": "ok"}

        hosts = ["good-1", "error-device", "good-2"]
        result = asyncio.run(execute_on_devices(conn_mgr, hosts, operation))

        assert result.error_count == 1
        assert "error-device" in result.errors
        assert result.success_count == 2


class TestFabricHealthPartialFailure:
    """Test fabric tools return partial failure format."""

    @pytest.fixture
    def mock_driver(self):
        driver = MagicMock()
        driver.platform = "eos"
        driver.is_connected.return_value = True
        return driver

    def test_fabric_health_partial_response_format(self, mock_driver):
        """Verify fabric health returns standardized partial failure format."""
        from network_mcp.tools.fabric import _health_operation

        # Test the operation itself
        mock_driver.run_show.return_value = [
            {
                "hostname": "test-switch",
                "modelName": "vEOS-lab",
                "version": "4.32.1F",
                "uptime": 86400,
                "memTotal": 2048000,
                "memFree": 1024000,
            },
            {"tempSensors": []},
            {"fanTraySlots": []},
            {"powerSupplies": {}},
        ]

        @contextmanager
        def _mock_acquire(host):
            yield mock_driver

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_driver),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
            patch.object(conn_mgr, "get_platform", return_value="eos"),
        ):
            result = _health_operation(conn_mgr, "test-switch")
            assert result["status"] == "success"
            assert result["device"] == "test-switch"


# ===========================================================================
# Mixed Result Multi-Device Operations
# ===========================================================================


class TestMultiDeviceMixedResults:
    """Test multi-device operations with mixed success/timeout/error results."""

    def test_five_devices_mixed_results(self):
        """Mock 5 devices: 3 succeed, 1 times out, 1 has command error."""
        result = MultiDeviceResult()
        result.add_success("device-1", {"version": "4.32.1F"})
        result.add_success("device-2", {"version": "4.32.1F"})
        result.add_success("device-3", {"version": "4.31.0F"})
        result.add_error("device-4", "Operation timed out after 30s")
        result.add_error("device-5", "Command error: invalid command")

        response = result.to_response(action="multi_check")
        assert response["status"] == "partial"
        assert response["summary"]["total"] == 5
        assert response["summary"]["succeeded"] == 3
        assert response["summary"]["failed"] == 2
        assert sorted(response["succeeded_hosts"]) == ["device-1", "device-2", "device-3"]
        assert sorted(response["failed_hosts"]) == ["device-4", "device-5"]

    def test_all_devices_fail(self):
        """When all devices are unreachable, status should be 'error'."""
        result = MultiDeviceResult()
        for i in range(5):
            result.add_error(f"device-{i}", "Connection refused")

        response = result.to_response(action="fabric_check")
        assert response["status"] == "error"
        assert response["summary"]["succeeded"] == 0
        assert response["summary"]["failed"] == 5
        assert response["succeeded_hosts"] == []

    def test_error_details_per_host(self):
        """Each failed host should have its specific error message in errors dict."""
        result = MultiDeviceResult()
        result.add_success("ok-host", {"data": "ok"})
        result.add_error("timeout-host", "Timed out after 30s")
        result.add_error("conn-host", "Connection refused")
        result.add_error("cmd-host", "Invalid command: show bogus")

        response = result.to_response()
        assert len(response["errors"]) == 3
        assert "Timed out" in response["errors"]["timeout-host"]
        assert "Connection refused" in response["errors"]["conn-host"]
        assert "Invalid command" in response["errors"]["cmd-host"]

    def test_execute_continues_past_failures(self):
        """execute_on_devices should attempt all devices even when some fail."""
        attempted = {"count": 0}

        def operation(conn, host):
            attempted["count"] += 1
            if host == "fail-device-2":
                raise ConnectionError("Connection refused")
            if host == "fail-device-4":
                raise TimeoutError("Timed out")
            return {"status": "success", "device": host, "data": "ok"}

        hosts = ["device-1", "fail-device-2", "device-3", "fail-device-4", "device-5"]
        result = asyncio.run(execute_on_devices(conn_mgr, hosts, operation))

        assert attempted["count"] == 5
        assert result.success_count == 3
        assert result.error_count == 2
        assert "fail-device-2" in result.errors
        assert "fail-device-4" in result.errors

    def test_large_fleet_partial_failure(self):
        """Simulate a 50-device fleet with 10% failure rate."""
        result = MultiDeviceResult()
        for i in range(50):
            if i % 10 == 0:  # 5 devices fail
                result.add_error(f"device-{i:03d}", f"Error on device {i}")
            else:
                result.add_success(f"device-{i:03d}", {"ok": True})

        response = result.to_response(action="fleet_check")
        assert response["status"] == "partial"
        assert response["summary"]["succeeded"] == 45
        assert response["summary"]["failed"] == 5


# ===========================================================================
# Extended Partial Failure: Error Details Per Host
# ===========================================================================


class TestPartialFailureErrorDetails:
    """Verify per-host error details are correctly preserved in partial failures."""

    def test_each_failed_host_has_specific_error(self):
        """Each failed host should have its specific error message."""
        result = MultiDeviceResult()
        result.add_success("ok-1", {"data": "yes"})
        result.add_error("timeout-host", "Operation timed out after 30s")
        result.add_error("auth-host", "Authentication failed: invalid credentials")
        result.add_error("cmd-host", "Command error: unsupported command")

        response = result.to_response()
        assert len(response["errors"]) == 3
        assert "timed out" in response["errors"]["timeout-host"]
        assert "Authentication" in response["errors"]["auth-host"]
        assert "unsupported command" in response["errors"]["cmd-host"]

    def test_success_results_preserved_alongside_errors(self):
        """Success results should be intact even with failures present."""
        result = MultiDeviceResult()
        result.add_success("spine-01", {"version": "4.32.1F", "uptime": 86400})
        result.add_success("spine-02", {"version": "4.31.0F", "uptime": 43200})
        result.add_error("leaf-01", "Connection refused")

        response = result.to_response()
        assert response["status"] == "partial"
        assert response["results"]["spine-01"]["version"] == "4.32.1F"
        assert response["results"]["spine-02"]["version"] == "4.31.0F"
        assert "leaf-01" not in response["results"]

    def test_action_field_preserved(self):
        """The action field should be preserved in the response."""
        result = MultiDeviceResult()
        result.add_success("host-1", {})
        result.add_error("host-2", "fail")

        response = result.to_response(action="fabric_health_check")
        assert response["action"] == "fabric_health_check"

    def test_default_action_is_empty_string(self):
        """Default action should be empty string when not provided."""
        result = MultiDeviceResult()
        result.add_success("host-1", {})
        response = result.to_response()
        assert response["action"] == ""

    def test_thread_safe_add_operations(self):
        """MultiDeviceResult should be thread-safe for concurrent add operations."""
        import threading

        result = MultiDeviceResult()
        errors = []

        def _add_success(i):
            try:
                result.add_success(f"host-{i}", {"i": i})
            except Exception as e:
                errors.append(e)

        def _add_error(i):
            try:
                result.add_error(f"fail-{i}", f"Error {i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(50):
            threads.append(threading.Thread(target=_add_success, args=(i,)))
            threads.append(threading.Thread(target=_add_error, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert result.success_count == 50
        assert result.error_count == 50

    def test_mixed_exception_types_in_execute(self):
        """execute_on_devices handles a mix of exception types."""

        def operation(conn, host):
            if host == "timeout-dev":
                raise TimeoutError("timed out")
            if host == "conn-dev":
                raise ConnectionError("refused")
            if host == "runtime-dev":
                raise RuntimeError("unexpected")
            return {"status": "success", "device": host, "data": "ok"}

        hosts = ["good-dev", "timeout-dev", "conn-dev", "runtime-dev", "good-dev-2"]
        result = asyncio.run(execute_on_devices(conn_mgr, hosts, operation))

        assert result.success_count == 2
        assert result.error_count == 3
        assert "timeout-dev" in result.errors
        assert "conn-dev" in result.errors
        assert "runtime-dev" in result.errors

    def test_single_device_success(self):
        """Single device success should return status='success'."""
        result = MultiDeviceResult()
        result.add_success("only-host", {"data": "ok"})

        response = result.to_response()
        assert response["status"] == "success"
        assert response["summary"]["total"] == 1
        assert response["summary"]["succeeded"] == 1
        assert response["summary"]["failed"] == 0

    def test_single_device_failure(self):
        """Single device failure should return status='error'."""
        result = MultiDeviceResult()
        result.add_error("only-host", "Connection refused")

        response = result.to_response()
        assert response["status"] == "error"
        assert response["summary"]["total"] == 1
        assert response["summary"]["succeeded"] == 0
        assert response["summary"]["failed"] == 1


# ===========================================================================
# Multi-Device Partial Failure Integration With Reliability Features
# ===========================================================================


class TestPartialFailureWithReliabilityFeatures:
    """Integration tests combining partial failures with circuit breaker, rate limiting, etc."""

    def test_multi_device_result_preserves_error_types(self):
        """Different error types in MultiDeviceResult should be distinguishable."""
        result = MultiDeviceResult()
        result.add_success("healthy-1", {"version": "4.32.1F"})
        result.add_error("timeout-host", "Operation timed out after 30s")
        result.add_error("auth-host", "Authentication failed: bad credentials")
        result.add_error("unreachable-host", "Connection refused")
        result.add_success("healthy-2", {"version": "4.31.0F"})

        response = result.to_response(action="multi_vendor_check")
        assert response["status"] == "partial"
        assert response["summary"]["total"] == 5
        assert response["summary"]["succeeded"] == 2
        assert response["summary"]["failed"] == 3

        # Verify error messages are distinct and preserved
        assert "timed out" in response["errors"]["timeout-host"]
        assert "Authentication" in response["errors"]["auth-host"]
        assert "refused" in response["errors"]["unreachable-host"]

    def test_large_fleet_with_10_percent_failure_rate(self):
        """Simulate 100-device fleet with 10% failure — verify partial response."""
        result = MultiDeviceResult()
        for i in range(100):
            if i % 10 == 0:  # 10 devices fail
                result.add_error(f"device-{i:03d}", f"Connection error on device-{i:03d}")
            else:
                result.add_success(f"device-{i:03d}", {"ok": True, "device_id": i})

        response = result.to_response(action="fleet_health")
        assert response["status"] == "partial"
        assert response["summary"]["total"] == 100
        assert response["summary"]["succeeded"] == 90
        assert response["summary"]["failed"] == 10
        assert len(response["results"]) == 90
        assert len(response["errors"]) == 10

    def test_execute_on_devices_with_varying_error_types(self):
        """execute_on_devices with mix of ConnectionError, TimeoutError, ValueError."""

        def operation(conn, host):
            if host == "timeout-dev":
                raise TimeoutError("timed out")
            if host == "conn-dev":
                raise ConnectionError("refused")
            if host == "value-dev":
                raise ValueError("bad input")
            if host == "error-status-dev":
                return {"status": "error", "device": host, "error": "command failed"}
            return {"status": "success", "device": host, "data": {"ok": True}}

        hosts = ["good-1", "timeout-dev", "conn-dev", "value-dev", "error-status-dev", "good-2"]
        result = asyncio.run(execute_on_devices(conn_mgr, hosts, operation))

        assert result.success_count == 2
        assert result.error_count == 4

        response = result.to_response(action="mixed_errors")
        assert response["status"] == "partial"
        assert len(response["errors"]) == 4
        assert len(response["results"]) == 2

    def test_all_succeed_large_fleet(self):
        """When all 100 devices succeed, status should be 'success'."""
        result = MultiDeviceResult()
        for i in range(100):
            result.add_success(f"device-{i:03d}", {"healthy": True})

        response = result.to_response(action="full_success")
        assert response["status"] == "success"
        assert response["summary"]["total"] == 100
        assert response["summary"]["succeeded"] == 100
        assert response["summary"]["failed"] == 0
        assert response["failed_hosts"] == []

    def test_partial_failure_percentage_calculation(self):
        """Verify correct percentage calculation for partial failures."""
        result = MultiDeviceResult()
        for i in range(20):
            if i < 15:
                result.add_success(f"dev-{i}", {"data": True})
            else:
                result.add_error(f"dev-{i}", "failed")

        response = result.to_response()
        total = response["summary"]["total"]
        succeeded = response["summary"]["succeeded"]
        failed = response["summary"]["failed"]

        assert total == 20
        assert succeeded == 15
        assert failed == 5
        assert succeeded + failed == total
        success_rate = succeeded / total
        assert 0.74 < success_rate < 0.76  # ~75%

    def test_concurrent_add_operations_thread_safety(self):
        """Concurrent add_success and add_error should not corrupt data."""
        import threading

        result = MultiDeviceResult()
        errors = []

        def _add_successes(start_idx):
            try:
                for i in range(100):
                    result.add_success(f"success-{start_idx}-{i}", {"i": i})
            except Exception as e:
                errors.append(e)

        def _add_errors(start_idx):
            try:
                for i in range(100):
                    result.add_error(f"error-{start_idx}-{i}", f"Error {i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for t_idx in range(5):
            threads.append(threading.Thread(target=_add_successes, args=(t_idx,)))
            threads.append(threading.Thread(target=_add_errors, args=(t_idx,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors
        assert result.success_count == 500
        assert result.error_count == 500

        response = result.to_response()
        assert response["status"] == "partial"
        assert response["summary"]["total"] == 1000
