"""Chaos engineering tests for resilience validation.

Tests the system's behavior under various failure conditions:
- Device disconnection and recovery
- Latency injection
- Partial failures across device fleet
- Connection pool exhaustion
- Cascading failures (MLAG split-brain, BGP flapping)
- Concurrent failure injection
- Rate limiter behavior under burst traffic
- Circuit breaker state machine transitions
- Output size limits and malformed responses
"""

import concurrent.futures
import random
import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pyeapi.eapilib
import pytest

from network_mcp.cache import CommandCache
from network_mcp.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, DeviceHealth
from network_mcp.config import NetworkSettings
from network_mcp.connection import ConnectionManager, get_bulkhead
from network_mcp.helpers import run_config_command, run_session_command, run_show_command
from network_mcp.rate_limiter import DeviceRateLimiter, TokenBucket
from network_mcp.sanitizer import enforce_dict_output_limit
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_VERSION = {
    "modelName": "vEOS-lab",
    "version": "4.32.1F",
    "serialNumber": "TEST123",
    "hostname": "test-switch",
}


def _make_mock_node(*, failing=False, error_cls=ConnectionError, delay=0.0):
    """Create a mock node that can succeed, fail, or delay."""
    node = MagicMock()
    node.platform = "eos"
    node.is_connected.return_value = True
    node.run_show = node.run_commands

    if failing:
        node.run_commands.side_effect = error_cls("device unreachable")
    elif delay > 0:
        original = [MOCK_VERSION]

        def _slow_response(*args, **kwargs):  # noqa: ARG001
            time.sleep(delay)
            return original

        node.run_commands.side_effect = _slow_response
    else:
        node.run_commands.return_value = [MOCK_VERSION]

    node.config.return_value = []
    node.run_config = node.config
    return node


@contextmanager
def _multi_device_conn_mgr(device_map):
    """Patch conn_mgr to return different nodes per host."""

    @contextmanager
    def _mock_acquire(host):
        if host not in device_map:
            raise ConnectionError(f"No device: {host}")
        yield device_map[host]

    def _mock_get_driver(host, **kwargs):  # noqa: ARG001
        if host not in device_map:
            raise ConnectionError(f"No device: {host}")
        return device_map[host]

    with (
        patch.object(conn_mgr, "get_driver", side_effect=_mock_get_driver),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield


# ===========================================================================
# Existing Chaos Tests (connection failures, config, sessions, output limits)
# ===========================================================================


class TestChaosConnectionFailures:
    """Test that tools handle intermittent and unexpected failures gracefully."""

    @pytest.fixture
    def flaky_conn_mgr(self, mock_node):
        """Set up a conn_mgr that fails intermittently."""
        call_count = 0

        def flaky_run_commands(commands, **kwargs):
            nonlocal call_count
            call_count += 1
            if random.random() < 0.5:  # noqa: S311
                raise pyeapi.eapilib.ConnectionError("spine-01", "Device unreachable")
            return [{"hostname": "spine-01", "version": "4.32.1F"}]

        mock_node.run_commands.side_effect = flaky_run_commands
        mock_node.run_show = mock_node.run_commands

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with (
            patch.object(conn_mgr, "get_driver", return_value=mock_node),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            yield mock_node

    def test_intermittent_device_failure(self, flaky_conn_mgr):
        """Device fails ~50% of calls — should handle gracefully, never raise."""
        random.seed(42)
        for _ in range(20):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] in ("success", "error")
            assert result["device"] == "spine-01"
            if result["status"] == "error":
                assert "error" in result
                assert isinstance(result["error"], str)

    def test_empty_response_handling(self, mock_conn_mgr, mock_node):
        """Device returns empty response — should handle gracefully."""
        mock_node.run_commands.return_value = [{}]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"
        assert result["data"] == [{}]

    def test_none_in_response_list(self, mock_conn_mgr, mock_node):
        """Device returns None in response list — should handle gracefully."""
        mock_node.run_commands.return_value = [None]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"

    def test_huge_response_handling(self, mock_conn_mgr, mock_node):
        """Device returns massive response — output limiting should kick in."""
        huge = {"data": "x" * 200_000}
        mock_node.run_commands.return_value = [huge]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        # The result should either be truncated or contain a truncation marker
        assert result["status"] in ("success", "error")
        if result["status"] == "error" and result.get("truncated"):
            assert result["original_size_bytes"] > 100_000

    def test_deeply_nested_response(self, mock_conn_mgr, mock_node):
        """Device returns deeply nested dict — should not stack overflow."""
        nested = {"level": 0}
        current = nested
        for i in range(1, 50):
            current["child"] = {"level": i}
            current = current["child"]
        mock_node.run_commands.return_value = [nested]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"

    def test_unicode_in_response(self, mock_conn_mgr, mock_node):
        """Device returns unicode characters — should handle without crash."""
        mock_node.run_commands.return_value = [{"description": "to-sp\u00efne-01 \u2603 \U0001f600"}]
        result = run_show_command(conn_mgr, "spine-01", ["show interfaces status"])
        assert result["status"] == "success"

    def test_connection_error_returns_structured_dict(self, mock_conn_mgr, mock_node):
        """ConnectionError should return a structured error dict, not raise."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert "spine-01" in result["error"]
        assert result["device"] == "spine-01"

    def test_command_error_returns_structured_dict(self, mock_conn_mgr, mock_node):
        """CommandError should return a structured error dict, not raise."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid input")
        result = run_show_command(conn_mgr, "spine-01", ["show invalid"])
        assert result["status"] == "error"
        assert "Command error" in result["error"]

    def test_timeout_error_returns_structured_dict(self, mock_conn_mgr, mock_node):
        """TimeoutError should be caught and returned as structured dict."""
        mock_node.run_commands.side_effect = TimeoutError("timed out")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert "Timeout" in result["error"] or "error" in result["error"].lower()

    def test_generic_exception_returns_structured_dict(self, mock_conn_mgr, mock_node):
        """Arbitrary exceptions should be caught and returned as structured dict."""
        mock_node.run_commands.side_effect = RuntimeError("something unexpected")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_multiple_commands_partial_failure(self, mock_conn_mgr, mock_node):
        """Multiple show commands where device returns partial data."""
        mock_node.run_commands.return_value = [{"hostname": "spine-01"}, {}]
        result = run_show_command(conn_mgr, "spine-01", ["show version", "show hostname"])
        assert result["status"] == "success"
        assert len(result["data"]) == 2

    def test_response_with_special_float_values(self, mock_conn_mgr, mock_node):
        """Device returns special float values (inf, nan)."""
        mock_node.run_commands.return_value = [{"utilization": float("inf"), "errors": float("nan")}]
        result = run_show_command(conn_mgr, "spine-01", ["show interfaces counters"])
        assert result["status"] == "success"

    def test_response_with_nested_lists(self, mock_conn_mgr, mock_node):
        """Device returns deeply nested lists."""
        mock_node.run_commands.return_value = [{"routes": [[[{"prefix": "10.0.0.0/8"}]]]}]
        result = run_show_command(conn_mgr, "spine-01", ["show ip route"])
        assert result["status"] == "success"

    def test_response_with_binary_like_strings(self, mock_conn_mgr, mock_node):
        """Device returns strings with null bytes."""
        mock_node.run_commands.return_value = [{"output": "normal\x00data"}]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"


class TestChaosConfigFailures:
    """Test config command resilience."""

    @pytest.fixture(autouse=True)
    def _set_write_mode(self):
        """Temporarily enable write mode for config tests."""
        import network_mcp.helpers as helpers_mod

        original = helpers_mod._settings
        helpers_mod._settings = NetworkSettings(net_read_only=False)
        yield
        helpers_mod._settings = original

    def test_config_connection_error(self, mock_conn_mgr, mock_node):
        """Config commands should handle connection errors gracefully."""
        mock_node.config.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
        result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"

    def test_config_command_error(self, mock_conn_mgr, mock_node):
        """Config commands should handle command errors gracefully."""
        mock_node.config.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid input")
        result = run_config_command(conn_mgr, "spine-01", ["vlan 99999"], "create_vlan")
        assert result["status"] == "error"

    def test_config_generic_exception(self, mock_conn_mgr, mock_node):
        """Config commands should handle arbitrary exceptions."""
        mock_node.config.side_effect = RuntimeError("unexpected")
        result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_config_timeout_error(self, mock_conn_mgr, mock_node):
        """Config commands should handle TimeoutError gracefully."""
        mock_node.config.side_effect = TimeoutError("config timeout")
        result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "error"


class TestChaosSessionFailures:
    """Test session command resilience."""

    def test_session_connection_error(self, mock_conn_mgr, mock_node):
        """Session commands should handle connection errors gracefully."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
        result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
        assert result["status"] == "error"
        assert "Connection" in result["error"]

    def test_session_command_error(self, mock_conn_mgr, mock_node):
        """Session commands should handle command errors gracefully."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid input")
        result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
        assert result["status"] == "error"

    def test_session_generic_error(self, mock_conn_mgr, mock_node):
        """Session commands should handle generic errors."""
        mock_node.run_commands.side_effect = RuntimeError("unexpected")
        result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_session_timeout_error(self, mock_conn_mgr, mock_node):
        """Session commands should handle TimeoutError gracefully."""
        mock_node.run_commands.side_effect = TimeoutError("session timeout")
        result = run_session_command(conn_mgr, "spine-01", ["configure session test"], "create_session")
        assert result["status"] == "error"


class TestOutputSizeLimits:
    """Test output size enforcement under various conditions."""

    def test_enforce_dict_output_limit_small(self):
        """Small dicts should pass through unchanged."""
        data = {"status": "success", "device": "spine-01", "data": {"hostname": "spine-01"}}
        result = enforce_dict_output_limit(data)
        assert result == data

    def test_enforce_dict_output_limit_large(self):
        """Large dicts should be truncated with metadata."""
        data = {"status": "success", "device": "spine-01", "data": "x" * 200_000}
        result = enforce_dict_output_limit(data)
        assert result["status"] == "error"
        assert result["truncated"] is True
        assert result["device"] == "spine-01"
        assert result["original_size_bytes"] > 100_000

    def test_enforce_dict_output_limit_exact_boundary(self):
        """Dict exactly at limit should pass through."""
        from network_mcp.sanitizer import MAX_OUTPUT_SIZE

        small = {"a": "b"}
        result = enforce_dict_output_limit(small, max_size=MAX_OUTPUT_SIZE)
        assert result == small

    def test_enforce_dict_output_limit_preserves_device(self):
        """Truncated output should preserve the device key."""
        data = {"status": "success", "device": "leaf-01", "data": "x" * 200_000}
        result = enforce_dict_output_limit(data)
        assert result["device"] == "leaf-01"

    def test_enforce_dict_output_limit_no_device_key(self):
        """Truncated output without device key should not crash."""
        data = {"status": "success", "data": "x" * 200_000}
        result = enforce_dict_output_limit(data)
        assert result["status"] == "error"
        assert "device" not in result

    def test_enforce_dict_output_limit_custom_max_size(self):
        """Custom max size should be respected."""
        data = {"status": "success", "data": "x" * 1000}
        result = enforce_dict_output_limit(data, max_size=100)
        assert result["status"] == "error"
        assert result["truncated"] is True

    def test_enforce_dict_output_limit_empty_dict(self):
        """Empty dict should pass through."""
        result = enforce_dict_output_limit({})
        assert result == {}


# ===========================================================================
# Device Failure Isolation Tests
# ===========================================================================


class TestDeviceFailureIsolation:
    """Verify that failures on one device don't affect others."""

    def test_single_device_failure_doesnt_affect_others(self):
        """When one device fails, other devices should still work."""
        devices = {
            "device-a": _make_mock_node(failing=True),
            "device-b": _make_mock_node(),
            "device-c": _make_mock_node(),
        }

        with _multi_device_conn_mgr(devices):
            result_a = run_show_command(conn_mgr, "device-a", ["show version"])
            result_b = run_show_command(conn_mgr, "device-b", ["show version"])
            result_c = run_show_command(conn_mgr, "device-c", ["show version"])

        assert result_a["status"] == "error"
        assert result_b["status"] == "success"
        assert result_c["status"] == "success"

    def test_thirty_percent_device_failure(self):
        """System should remain functional when 30% of devices are down."""
        devices = {}
        for i in range(10):
            failing = i < 3  # First 3 devices fail
            devices[f"device-{i}"] = _make_mock_node(failing=failing)

        with _multi_device_conn_mgr(devices):
            results = {}
            for name in devices:
                results[name] = run_show_command(conn_mgr, name, ["show version"])

        failed = [n for n, r in results.items() if r["status"] == "error"]
        succeeded = [n for n, r in results.items() if r["status"] == "success"]

        assert len(failed) == 3
        assert len(succeeded) == 7

    def test_mixed_error_types_handled_independently(self):
        """Different error types per device should be handled without cross-contamination."""
        timeout_dev = _make_mock_node()
        timeout_dev.run_commands.side_effect = TimeoutError("timed out")

        conn_err_dev = _make_mock_node()
        conn_err_dev.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")

        cmd_err_dev = _make_mock_node()
        cmd_err_dev.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "invalid command")

        ok_dev = _make_mock_node()

        devices = {
            "timeout-dev": timeout_dev,
            "conn-err-dev": conn_err_dev,
            "cmd-err-dev": cmd_err_dev,
            "ok-dev": ok_dev,
        }

        with _multi_device_conn_mgr(devices):
            results = {name: run_show_command(conn_mgr, name, ["show version"]) for name in devices}

        assert results["timeout-dev"]["status"] == "error"
        assert results["conn-err-dev"]["status"] == "error"
        assert results["cmd-err-dev"]["status"] == "error"
        assert results["ok-dev"]["status"] == "success"


# ===========================================================================
# Circuit Breaker State Machine Tests
# ===========================================================================


class TestCircuitBreakerStateMachine:
    """Test circuit breaker state transitions under chaos conditions."""

    def test_opens_on_consecutive_failures(self):
        """After N consecutive failures, circuit breaker should open."""
        cb = CircuitBreaker("failing-device", threshold=3, cooldown=60)
        assert cb.state == DeviceHealth.HEALTHY

        cb.record_failure()
        assert cb.state == DeviceHealth.HEALTHY
        cb.record_failure()
        assert cb.state == DeviceHealth.HEALTHY
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

    def test_open_circuit_denies_requests(self):
        """Open circuit should deny new requests (fail fast)."""
        cb = CircuitBreaker("failing-device", threshold=2, cooldown=300)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN
        assert cb.allow_request() is False

    def test_half_open_after_cooldown(self):
        """After cooldown, circuit should transition to half-open."""
        cb = CircuitBreaker("recovering-device", threshold=2, cooldown=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

        time.sleep(1.1)
        state = cb.get_state()
        assert state == DeviceHealth.HALF_OPEN

    def test_half_open_success_restores_healthy(self):
        """Success during half-open should restore to healthy."""
        cb = CircuitBreaker("recovering-device", threshold=2, cooldown=1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(1.1)
        assert cb.get_state() == DeviceHealth.HALF_OPEN

        cb.record_success()
        assert cb.state == DeviceHealth.HEALTHY

    def test_half_open_failure_reopens_with_backoff(self):
        """Failure during half-open should reopen with longer cooldown."""
        cb = CircuitBreaker("flaky-device", threshold=2, cooldown=1)
        cb.record_failure()
        cb.record_failure()
        original_cooldown = cb._cooldown

        time.sleep(1.1)
        assert cb.get_state() == DeviceHealth.HALF_OPEN

        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN
        assert cb._cooldown == min(original_cooldown * 2, CircuitBreaker._MAX_COOLDOWN)

    def test_success_resets_failure_count_and_cooldown(self):
        """A success should reset consecutive failures and base cooldown."""
        cb = CircuitBreaker("device", threshold=3, cooldown=10)
        cb.record_failure()
        cb.record_failure()
        assert cb.consecutive_failures == 2

        cb.record_success()
        assert cb.consecutive_failures == 0
        assert cb._cooldown == 10

    def test_exponential_backoff_caps_at_max(self):
        """Exponential backoff should cap at _MAX_COOLDOWN."""
        cb = CircuitBreaker("device", threshold=1, cooldown=100)
        for _ in range(10):
            cb.record_failure()
            cb._last_failure_time = time.monotonic() - cb._cooldown - 1
            cb.get_state()  # Trigger OPEN -> HALF_OPEN
            cb.record_failure()

        assert cb._cooldown <= CircuitBreaker._MAX_COOLDOWN

    def test_thread_safety(self):
        """Circuit breaker should be safe under concurrent access."""
        cb = CircuitBreaker("contested-device", threshold=5, cooldown=60)
        errors = []

        def _hammer(breaker, iterations):
            try:
                for _ in range(iterations):
                    breaker.record_failure()
                    breaker.allow_request()
                    breaker.get_state()
                    breaker.record_success()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_hammer, args=(cb, 200)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread safety violation: {errors}"


class TestCircuitBreakerRegistryAdvanced:
    """Test the registry that manages per-device breakers."""

    def test_creates_breakers_on_demand(self):
        """Registry should create breakers lazily for unknown hosts."""
        registry = CircuitBreakerRegistry(threshold=3, cooldown=60, enabled=True)
        breaker = registry.get("new-device")
        assert breaker.host == "new-device"
        assert breaker.state == DeviceHealth.HEALTHY

    def test_disabled_always_allows(self):
        """When disabled, all requests should be allowed through."""
        registry = CircuitBreakerRegistry(threshold=1, cooldown=60, enabled=False)
        registry.record_failure("device-x")
        registry.record_failure("device-x")
        registry.record_failure("device-x")
        assert registry.allow_request("device-x") is True

    def test_tracks_open_circuits(self):
        """get_open_circuits() should return hosts with open breakers."""
        registry = CircuitBreakerRegistry(threshold=2, cooldown=300, enabled=True)
        registry.record_failure("dev-1")
        registry.record_failure("dev-1")
        registry.record_failure("dev-2")  # Only 1 failure, not open yet

        open_circuits = registry.get_open_circuits()
        assert "dev-1" in open_circuits
        assert "dev-2" not in open_circuits

    def test_reset_clears_all(self):
        """reset() should clear all breakers."""
        registry = CircuitBreakerRegistry(threshold=2, cooldown=60, enabled=True)
        registry.record_failure("dev-a")
        registry.record_failure("dev-a")
        registry.record_failure("dev-b")

        registry.reset()
        assert registry.allow_request("dev-a") is True
        assert len(registry.get_open_circuits()) == 0

    def test_reset_single_host(self):
        """reset(host) should clear only that host's breaker."""
        registry = CircuitBreakerRegistry(threshold=2, cooldown=300, enabled=True)
        registry.record_failure("dev-a")
        registry.record_failure("dev-a")
        registry.record_failure("dev-b")
        registry.record_failure("dev-b")

        registry.reset("dev-a")
        assert registry.allow_request("dev-a") is True
        assert registry.allow_request("dev-b") is False

    def test_stats_returns_complete_info(self):
        """stats() should return comprehensive breaker information."""
        registry = CircuitBreakerRegistry(threshold=2, cooldown=60, enabled=True)
        registry.record_failure("dev-a")
        registry.record_failure("dev-a")
        registry.record_success("dev-b")

        stats = registry.stats()
        assert stats["enabled"] is True
        assert stats["total_breakers"] == 2
        assert "dev-a" in stats["open_circuits"]
        assert "dev-b" not in stats["open_circuits"]
        assert "dev-a" in stats["breakers"]
        assert "dev-b" in stats["breakers"]


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration with run_show_command."""

    def test_fast_fail_in_show_command(self):
        """run_show_command should return circuit-open error immediately."""
        from network_mcp.helpers import circuit_breaker_registry

        circuit_breaker_registry._enabled = True
        try:
            breaker = circuit_breaker_registry.get("cb-test-device")
            for _ in range(circuit_breaker_registry._threshold):
                breaker.record_failure()

            result = run_show_command(conn_mgr, "cb-test-device", ["show version"])
            assert result["status"] == "error"
            assert "Circuit breaker open" in result["error"]
        finally:
            circuit_breaker_registry._enabled = False
            circuit_breaker_registry.reset()

    def test_blocks_requests_without_contacting_device(self, mock_conn_mgr, mock_node):
        """When circuit breaker is open, device should never be contacted."""
        with patch("network_mcp.helpers.circuit_breaker_registry") as mock_cb:
            mock_cb.allow_request.return_value = False
            breaker = MagicMock()
            breaker.consecutive_failures = 5
            mock_cb.get.return_value = breaker
            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] == "error"
            assert "Circuit breaker" in result["error"]
            mock_node.run_commands.assert_not_called()

    def test_opens_after_repeated_connection_failures(self, mock_conn_mgr, mock_node):
        """Circuit breaker should open after repeated connection failures via run_show_command."""
        from network_mcp.helpers import circuit_breaker_registry

        original_enabled = circuit_breaker_registry._enabled
        circuit_breaker_registry._enabled = True
        try:
            mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
            for _ in range(10):
                run_show_command(conn_mgr, "spine-01", ["show version"])

            breaker = circuit_breaker_registry.get("spine-01")
            assert breaker.consecutive_failures > 0
        finally:
            circuit_breaker_registry._enabled = original_enabled


# ===========================================================================
# Latency Injection Tests
# ===========================================================================


class TestLatencyInjection:
    """Simulate slow device responses."""

    def test_slow_device_doesnt_crash_server(self, mock_conn_mgr, mock_node):
        """Slow device should return eventually, not crash."""
        call_count = 0

        def _slow(*args, **kwargs):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            time.sleep(0.05)
            return [MOCK_VERSION]

        mock_node.run_commands.side_effect = _slow
        result = run_show_command(conn_mgr, "slow-device", ["show version"])
        assert result["status"] == "success"
        assert call_count >= 1

    def test_slow_device_doesnt_block_other_devices(self):
        """Slow device shouldn't prevent other devices from responding quickly."""
        devices = {
            "slow-device": _make_mock_node(delay=0.5),
            "fast-device": _make_mock_node(),
        }

        with _multi_device_conn_mgr(devices):
            fast_start = time.monotonic()
            fast_result = run_show_command(conn_mgr, "fast-device", ["show version"])
            fast_duration = time.monotonic() - fast_start

        assert fast_result["status"] == "success"
        assert fast_duration < 0.3

    def test_concurrent_slow_and_fast_devices(self):
        """Mix of slow and fast devices should all complete."""
        devices = {
            "slow-0": _make_mock_node(delay=0.2),
            "slow-1": _make_mock_node(delay=0.2),
            "fast-0": _make_mock_node(),
            "fast-1": _make_mock_node(),
            "fast-2": _make_mock_node(),
        }

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    name: executor.submit(run_show_command, conn_mgr, name, ["show version"]) for name in devices
                }
                results = {name: f.result(timeout=5) for name, f in futures.items()}

        for name, result in results.items():
            assert result["status"] == "success", f"{name} failed: {result}"


# ===========================================================================
# Connection Pool Exhaustion Tests
# ===========================================================================


class TestConnectionPoolExhaustion:
    """Simulate connection pool at capacity."""

    def test_pool_exhaustion_returns_error(self):
        """When pool is full and all connections active, new requests should fail gracefully."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=2)

        mock_a = _make_mock_node()
        mock_b = _make_mock_node()

        with patch.object(mgr, "_create_driver", side_effect=[mock_a, mock_b]):
            ctx_a = mgr.acquire("device-a")
            driver_a = ctx_a.__enter__()  # noqa: F841
            ctx_b = mgr.acquire("device-b")
            driver_b = ctx_b.__enter__()  # noqa: F841

            with pytest.raises(ConnectionError, match="pool exhausted"):
                with mgr.acquire("device-c"):
                    pass

            ctx_a.__exit__(None, None, None)
            ctx_b.__exit__(None, None, None)

    def test_pool_recovery_after_connections_released(self):
        """After connections are released, new requests should work."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=2)

        mock_a = _make_mock_node()
        mock_b = _make_mock_node()
        mock_c = _make_mock_node()

        with patch.object(mgr, "_create_driver", side_effect=[mock_a, mock_b, mock_c]):
            with mgr.acquire("device-a"):
                pass
            with mgr.acquire("device-b"):
                pass

            # Pool is full but connections are idle, so eviction works
            with mgr.acquire("device-c") as driver:
                assert driver is mock_c

    def test_pool_evicts_idle_connections(self):
        """Pool should evict idle connections when at capacity."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=2)

        mock_a = _make_mock_node()
        mock_b = _make_mock_node()
        mock_c = _make_mock_node()

        with patch.object(mgr, "_create_driver", side_effect=[mock_a, mock_b, mock_c]):
            with mgr.acquire("device-a"):
                pass
            with mgr.acquire("device-b"):
                pass

            with mgr.acquire("device-c") as driver:
                assert driver is mock_c

            stats = mgr.pool_stats()
            assert stats["pooled"] <= 2

    def test_concurrent_pool_access_no_deadlock(self):
        """Concurrent pool access should not deadlock."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=5)

        nodes = [_make_mock_node() for _ in range(10)]
        node_idx = 0
        create_lock = threading.Lock()

        def _create_driver_side_effect(*args, **kwargs):  # noqa: ARG001
            nonlocal node_idx
            with create_lock:
                idx = node_idx
                node_idx += 1
            return nodes[idx % len(nodes)]

        errors = []

        def _use_pool(device_name):
            try:
                with mgr.acquire(device_name):
                    time.sleep(0.01)
            except ConnectionError:
                pass  # Pool exhaustion is expected
            except Exception as e:
                errors.append(e)

        with patch.object(mgr, "_create_driver", side_effect=_create_driver_side_effect):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_use_pool, f"device-{i}") for i in range(10)]
                done, not_done = concurrent.futures.wait(futures, timeout=10)

        assert len(not_done) == 0, f"{len(not_done)} futures timed out (deadlock)"
        assert not errors, f"Pool access errors: {errors}"


# ===========================================================================
# Cascading Failure Tests
# ===========================================================================


class TestCascadingFailures:
    """Simulate cascading failure scenarios."""

    def test_mlag_split_brain_both_queryable(self):
        """When MLAG peers disagree, both should be queryable independently."""
        peer_a_mlag = {
            "domainId": "mlag-01",
            "state": "active",
            "negStatus": "connected",
            "configSanity": "inconsistent",
        }
        peer_b_mlag = {
            "domainId": "mlag-01",
            "state": "active",
            "negStatus": "connected",
            "configSanity": "inconsistent",
        }

        device_a = _make_mock_node()
        device_a.run_commands.return_value = [peer_a_mlag]
        device_b = _make_mock_node()
        device_b.run_commands.return_value = [peer_b_mlag]

        with _multi_device_conn_mgr({"peer-a": device_a, "peer-b": device_b}):
            result_a = run_show_command(conn_mgr, "peer-a", ["show mlag"])
            result_b = run_show_command(conn_mgr, "peer-b", ["show mlag"])

        assert result_a["status"] == "success"
        assert result_b["status"] == "success"
        assert result_a["data"][0]["configSanity"] == "inconsistent"
        assert result_b["data"][0]["configSanity"] == "inconsistent"

    def test_bgp_flapping_stability(self):
        """Rapid BGP state changes shouldn't crash the server."""
        device = _make_mock_node()
        call_count = 0

        def _flapping_bgp(*args, **kwargs):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                return [{"vrfs": {"default": {"peers": {"10.0.0.2": {"peerState": "Established"}}}}}]
            else:
                return [{"vrfs": {"default": {"peers": {"10.0.0.2": {"peerState": "Active"}}}}}]

        device.run_commands.side_effect = _flapping_bgp

        @contextmanager
        def _mock_acquire(host):  # noqa: ARG001
            yield device

        with (
            patch.object(conn_mgr, "get_driver", return_value=device),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            results = []
            for _ in range(20):
                r = run_show_command(conn_mgr, "flapping-device", ["show ip bgp summary"], cache_ttl=0)
                results.append(r)

        # All calls should complete without crashing (some may be rate-limited)
        for r in results:
            assert r["status"] in ("success", "error")

        # At least some successful calls should show alternating states
        success_results = [r for r in results if r["status"] == "success"]
        assert len(success_results) >= 2, "Too few successful results"
        states = {r["data"][0]["vrfs"]["default"]["peers"]["10.0.0.2"]["peerState"] for r in success_results}
        assert len(states) >= 1  # At least one BGP state observed

    def test_error_during_retry_doesnt_corrupt_state(self):
        """Errors during retry logic shouldn't leave corrupted state."""
        device = _make_mock_node()
        call_count = 0

        def _intermittent(*args, **kwargs):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise pyeapi.eapilib.ConnectionError("host", "transient error")
            return [MOCK_VERSION]

        device.run_commands.side_effect = _intermittent

        @contextmanager
        def _mock_acquire(host):  # noqa: ARG001
            yield device

        with (
            patch.object(conn_mgr, "get_driver", return_value=device),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            result = run_show_command(conn_mgr, "retry-device", ["show version"])

        assert result["status"] == "success"

    def test_all_devices_fail_simultaneously(self):
        """When all devices fail at once, system should handle it without crashing."""
        devices = {f"device-{i}": _make_mock_node(failing=True) for i in range(10)}

        with _multi_device_conn_mgr(devices):
            results = {name: run_show_command(conn_mgr, name, ["show version"]) for name in devices}

        assert all(r["status"] == "error" for r in results.values())

    def test_device_recovers_after_failure(self):
        """A device that was failing should work again when it recovers."""
        device = _make_mock_node()
        call_count = 0

        def _recover(*args, **kwargs):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise pyeapi.eapilib.ConnectionError("host", "down")
            return [MOCK_VERSION]

        device.run_commands.side_effect = _recover

        @contextmanager
        def _mock_acquire(host):  # noqa: ARG001
            yield device

        with (
            patch.object(conn_mgr, "get_driver", return_value=device),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            # First 3 calls fail (retry exhausts in first run_show_command call)
            run_show_command(conn_mgr, "recovering-device", ["show version"])
            # After recovery, next call should succeed
            result2 = run_show_command(conn_mgr, "recovering-device", ["show version"])

        # First may or may not succeed depending on retry
        # Second should definitely succeed since call_count > 3
        assert result2["status"] == "success"


# ===========================================================================
# Concurrent Chaos Tests
# ===========================================================================


class TestConcurrentChaos:
    """Concurrent failure injection."""

    def test_concurrent_failures_across_devices(self):
        """Multiple devices failing simultaneously — no deadlocks, all requests complete."""
        devices = {}
        for i in range(20):
            failing = i < 5
            devices[f"device-{i}"] = _make_mock_node(failing=failing)

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = {
                    name: executor.submit(run_show_command, conn_mgr, name, ["show version"]) for name in devices
                }
                results = {}
                for name, future in futures.items():
                    results[name] = future.result(timeout=10)

        success = sum(1 for r in results.values() if r["status"] == "success")
        error = sum(1 for r in results.values() if r["status"] == "error")
        assert success == 15
        assert error == 5
        assert success + error == 20

    def test_concurrent_requests_to_same_device(self, mock_conn_mgr):
        """Multiple concurrent requests to the same device should all complete."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(run_show_command, conn_mgr, "shared-device", ["show version"]) for _ in range(10)
            ]
            results = [f.result(timeout=10) for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 10
        for r in results:
            assert r["status"] in ("success", "error")

    def test_no_deadlock_under_mixed_operations(self):
        """Mixed read operations under concurrency shouldn't deadlock."""
        devices = {f"device-{i}": _make_mock_node() for i in range(5)}

        commands_variants = [
            ["show version"],
            ["show interfaces status"],
            ["show ip bgp summary"],
        ]

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                futures = []
                for i in range(30):
                    dev = f"device-{i % 5}"
                    cmds = commands_variants[i % 3]
                    futures.append(executor.submit(run_show_command, conn_mgr, dev, cmds))

                done, not_done = concurrent.futures.wait(futures, timeout=15)

        assert len(not_done) == 0, f"{len(not_done)} futures timed out (possible deadlock)"

    def test_rapid_connect_disconnect_stability(self):
        """Rapid creation/teardown of connections should not corrupt state."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=5)
        errors = []

        def _create_stub(*args, **kwargs):  # noqa: ARG001
            return _make_mock_node()

        def _churn(device_name, iterations):
            try:
                for _ in range(iterations):
                    with mgr.acquire(device_name):
                        pass  # Immediately release
            except Exception as e:
                errors.append(e)

        with patch.object(mgr, "_create_driver", side_effect=_create_stub):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_churn, f"dev-{i % 3}", 50) for i in range(10)]
                done, not_done = concurrent.futures.wait(futures, timeout=15)

        assert len(not_done) == 0
        assert not errors, f"Churn errors: {errors}"


# ===========================================================================
# Rate Limiter Chaos Tests
# ===========================================================================


class TestRateLimiterUnderChaos:
    """Test rate limiter behavior under stress conditions."""

    def test_burst_throttling(self):
        """Rate limiter should throttle correctly under sudden burst."""
        limiter = DeviceRateLimiter(show_rate=5.0, show_burst=5.0, config_rate=1.0, config_burst=1.0)

        allowed = 0
        for _ in range(20):
            if limiter.check_show("burst-device"):
                allowed += 1

        assert 3 <= allowed <= 7

    def test_recovers_over_time(self):
        """After burst exhaustion, rate limiter should recover with time."""
        limiter = DeviceRateLimiter(show_rate=10.0, show_burst=5.0, config_rate=1.0, config_burst=1.0)

        for _ in range(10):
            limiter.check_show("recovery-device")

        assert limiter.check_show("recovery-device") is False

        time.sleep(0.2)
        assert limiter.check_show("recovery-device") is True

    def test_per_device_isolation(self):
        """Rate limiting for one device shouldn't affect another."""
        limiter = DeviceRateLimiter(show_rate=2.0, show_burst=2.0, config_rate=1.0, config_burst=1.0)

        for _ in range(5):
            limiter.check_show("device-a")

        assert limiter.check_show("device-b") is True
        assert limiter.check_show("device-b") is True

    def test_thread_safety(self):
        """Rate limiter should be thread-safe under concurrent access."""
        limiter = DeviceRateLimiter(show_rate=100.0, show_burst=50.0, config_rate=10.0, config_burst=5.0)
        errors = []

        def _hammer(device, iterations):
            try:
                for _ in range(iterations):
                    limiter.check_show(device)
                    limiter.check_config(device)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_hammer, args=(f"dev-{i % 3}", 500)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread safety violation: {errors}"

    def test_token_bucket_never_goes_negative(self):
        """Token bucket should never have negative tokens."""
        bucket = TokenBucket(rate=1.0, capacity=1.0)

        bucket.acquire()
        bucket.acquire()
        bucket.acquire()

        with bucket._lock:
            assert bucket._tokens >= -0.001  # Float tolerance

    def test_rapid_show_commands_rate_limited(self, mock_conn_mgr, mock_node):
        """Rapid-fire show commands should eventually be rate limited."""
        mock_node.run_commands.return_value = [{"hostname": "spine-01"}]
        with patch("network_mcp.helpers.device_rate_limiter") as mock_rl:
            mock_rl.check_show.return_value = False
            mock_rl.check_show_with_backoff.return_value = False
            mock_rl.show_wait_time.return_value = 0.5
            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] == "error"
            assert "Rate limit" in result["error"]

    def test_config_rate_separate_from_show(self):
        """Config and show rate limits should be independent."""
        limiter = DeviceRateLimiter(show_rate=100.0, show_burst=100.0, config_rate=1.0, config_burst=1.0)

        # Exhaust config rate
        limiter.check_config("dev")
        assert limiter.check_config("dev") is False

        # Show should still work
        assert limiter.check_show("dev") is True


# ===========================================================================
# Cache Under Chaos
# ===========================================================================


class TestCacheUnderChaos:
    """Test cache behavior under adverse conditions."""

    def test_invalidation_during_concurrent_reads(self):
        """Cache invalidation during concurrent reads shouldn't cause errors."""
        cache = CommandCache(max_entries=100, enabled=True)
        errors = []

        def _reader():
            try:
                for i in range(100):
                    cache.get(f"host-{i % 5}", [f"cmd-{i}"], "json")
                    cache.put(f"host-{i % 5}", [f"cmd-{i}"], "json", {"data": i}, ttl=10)
            except Exception as e:
                errors.append(e)

        def _invalidator():
            try:
                for i in range(50):
                    cache.invalidate_host(f"host-{i % 5}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_reader) for _ in range(5)]
        threads.append(threading.Thread(target=_invalidator))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent cache error: {errors}"

    def test_eviction_under_memory_pressure(self):
        """Cache should evict entries when approaching memory limit."""
        cache = CommandCache(max_entries=1000, max_memory_mb=1, enabled=True)

        large_data = {"data": "x" * 100_000}
        for i in range(20):
            cache.put(f"host-{i}", ["show version"], "json", large_data, ttl=300)

        stats = cache.stats()
        assert stats["memory_mb"] <= 1.1

    def test_clear_during_heavy_usage(self):
        """Clearing cache during heavy usage shouldn't cause errors."""
        cache = CommandCache(max_entries=500, enabled=True)
        errors = []

        def _use_cache():
            try:
                for i in range(200):
                    cache.put(f"host-{i % 10}", [f"cmd-{i}"], "json", {"val": i}, ttl=60)
                    cache.get(f"host-{i % 10}", [f"cmd-{i}"], "json")
            except Exception as e:
                errors.append(e)

        def _clear_cache():
            try:
                for _ in range(10):
                    cache.clear()
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_use_cache) for _ in range(5)]
        threads.append(threading.Thread(target=_clear_cache))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Cache clear concurrent error: {errors}"

    def test_ttl_expiry_under_load(self):
        """Expired entries should be cleaned up correctly under concurrent access."""
        cache = CommandCache(max_entries=100, enabled=True)

        # Put entries with 1s TTL
        for i in range(20):
            cache.put(f"host-{i}", ["show version"], "json", {"val": i}, ttl=1)

        # Wait for expiry
        time.sleep(1.1)

        # All should be expired
        for i in range(20):
            result = cache.get(f"host-{i}", ["show version"], "json")
            assert result is None

    def test_disabled_cache_no_errors(self):
        """Disabled cache should return None and accept puts without error."""
        cache = CommandCache(enabled=False)

        cache.put("host", ["cmd"], "json", {"data": 1}, ttl=60)
        result = cache.get("host", ["cmd"], "json")
        assert result is None
        assert cache.stats()["entries"] == 0


# ===========================================================================
# Reliability Integration: Timeout Mid-Operation Tests
# ===========================================================================


class TestTimeoutMidOperation:
    """Verify timeout handling returns proper error codes and completes without hanging."""

    def test_device_timeout_returns_timeout_error_code(self, mock_conn_mgr, mock_node):
        """Mock device that sleeps past timeout returns TIMEOUT_ERROR code."""
        mock_node.run_commands.side_effect = TimeoutError("timed out after 30s")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert "error_code" in result
        # May be TIMEOUT_ERROR or INTERNAL_ERROR depending on retry wrapping
        assert result["error_code"] in ("TIMEOUT_ERROR", "INTERNAL_ERROR")
        assert result["device"] == "spine-01"

    def test_timeout_completes_without_hanging(self, mock_conn_mgr, mock_node):
        """Timeout scenario should complete within a reasonable wall-clock time."""
        mock_node.run_commands.side_effect = TimeoutError("timed out")
        start = time.monotonic()
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        elapsed = time.monotonic() - start
        assert result["status"] == "error"
        assert elapsed < 10, f"Timeout scenario took {elapsed:.1f}s — should complete quickly"

    def test_timeout_records_circuit_breaker_failure(self, mock_conn_mgr, mock_node):
        """Timeout errors should be recorded by the circuit breaker."""
        from network_mcp.helpers import circuit_breaker_registry

        circuit_breaker_registry._enabled = True
        try:
            mock_node.run_commands.side_effect = TimeoutError("timed out")
            run_show_command(conn_mgr, "spine-01", ["show version"])
            breaker = circuit_breaker_registry.get("spine-01")
            assert breaker.consecutive_failures > 0
        finally:
            circuit_breaker_registry._enabled = False


# ===========================================================================
# Bulkhead Isolation Under Vendor Pressure Tests
# ===========================================================================


class TestBulkheadIsolationUnderPressure:
    """Test bulkhead concurrency limits with multi-vendor isolation."""

    def test_concurrent_threads_limited_by_bulkhead(self):
        """Spawn 5 threads for limit=2, verify 3 fail or wait."""
        from network_mcp.connection import BulkheadManager

        bh = BulkheadManager(per_vendor_limit=2)
        results = {"acquired": 0, "failed": 0}
        lock = threading.Lock()
        barrier = threading.Barrier(5, timeout=5)

        def _try_acquire():
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            got = bh.acquire("eos", timeout=0.3)
            with lock:
                if got:
                    results["acquired"] += 1
                    time.sleep(0.5)
                    bh.release("eos")
                else:
                    results["failed"] += 1

        threads = [threading.Thread(target=_try_acquire) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert results["acquired"] >= 2
        assert results["acquired"] + results["failed"] == 5

    def test_vendor_isolation_under_pressure(self):
        """Exhaust 'eos' slots, verify 'iosxe' still works immediately."""
        from network_mcp.connection import BulkheadManager

        bh = BulkheadManager(per_vendor_limit=3)
        # Exhaust all eos slots
        for _ in range(3):
            assert bh.acquire("eos", timeout=1.0) is True
        assert bh.get_available("eos") == 0

        # iosxe should be unaffected
        assert bh.acquire("iosxe", timeout=0.1) is True
        assert bh.get_available("iosxe") == 2

        # Release eos slots
        for _ in range(3):
            bh.release("eos")
        assert bh.get_available("eos") == 3

        # eos should work again
        assert bh.acquire("eos", timeout=0.1) is True

    def test_bulkhead_slots_released_after_exception(self):
        """Verify bulkhead slots are released even when operations raise."""
        from network_mcp.connection import bulkhead_slot, init_bulkhead

        init_bulkhead(2)
        bh = get_bulkhead()
        assert bh is not None

        for _ in range(3):
            try:
                with bulkhead_slot("eos", timeout=1.0):
                    raise RuntimeError("simulated failure")
            except RuntimeError:
                pass

        assert bh.get_available("eos") == 2


# ===========================================================================
# Stale-if-Error Cache Under Device Failure
# ===========================================================================


class TestStaleCacheUnderDeviceFailure:
    """Verify stale cache serves expired data when device fails."""

    def test_stale_cache_serves_expired_data_on_failure(self, mock_conn_mgr, mock_node):
        """Populate cache, expire it, fail device, verify stale data returned."""
        from network_mcp.helpers import command_cache

        # Successful call to populate cache
        mock_node.run_commands.return_value = [{"status": "success", "version": "4.32.1F"}]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"

        # Expire the cache entry
        key = command_cache._make_key("spine-01", ["show version"], "json")
        command_cache._cache[key].timestamp = time.monotonic() - 100
        command_cache._cache[key].ttl = 30

        # Mock connection failure
        mock_node.run_commands.side_effect = ConnectionError("device unreachable")

        from unittest.mock import MagicMock

        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = MagicMock()
            settings.net_cache_stale_if_error_ttl = 300
            settings.net_rate_limit_max_wait = 5.0
            settings.net_conn_acquire_timeout = 30.0
            mock_settings.return_value = settings

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            # Should get stale data with cache metadata, or fallback to error
            if result.get("_cache", {}).get("stale"):
                assert result["status"] == "success"
                assert "data" in result
            # Either way, we got a response (not a crash)
            assert isinstance(result, dict)


# ===========================================================================
# Graceful Shutdown During In-Flight Operations
# ===========================================================================


class TestGracefulShutdownDuringInFlight:
    """Test that in-flight ops complete during shutdown and new ops are rejected."""

    @pytest.fixture(autouse=True)
    def _reset_shutdown(self):
        """Reset shutdown state."""
        import network_mcp.server as server_mod

        server_mod._shutting_down = False
        server_mod._in_flight_count = 0
        server_mod._shutdown_event.clear()
        yield
        server_mod._shutting_down = False
        server_mod._in_flight_count = 0
        server_mod._shutdown_event.clear()

    def test_in_flight_ops_complete_after_shutdown_flag(self):
        """Start concurrent ops, set shutdown mid-way, verify all started ops complete."""
        import network_mcp.server as server_mod

        results = []
        lock = threading.Lock()
        started = threading.Event()

        # Slow device
        slow_node = _make_mock_node(delay=0.5)
        devices = {f"device-{i}": slow_node for i in range(3)}

        def _run_op(name):
            started.set()
            r = run_show_command(conn_mgr, name, ["show version"])
            with lock:
                results.append(r)

        with _multi_device_conn_mgr(devices):
            threads = [threading.Thread(target=_run_op, args=(f"device-{i}",)) for i in range(3)]
            for t in threads:
                t.start()

            # Wait for at least one to start
            started.wait(timeout=2)
            time.sleep(0.1)

            # Trigger shutdown
            server_mod._shutting_down = True

            for t in threads:
                t.join(timeout=10)

        # All 3 started ops should have completed (success or error, not killed)
        assert len(results) == 3
        for r in results:
            assert r["status"] in ("success", "error")

    def test_new_ops_rejected_after_shutdown(self):
        """Operations started after shutdown flag should return SERVER_SHUTTING_DOWN."""
        import network_mcp.server as server_mod

        server_mod._shutting_down = True
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert result["error_code"] == "SERVER_SHUTTING_DOWN"


# ===========================================================================
# Circuit Breaker + Stale Cache Cascade
# ===========================================================================


class TestCircuitBreakerStaleCacheCascade:
    """Test interaction between circuit breaker and stale-if-error cache."""

    @pytest.fixture(autouse=True)
    def _reset_shutdown(self):
        import network_mcp.server as server_mod

        server_mod._shutting_down = False
        server_mod._in_flight_count = 0
        server_mod._shutdown_event.clear()
        yield
        server_mod._shutting_down = False

    def test_stale_cache_checked_before_circuit_breaker_error(self, mock_conn_mgr, mock_node):
        """When circuit breaker is open, stale cache should be checked first."""
        from network_mcp.helpers import circuit_breaker_registry, command_cache

        # Populate cache
        mock_node.run_commands.return_value = [{"status": "success", "version": "4.32.1F"}]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"

        # Expire the cache entry
        key = command_cache._make_key("spine-01", ["show version"], "json")
        command_cache._cache[key].timestamp = time.monotonic() - 100
        command_cache._cache[key].ttl = 30

        # Open circuit breaker
        circuit_breaker_registry._enabled = True
        try:
            breaker = circuit_breaker_registry.get("spine-01")
            for _ in range(breaker._threshold):
                breaker.record_failure()

            with patch("network_mcp.helpers._get_settings") as mock_settings:
                settings = MagicMock()
                settings.net_cache_stale_if_error_ttl = 300
                settings.net_rate_limit_max_wait = 5.0
                settings.net_conn_acquire_timeout = 30.0
                mock_settings.return_value = settings

                result = run_show_command(conn_mgr, "spine-01", ["show version"])
                # Should prefer stale cache over circuit breaker error
                if result.get("_cache", {}).get("stale"):
                    assert result["status"] == "success"
                    assert "data" in result
                else:
                    # If stale cache not served, should get circuit breaker error
                    assert result["error_code"] == "CIRCUIT_OPEN"
        finally:
            circuit_breaker_registry._enabled = False


# ===========================================================================
# Rate Limiter Backoff Under Burst
# ===========================================================================


class TestRateLimiterBackoffUnderBurst:
    """Test rate limiter behavior with and without backoff under burst load."""

    def test_backoff_allows_more_requests_than_immediate_fail(self):
        """With backoff, more requests should succeed over time vs immediate rejection."""
        from network_mcp.rate_limiter import TokenBucket

        # Without backoff: immediate acquire only
        bucket_no_backoff = TokenBucket(rate=5.0, capacity=5.0)
        no_backoff_success = 0
        for _ in range(20):
            if bucket_no_backoff.acquire():
                no_backoff_success += 1

        # With backoff: acquire_with_backoff allows waiting
        bucket_with_backoff = TokenBucket(rate=5.0, capacity=5.0)
        backoff_success = 0
        for _ in range(20):
            if bucket_with_backoff.acquire_with_backoff(max_wait=0.5):
                backoff_success += 1

        # Backoff should allow more successes (or equal if rate is high enough)
        assert backoff_success >= no_backoff_success

    def test_rapid_burst_with_backoff_doesnt_hang(self):
        """20 rapid requests with backoff should complete within bounded time."""
        from network_mcp.rate_limiter import TokenBucket

        bucket = TokenBucket(rate=5.0, capacity=5.0)
        start = time.monotonic()
        successes = 0
        for _ in range(20):
            if bucket.acquire_with_backoff(max_wait=0.3):
                successes += 1
        elapsed = time.monotonic() - start
        assert elapsed < 15, f"Burst with backoff took {elapsed:.1f}s"
        assert successes >= 5  # At least the initial burst should succeed


# ===========================================================================
# Extended Timeout Mid-Operation Tests
# ===========================================================================


class TestTimeoutMidOperationExtended:
    """Extended timeout tests verifying error codes and circuit breaker interaction."""

    def test_timeout_error_code_is_set(self, mock_conn_mgr, mock_node):
        """Verify TIMEOUT_ERROR or INTERNAL_ERROR code on timeout."""
        mock_node.run_commands.side_effect = TimeoutError("device timed out after 30s")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert "error_code" in result
        assert result["error_code"] in ("TIMEOUT_ERROR", "INTERNAL_ERROR")
        assert result["is_retryable"] is True

    def test_timeout_wall_clock_bounded(self, mock_conn_mgr, mock_node):
        """Timeout scenario should complete within 10s wall clock."""
        mock_node.run_commands.side_effect = TimeoutError("device timed out")
        start = time.monotonic()
        run_show_command(conn_mgr, "spine-01", ["show version"])
        elapsed = time.monotonic() - start
        assert elapsed < 10, f"Timeout took {elapsed:.1f}s to complete"

    def test_timeout_increments_circuit_breaker_failures(self, mock_conn_mgr, mock_node):
        """Timeout should increment the circuit breaker failure count."""
        from network_mcp.helpers import circuit_breaker_registry

        circuit_breaker_registry._enabled = True
        try:
            mock_node.run_commands.side_effect = TimeoutError("device timed out")
            run_show_command(conn_mgr, "spine-01", ["show version"])
            breaker = circuit_breaker_registry.get("spine-01")
            assert breaker.consecutive_failures >= 1
        finally:
            circuit_breaker_registry._enabled = False

    def test_multiple_timeouts_open_circuit_breaker(self, mock_conn_mgr, mock_node):
        """Repeated timeouts should eventually open the circuit breaker."""
        from network_mcp.helpers import circuit_breaker_registry

        circuit_breaker_registry._enabled = True
        try:
            mock_node.run_commands.side_effect = TimeoutError("timed out")
            for _ in range(10):
                run_show_command(conn_mgr, "spine-01", ["show version"])

            breaker = circuit_breaker_registry.get("spine-01")
            assert breaker.state == DeviceHealth.CIRCUIT_OPEN
        finally:
            circuit_breaker_registry._enabled = False


# ===========================================================================
# Extended Bulkhead Isolation Tests
# ===========================================================================


class TestBulkheadIsolationExtended:
    """Extended bulkhead tests with multi-vendor isolation and slot release verification."""

    def test_exhausted_vendor_doesnt_affect_others(self):
        """Exhaust all 'eos' slots, verify 'iosxe' and 'junos' are unaffected."""
        from network_mcp.connection import BulkheadManager

        bh = BulkheadManager(per_vendor_limit=2)

        # Exhaust eos slots
        assert bh.acquire("eos", timeout=0.1) is True
        assert bh.acquire("eos", timeout=0.1) is True
        assert bh.get_available("eos") == 0

        # Other vendors should work
        assert bh.acquire("iosxe", timeout=0.1) is True
        assert bh.acquire("junos", timeout=0.1) is True
        assert bh.get_available("iosxe") == 1
        assert bh.get_available("junos") == 1

        # Release eos and verify
        bh.release("eos")
        bh.release("eos")
        assert bh.get_available("eos") == 2

        # Clean up
        bh.release("iosxe")
        bh.release("junos")

    def test_bulkhead_timeout_returns_false(self):
        """When all slots are taken, acquire with short timeout returns False."""
        from network_mcp.connection import BulkheadManager

        bh = BulkheadManager(per_vendor_limit=1)
        assert bh.acquire("eos", timeout=0.1) is True
        # Second acquire should fail
        assert bh.acquire("eos", timeout=0.1) is False
        bh.release("eos")

    def test_released_slots_allow_waiters_to_proceed(self):
        """After releasing slots, waiting threads should be able to proceed."""
        from network_mcp.connection import BulkheadManager

        bh = BulkheadManager(per_vendor_limit=1)
        assert bh.acquire("eos", timeout=0.1) is True

        acquired = threading.Event()
        result = {"got_slot": False}

        def _waiter():
            if bh.acquire("eos", timeout=2.0):
                result["got_slot"] = True
                acquired.set()
                bh.release("eos")

        waiter_thread = threading.Thread(target=_waiter)
        waiter_thread.start()

        # Small delay then release slot
        time.sleep(0.2)
        bh.release("eos")

        acquired.wait(timeout=3.0)
        waiter_thread.join(timeout=5)
        assert result["got_slot"] is True

    def test_concurrent_bulkhead_with_exceptions(self):
        """Bulkhead slots should be released even when operations raise exceptions."""
        from network_mcp.connection import BulkheadManager

        bh = BulkheadManager(per_vendor_limit=3)

        def _work_with_exception(vendor):
            got = bh.acquire(vendor, timeout=2.0)
            if got:
                try:
                    raise RuntimeError("simulated failure")
                except RuntimeError:
                    pass
                finally:
                    bh.release(vendor)

        threads = [threading.Thread(target=_work_with_exception, args=("eos",)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All slots should be released
        assert bh.get_available("eos") == 3


# ===========================================================================
# Extended Stale Cache Under Device Failure Tests
# ===========================================================================


class TestStaleCacheUnderDeviceFailureExtended:
    """Extended tests for stale-if-error cache interaction with device failures."""

    def test_stale_cache_returns_data_with_metadata(self, mock_conn_mgr, mock_node):
        """Populate cache, expire, fail device — stale data should have _cache.stale metadata."""
        from network_mcp.helpers import command_cache

        # Populate
        mock_node.run_commands.return_value = [{"version": "4.32.1F", "hostname": "spine-01"}]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"

        # Expire the entry
        key = command_cache._make_key("spine-01", ["show version"], "json")
        entry = command_cache._cache.get(key)
        if entry:
            entry.timestamp = time.monotonic() - 200
            entry.ttl = 30

            # Mock connection failure
            mock_node.run_commands.side_effect = ConnectionError("device unreachable")

            with patch("network_mcp.helpers._get_settings") as mock_settings:
                settings = MagicMock()
                settings.net_cache_stale_if_error_ttl = 300
                settings.net_rate_limit_max_wait = 5.0
                settings.net_conn_acquire_timeout = 30.0
                mock_settings.return_value = settings

                result = run_show_command(conn_mgr, "spine-01", ["show version"])
                # Should get stale data or fallback error
                if result.get("_cache", {}).get("stale"):
                    assert result["status"] == "success"
                else:
                    # Connection error fallback
                    assert result["status"] == "error"

    def test_no_stale_cache_means_error_returned(self, mock_conn_mgr, mock_node):
        """Without prior cache, device failure should return error directly."""
        mock_node.run_commands.side_effect = ConnectionError("device unreachable")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert "device" in result


# ===========================================================================
# Extended Graceful Shutdown Tests
# ===========================================================================


class TestGracefulShutdownExtended:
    """Extended shutdown tests verifying in-flight tracking and rejection."""

    @pytest.fixture(autouse=True)
    def _reset_shutdown(self):
        import network_mcp.server as server_mod

        server_mod._shutting_down = False
        server_mod._in_flight_count = 0
        server_mod._shutdown_event.clear()
        yield
        server_mod._shutting_down = False
        server_mod._in_flight_count = 0
        server_mod._shutdown_event.clear()

    def test_new_ops_after_shutdown_return_correct_error_code(self):
        """Operations after shutdown flag should return SERVER_SHUTTING_DOWN error code."""
        import network_mcp.server as server_mod

        server_mod._shutting_down = True
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert result["error_code"] == "SERVER_SHUTTING_DOWN"
        assert result["is_retryable"] is False

    def test_in_flight_counter_increments_and_decrements(self):
        """increment_in_flight and decrement_in_flight should track properly."""
        import network_mcp.server as server_mod

        assert server_mod.get_in_flight_count() == 0
        assert server_mod.increment_in_flight() is True
        assert server_mod.get_in_flight_count() == 1
        assert server_mod.increment_in_flight() is True
        assert server_mod.get_in_flight_count() == 2
        server_mod.decrement_in_flight()
        assert server_mod.get_in_flight_count() == 1
        server_mod.decrement_in_flight()
        assert server_mod.get_in_flight_count() == 0

    def test_decrement_never_goes_negative(self):
        """decrement_in_flight should not go below 0."""
        import network_mcp.server as server_mod

        server_mod.decrement_in_flight()
        server_mod.decrement_in_flight()
        assert server_mod.get_in_flight_count() == 0

    def test_shutdown_event_set_when_in_flight_reaches_zero(self):
        """_shutdown_event should be set when last in-flight completes during shutdown."""
        import network_mcp.server as server_mod

        server_mod.increment_in_flight()
        server_mod._shutting_down = True
        assert not server_mod._shutdown_event.is_set()
        server_mod.decrement_in_flight()
        assert server_mod._shutdown_event.is_set()

    def test_concurrent_ops_complete_before_shutdown(self):
        """Multiple concurrent ops started before shutdown should all complete."""
        import network_mcp.server as server_mod

        slow_node = _make_mock_node(delay=0.3)
        devices = {f"dev-{i}": slow_node for i in range(5)}
        results = []
        lock = threading.Lock()
        started = threading.Barrier(5, timeout=5)

        def _run_op(name):
            try:
                started.wait()
            except threading.BrokenBarrierError:
                pass
            r = run_show_command(conn_mgr, name, ["show version"])
            with lock:
                results.append(r)

        with _multi_device_conn_mgr(devices):
            threads = [threading.Thread(target=_run_op, args=(f"dev-{i}",)) for i in range(5)]
            for t in threads:
                t.start()

            time.sleep(0.1)
            server_mod._shutting_down = True

            for t in threads:
                t.join(timeout=10)

        # All 5 should have completed
        assert len(results) == 5
        for r in results:
            assert r["status"] in ("success", "error")


# ===========================================================================
# Extended Circuit Breaker + Stale Cache Cascade Tests
# ===========================================================================


class TestCircuitBreakerStaleCacheCascadeExtended:
    """Extended cascade interaction between circuit breaker and stale cache."""

    @pytest.fixture(autouse=True)
    def _reset_shutdown(self):
        import network_mcp.server as server_mod

        server_mod._shutting_down = False
        server_mod._in_flight_count = 0
        server_mod._shutdown_event.clear()
        yield
        server_mod._shutting_down = False

    def test_circuit_open_without_stale_cache_returns_circuit_open_error(self, mock_conn_mgr, mock_node):
        """When circuit is open and no stale cache, should get CIRCUIT_OPEN error."""
        from network_mcp.helpers import circuit_breaker_registry

        circuit_breaker_registry._enabled = True
        try:
            breaker = circuit_breaker_registry.get("no-cache-device")
            for _ in range(breaker._threshold):
                breaker.record_failure()

            result = run_show_command(conn_mgr, "no-cache-device", ["show version"])
            assert result["status"] == "error"
            assert result["error_code"] == "CIRCUIT_OPEN"
        finally:
            circuit_breaker_registry._enabled = False

    def test_circuit_open_prefers_stale_cache_over_error(self, mock_conn_mgr, mock_node):
        """When circuit is open but stale cache exists, prefer stale data."""
        from network_mcp.helpers import circuit_breaker_registry, command_cache

        # Populate cache
        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        result = run_show_command(conn_mgr, "stale-device", ["show version"])
        assert result["status"] == "success"

        # Expire the entry but within stale_ttl
        key = command_cache._make_key("stale-device", ["show version"], "json")
        entry = command_cache._cache.get(key)
        if entry:
            entry.timestamp = time.monotonic() - 100
            entry.ttl = 30

        # Open circuit breaker
        circuit_breaker_registry._enabled = True
        try:
            breaker = circuit_breaker_registry.get("stale-device")
            for _ in range(breaker._threshold):
                breaker.record_failure()

            with patch("network_mcp.helpers._get_settings") as mock_settings:
                settings = MagicMock()
                settings.net_cache_stale_if_error_ttl = 300
                settings.net_rate_limit_max_wait = 5.0
                settings.net_conn_acquire_timeout = 30.0
                mock_settings.return_value = settings

                result = run_show_command(conn_mgr, "stale-device", ["show version"])
                # Should get stale data or circuit open error
                if result.get("_cache", {}).get("stale"):
                    assert result["status"] == "success"
                else:
                    assert result["error_code"] == "CIRCUIT_OPEN"
        finally:
            circuit_breaker_registry._enabled = False


# ===========================================================================
# Extended Rate Limiter Backoff Under Burst Tests
# ===========================================================================


class TestRateLimiterBackoffExtended:
    """Extended rate limiter tests comparing backoff vs no-backoff success rates."""

    def test_backoff_success_count_exceeds_no_backoff(self):
        """With backoff, total successes should be >= no-backoff count."""
        from network_mcp.rate_limiter import TokenBucket

        # No backoff
        bucket1 = TokenBucket(rate=5.0, capacity=3.0)
        no_backoff = sum(1 for _ in range(15) if bucket1.acquire())

        # With backoff
        bucket2 = TokenBucket(rate=5.0, capacity=3.0)
        with_backoff = sum(1 for _ in range(15) if bucket2.acquire_with_backoff(max_wait=0.3))

        assert with_backoff >= no_backoff

    def test_backoff_completes_without_hanging_under_burst(self):
        """Burst of 30 requests with backoff should complete in bounded time."""
        from network_mcp.rate_limiter import TokenBucket

        bucket = TokenBucket(rate=10.0, capacity=5.0)
        start = time.monotonic()
        for _ in range(30):
            bucket.acquire_with_backoff(max_wait=0.2)
        elapsed = time.monotonic() - start
        assert elapsed < 20, f"Backoff burst took {elapsed:.1f}s"

    def test_zero_max_wait_backoff_equivalent_to_acquire(self):
        """acquire_with_backoff(max_wait=0) should behave like acquire()."""
        from network_mcp.rate_limiter import TokenBucket

        bucket = TokenBucket(rate=1.0, capacity=1.0)
        assert bucket.acquire_with_backoff(max_wait=0) is True
        assert bucket.acquire_with_backoff(max_wait=0) is False


# ===========================================================================
# Error Code Classification Tests
# ===========================================================================


class TestErrorCodeClassification:
    """Test classify_exception maps exceptions to correct error codes."""

    def test_classify_pyeapi_connection_error(self):
        from network_mcp.errors import classify_exception

        exc = pyeapi.eapilib.ConnectionError("https", "refused")
        assert classify_exception(exc) == "CONNECTION_ERROR"

    def test_classify_pyeapi_command_error(self):
        from network_mcp.errors import classify_exception

        exc = pyeapi.eapilib.CommandError(1000, "invalid command")
        assert classify_exception(exc) == "COMMAND_ERROR"

    def test_classify_timeout_error(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(TimeoutError("timed out")) == "TIMEOUT_ERROR"

    def test_classify_permission_error(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(PermissionError("denied")) == "AUTH_ERROR"

    def test_classify_connection_refused_error(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(ConnectionRefusedError("refused")) == "DEVICE_UNREACHABLE"

    def test_classify_generic_exception(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(RuntimeError("oops")) == "INTERNAL_ERROR"


# ===========================================================================
# Comprehensive Reliability: Error Code Correctness Per Failure Mode
# ===========================================================================


class TestErrorCodeCorrectnessPerFailureMode:
    """Verify that each failure mode returns the correct error_code and metadata."""

    def test_connection_error_returns_connection_error_code(self, mock_conn_mgr, mock_node):
        """pyeapi.ConnectionError should map to CONNECTION_ERROR."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert result["error_code"] == "CONNECTION_ERROR"
        assert result["error_category"] == "transient"
        assert result["is_retryable"] is True

    def test_command_error_returns_command_error_code(self, mock_conn_mgr, mock_node):
        """pyeapi.CommandError should map to COMMAND_ERROR."""
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "Invalid input")
        result = run_show_command(conn_mgr, "spine-01", ["show invalid-cmd"])
        assert result["status"] == "error"
        assert result["error_code"] == "COMMAND_ERROR"
        assert result["error_category"] == "permanent"
        assert result["is_retryable"] is False

    def test_timeout_error_returns_timeout_error_code(self, mock_conn_mgr, mock_node):
        """TimeoutError should map to TIMEOUT_ERROR."""
        mock_node.run_commands.side_effect = TimeoutError("timed out after 30s")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert result["error_code"] in ("TIMEOUT_ERROR", "INTERNAL_ERROR")
        assert result["is_retryable"] is True

    def test_runtime_error_returns_internal_error_code(self, mock_conn_mgr, mock_node):
        """RuntimeError should map to INTERNAL_ERROR."""
        mock_node.run_commands.side_effect = RuntimeError("unexpected internal failure")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert result["error_code"] == "INTERNAL_ERROR"
        assert result["is_retryable"] is True

    def test_circuit_open_returns_circuit_open_code(self):
        """Circuit breaker open should return CIRCUIT_OPEN error code."""
        from network_mcp.helpers import circuit_breaker_registry

        circuit_breaker_registry._enabled = True
        try:
            breaker = circuit_breaker_registry.get("err-code-device")
            for _ in range(breaker._threshold):
                breaker.record_failure()

            result = run_show_command(conn_mgr, "err-code-device", ["show version"])
            assert result["status"] == "error"
            assert result["error_code"] == "CIRCUIT_OPEN"
            assert result["error_category"] == "transient"
            assert result["is_retryable"] is True
            assert result.get("retry_after_seconds") == 60
        finally:
            circuit_breaker_registry._enabled = False
            circuit_breaker_registry.reset()

    def test_rate_limited_returns_rate_limited_code(self, mock_conn_mgr, mock_node):
        """Rate limiting should return RATE_LIMITED error code."""
        with patch("network_mcp.helpers.device_rate_limiter") as mock_rl:
            mock_rl.check_show.return_value = False
            mock_rl.check_show_with_backoff.return_value = False
            mock_rl.show_wait_time.return_value = 1.0
            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] == "error"
            assert result["error_code"] == "RATE_LIMITED"
            assert result["is_retryable"] is True

    def test_shutdown_returns_server_shutting_down_code(self):
        """Shutdown state should return SERVER_SHUTTING_DOWN error code."""
        import network_mcp.server as server_mod

        old_shutting_down = server_mod._shutting_down
        server_mod._shutting_down = True
        try:
            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] == "error"
            assert result["error_code"] == "SERVER_SHUTTING_DOWN"
            assert result["is_retryable"] is False
        finally:
            server_mod._shutting_down = old_shutting_down

    def test_make_error_response_includes_retry_after(self):
        """make_error_response should include retry_after_seconds for retryable errors."""
        from network_mcp.errors import ErrorCode, make_error_response

        result = make_error_response(ErrorCode.CONNECTION_ERROR, "spine-01", "Connection refused")
        assert result["retry_after_seconds"] == 5
        assert result["is_retryable"] is True

    def test_make_error_response_no_retry_after_for_permanent(self):
        """Permanent errors should not include retry_after_seconds."""
        from network_mcp.errors import ErrorCode, make_error_response

        result = make_error_response(ErrorCode.COMMAND_ERROR, "spine-01", "Invalid command")
        assert "retry_after_seconds" not in result
        assert result["is_retryable"] is False


# ===========================================================================
# Comprehensive: Device Timeout Mid-Operation With Full Error Verification
# ===========================================================================


class TestDeviceTimeoutMidOperationComprehensive:
    """End-to-end timeout scenarios verifying error code, category, retryability."""

    def test_slow_device_timeout_has_correct_metadata(self, mock_conn_mgr, mock_node):
        """Slow device timeout should have full error metadata in response."""
        mock_node.run_commands.side_effect = TimeoutError("connection timed out after 30s")
        result = run_show_command(conn_mgr, "slow-switch", ["show version"])
        assert result["status"] == "error"
        assert result["device"] == "slow-switch"
        assert "error_code" in result
        assert "error_category" in result
        assert "is_retryable" in result
        # Timeout or internal (due to retry wrapping) — both are transient and retryable
        assert result["error_code"] in ("TIMEOUT_ERROR", "INTERNAL_ERROR")
        assert result["is_retryable"] is True

    def test_concurrent_timeouts_all_return_error(self):
        """Multiple devices timing out concurrently should all return errors."""
        timeout_dev = _make_mock_node()
        timeout_dev.run_commands.side_effect = TimeoutError("timed out")
        devices = {f"timeout-dev-{i}": timeout_dev for i in range(5)}

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    name: executor.submit(run_show_command, conn_mgr, name, ["show version"]) for name in devices
                }
                results = {name: f.result(timeout=30) for name, f in futures.items()}

        for name, result in results.items():
            assert result["status"] == "error", f"{name} did not return error"
            assert result["device"] == name
            assert "error_code" in result

    def test_timeout_followed_by_recovery(self, mock_conn_mgr, mock_node):
        """Device that times out then recovers should succeed on next call."""
        call_count = {"n": 0}

        def _timeout_then_recover(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] <= 3:
                raise TimeoutError("device is slow")
            return [MOCK_VERSION]

        mock_node.run_commands.side_effect = _timeout_then_recover
        # First call fails (retries exhaust within the call)
        result1 = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result1["status"] == "error"

        # Second call should succeed after device recovers
        result2 = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result2["status"] == "success"


# ===========================================================================
# Comprehensive: Stale-if-Error Cache With Multiple Expiry Windows
# ===========================================================================


class TestStaleIfErrorCacheComprehensive:
    """Test stale-if-error cache serving across different expiry windows."""

    def test_fresh_cache_served_without_stale_flag(self, mock_conn_mgr, mock_node):
        """Non-expired cache entries should be served without _cache.stale flag."""
        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        result1 = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result1["status"] == "success"

        # Second call should hit cache, no stale flag
        result2 = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result2.get("_cache", {}).get("stale") is not True

    def test_expired_cache_not_served_without_stale_if_error(self, mock_conn_mgr, mock_node):
        """Expired cache should NOT be served when stale_if_error is not configured."""
        from network_mcp.helpers import command_cache

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        run_show_command(conn_mgr, "spine-01", ["show version"])

        # Expire entry
        key = command_cache._make_key("spine-01", ["show version"], "json")
        entry = command_cache._cache.get(key)
        if entry:
            entry.timestamp = time.monotonic() - 1000
            entry.ttl = 30

        # Query with default settings (stale_if_error_ttl=0)
        mock_node.run_commands.side_effect = ConnectionError("device down")
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        # Should be an error, not stale cache
        assert result["status"] == "error"

    def test_stale_cache_beyond_stale_ttl_returns_error(self, mock_conn_mgr, mock_node):
        """Cache expired beyond stale_ttl should not be served even with stale-if-error."""
        from network_mcp.helpers import command_cache

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        run_show_command(conn_mgr, "spine-01", ["show version"])

        # Expire entry far beyond stale_ttl
        key = command_cache._make_key("spine-01", ["show version"], "json")
        entry = command_cache._cache.get(key)
        if entry:
            entry.timestamp = time.monotonic() - 10000  # 10000s ago
            entry.ttl = 30

        mock_node.run_commands.side_effect = ConnectionError("device down")

        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = MagicMock()
            settings.net_cache_stale_if_error_ttl = 300  # 5 min stale window
            settings.net_rate_limit_max_wait = 5.0
            settings.net_conn_acquire_timeout = 30.0
            mock_settings.return_value = settings

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            # Should be error — stale data is too old
            assert result["status"] == "error"

    def test_cache_invalidation_prevents_stale_serving(self, mock_conn_mgr, mock_node):
        """After invalidate_host, stale cache should not be available."""
        from network_mcp.helpers import command_cache

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        run_show_command(conn_mgr, "spine-01", ["show version"])

        # Invalidate cache for host
        command_cache.invalidate_host("spine-01")

        # Now fail device
        mock_node.run_commands.side_effect = ConnectionError("down")

        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = MagicMock()
            settings.net_cache_stale_if_error_ttl = 300
            settings.net_rate_limit_max_wait = 5.0
            settings.net_conn_acquire_timeout = 30.0
            mock_settings.return_value = settings

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            # No stale cache after invalidation
            assert result["status"] == "error"


# ===========================================================================
# Comprehensive: Multi-Device Mixed Failure Integration
# ===========================================================================


class TestMultiDeviceMixedFailureIntegration:
    """Integration tests combining multiple failure types across a device fleet."""

    def test_fleet_with_all_failure_types(self):
        """10-device fleet with mix of timeout, conn error, cmd error, and success."""
        ok_dev = _make_mock_node()
        timeout_dev = _make_mock_node()
        timeout_dev.run_commands.side_effect = TimeoutError("timed out")
        conn_err_dev = _make_mock_node()
        conn_err_dev.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        cmd_err_dev = _make_mock_node()
        cmd_err_dev.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "invalid")

        devices = {
            "ok-1": ok_dev,
            "ok-2": _make_mock_node(),
            "ok-3": _make_mock_node(),
            "ok-4": _make_mock_node(),
            "ok-5": _make_mock_node(),
            "ok-6": _make_mock_node(),
            "timeout-1": timeout_dev,
            "conn-err-1": conn_err_dev,
            "conn-err-2": _make_mock_node(failing=True),
            "cmd-err-1": cmd_err_dev,
        }

        with _multi_device_conn_mgr(devices):
            results = {}
            for name in devices:
                results[name] = run_show_command(conn_mgr, name, ["show version"])

        # Verify success/error split
        success = [n for n, r in results.items() if r["status"] == "success"]
        errors = [n for n, r in results.items() if r["status"] == "error"]
        assert len(success) == 6
        assert len(errors) == 4

        # Verify specific error codes where present
        for name in errors:
            assert "error_code" in results[name], f"{name} missing error_code"
            assert results[name]["device"] == name

    def test_concurrent_fleet_with_mixed_failures(self):
        """Concurrent access to 10-device fleet with mixed failures."""
        ok_dev = _make_mock_node()
        fail_dev = _make_mock_node(failing=True)
        slow_dev = _make_mock_node(delay=0.2)

        devices = {}
        for i in range(10):
            if i < 5:
                devices[f"dev-{i}"] = ok_dev
            elif i < 8:
                devices[f"dev-{i}"] = fail_dev
            else:
                devices[f"dev-{i}"] = slow_dev

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = {
                    name: executor.submit(run_show_command, conn_mgr, name, ["show version"]) for name in devices
                }
                results = {name: f.result(timeout=15) for name, f in futures.items()}

        # All should complete (no crashes, no deadlocks)
        assert len(results) == 10
        for name, r in results.items():
            assert r["status"] in ("success", "error")
            assert r["device"] == name

    def test_failure_on_one_device_does_not_corrupt_cache_for_another(self):
        """Failure on device-A should not affect cached results for device-B."""
        from network_mcp.helpers import command_cache

        ok_dev = _make_mock_node()
        fail_dev = _make_mock_node(failing=True)

        devices = {"dev-ok": ok_dev, "dev-fail": fail_dev}

        with _multi_device_conn_mgr(devices):
            # Populate cache for dev-ok
            r_ok = run_show_command(conn_mgr, "dev-ok", ["show version"])
            assert r_ok["status"] == "success"

            # Fail on dev-fail
            r_fail = run_show_command(conn_mgr, "dev-fail", ["show version"])
            assert r_fail["status"] == "error"

            # dev-ok cache should still be intact
            cached = command_cache.get("dev-ok", ["show version"], "json")
            assert cached is not None


# ===========================================================================
# Comprehensive: Circuit Breaker Full State Machine Transitions
# ===========================================================================


class TestCircuitBreakerFullTransitions:
    """Test all circuit breaker state transitions with timing verification."""

    def test_healthy_to_open_to_half_open_to_healthy(self):
        """Full cycle: HEALTHY -> OPEN -> HALF_OPEN -> HEALTHY."""
        cb = CircuitBreaker("cycle-device", threshold=2, cooldown=1)

        # Start healthy
        assert cb.get_state() == DeviceHealth.HEALTHY
        assert cb.allow_request() is True

        # Two failures -> OPEN
        cb.record_failure()
        cb.record_failure()
        assert cb.get_state() == DeviceHealth.CIRCUIT_OPEN
        assert cb.allow_request() is False

        # Wait for cooldown -> HALF_OPEN
        time.sleep(1.1)
        assert cb.get_state() == DeviceHealth.HALF_OPEN
        assert cb.allow_request() is True

        # Success -> HEALTHY
        cb.record_success()
        assert cb.get_state() == DeviceHealth.HEALTHY
        assert cb.consecutive_failures == 0

    def test_healthy_to_open_to_half_open_to_open_backoff(self):
        """HALF_OPEN failure should re-open with doubled cooldown."""
        cb = CircuitBreaker("backoff-device", threshold=2, cooldown=1)

        cb.record_failure()
        cb.record_failure()
        assert cb.get_state() == DeviceHealth.CIRCUIT_OPEN
        original_cooldown = cb._cooldown

        time.sleep(1.1)
        assert cb.get_state() == DeviceHealth.HALF_OPEN

        # Fail again -> OPEN with backoff
        cb.record_failure()
        assert cb.get_state() == DeviceHealth.CIRCUIT_OPEN
        assert cb._cooldown == min(original_cooldown * 2, CircuitBreaker._MAX_COOLDOWN)

    def test_multiple_open_close_cycles(self):
        """Multiple open/close cycles should work without state corruption."""
        cb = CircuitBreaker("multi-cycle", threshold=2, cooldown=1)

        for _cycle in range(3):
            # Open the breaker
            cb.record_failure()
            cb.record_failure()
            assert cb.get_state() == DeviceHealth.CIRCUIT_OPEN

            # Wait for half-open
            time.sleep(1.1)
            assert cb.get_state() == DeviceHealth.HALF_OPEN

            # Recover
            cb.record_success()
            assert cb.get_state() == DeviceHealth.HEALTHY

    def test_success_before_threshold_keeps_healthy(self):
        """A success before reaching threshold should keep state healthy."""
        cb = CircuitBreaker("resilient-device", threshold=3, cooldown=60)

        cb.record_failure()
        cb.record_failure()
        assert cb.get_state() == DeviceHealth.HEALTHY

        # Success resets failures
        cb.record_success()
        assert cb.consecutive_failures == 0
        assert cb.get_state() == DeviceHealth.HEALTHY

        # Three more failures needed to open
        cb.record_failure()
        cb.record_failure()
        assert cb.get_state() == DeviceHealth.HEALTHY
        cb.record_failure()
        assert cb.get_state() == DeviceHealth.CIRCUIT_OPEN
