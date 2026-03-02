"""Tests for the circuit breaker module."""

import threading
import time

from network_mcp.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, DeviceHealth

# --- DeviceHealth enum ---


def test_device_health_values():
    assert DeviceHealth.HEALTHY.value == "healthy"
    assert DeviceHealth.CIRCUIT_OPEN.value == "open"
    assert DeviceHealth.HALF_OPEN.value == "half_open"


# --- CircuitBreaker state machine ---


class TestCircuitBreakerStateMachine:
    """Test the core state machine transitions."""

    def test_initial_state_is_healthy(self):
        cb = CircuitBreaker("host1", threshold=3, cooldown=60)
        assert cb.state == DeviceHealth.HEALTHY
        assert cb.consecutive_failures == 0
        assert cb.host == "host1"

    def test_healthy_allows_requests(self):
        cb = CircuitBreaker("host1")
        assert cb.allow_request() is True

    def test_healthy_to_open_after_threshold_failures(self):
        cb = CircuitBreaker("host1", threshold=3, cooldown=60)
        cb.record_failure()
        assert cb.state == DeviceHealth.HEALTHY
        assert cb.consecutive_failures == 1

        cb.record_failure()
        assert cb.state == DeviceHealth.HEALTHY
        assert cb.consecutive_failures == 2

        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN
        assert cb.consecutive_failures == 3

    def test_open_denies_requests(self):
        cb = CircuitBreaker("host1", threshold=2, cooldown=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN
        assert cb.allow_request() is False

    def test_open_to_half_open_after_cooldown(self):
        cb = CircuitBreaker("host1", threshold=2, cooldown=10)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

        # Simulate cooldown elapsed by backdating _last_failure_time
        cb._last_failure_time = time.monotonic() - 20
        assert cb.get_state() == DeviceHealth.HALF_OPEN

    def test_half_open_allows_one_request(self):
        cb = CircuitBreaker("host1", threshold=2, cooldown=10)
        cb.record_failure()
        cb.record_failure()

        cb._last_failure_time = time.monotonic() - 20
        assert cb.allow_request() is True

    def test_half_open_to_healthy_on_success(self):
        cb = CircuitBreaker("host1", threshold=2, cooldown=10)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

        cb._last_failure_time = time.monotonic() - 20
        assert cb.get_state() == DeviceHealth.HALF_OPEN

        cb.record_success()
        assert cb.state == DeviceHealth.HEALTHY
        assert cb.consecutive_failures == 0

    def test_half_open_to_open_on_failure(self):
        cb = CircuitBreaker("host1", threshold=2, cooldown=10)
        cb.record_failure()
        cb.record_failure()

        cb._last_failure_time = time.monotonic() - 20
        assert cb.get_state() == DeviceHealth.HALF_OPEN

        # Fail again while in HALF_OPEN
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("host1", threshold=5, cooldown=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.consecutive_failures == 2

        cb.record_success()
        assert cb.consecutive_failures == 0
        assert cb.state == DeviceHealth.HEALTHY

    def test_intermittent_failures_dont_open_circuit(self):
        """Success between failures prevents reaching threshold."""
        cb = CircuitBreaker("host1", threshold=3, cooldown=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # resets count
        cb.record_failure()
        cb.record_failure()
        assert cb.state == DeviceHealth.HEALTHY
        assert cb.consecutive_failures == 2

    def test_to_dict(self):
        cb = CircuitBreaker("host1", threshold=3, cooldown=60)
        cb.record_failure()
        d = cb.to_dict()
        assert d["host"] == "host1"
        assert d["state"] == "healthy"
        assert d["consecutive_failures"] == 1
        assert d["threshold"] == 3
        assert d["cooldown_seconds"] == 60


class TestCircuitBreakerConcurrency:
    """Test thread safety of circuit breaker state transitions."""

    def test_concurrent_failures(self):
        """Multiple threads recording failures should not corrupt state."""
        cb = CircuitBreaker("host1", threshold=100, cooldown=60)
        barrier = threading.Barrier(10)

        def record_failures():
            barrier.wait()
            for _ in range(50):
                cb.record_failure()

        threads = [threading.Thread(target=record_failures) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cb.consecutive_failures == 500

    def test_concurrent_mixed_operations(self):
        """Concurrent successes and failures should not cause crashes."""
        cb = CircuitBreaker("host1", threshold=1000, cooldown=60)
        barrier = threading.Barrier(6)

        def record_failures():
            barrier.wait()
            for _ in range(100):
                cb.record_failure()

        def record_successes():
            barrier.wait()
            for _ in range(100):
                cb.record_success()

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=record_failures))
            threads.append(threading.Thread(target=record_successes))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # State should be valid (no crash, state is a valid DeviceHealth)
        assert cb.state in list(DeviceHealth)


# --- CircuitBreakerRegistry ---


class TestCircuitBreakerRegistry:
    """Test the registry that manages per-device breakers."""

    def test_disabled_registry_allows_all(self):
        reg = CircuitBreakerRegistry(threshold=1, cooldown=60, enabled=False)
        assert reg.allow_request("host1") is True
        reg.record_failure("host1")
        assert reg.allow_request("host1") is True  # still allowed when disabled

    def test_enabled_registry_creates_breakers(self):
        reg = CircuitBreakerRegistry(threshold=2, cooldown=60, enabled=True)
        assert reg.allow_request("host1") is True
        reg.record_failure("host1")
        reg.record_failure("host1")
        assert reg.allow_request("host1") is False

    def test_different_hosts_independent(self):
        reg = CircuitBreakerRegistry(threshold=2, cooldown=60, enabled=True)
        reg.record_failure("host1")
        reg.record_failure("host1")
        assert reg.allow_request("host1") is False
        assert reg.allow_request("host2") is True  # different device is fine

    def test_get_open_circuits(self):
        reg = CircuitBreakerRegistry(threshold=1, cooldown=60, enabled=True)
        reg.record_failure("host1")
        reg.record_failure("host2")
        assert set(reg.get_open_circuits()) == {"host1", "host2"}

    def test_get_open_circuits_after_recovery(self):
        reg = CircuitBreakerRegistry(threshold=1, cooldown=60, enabled=True)
        reg.record_failure("host1")
        reg.record_failure("host2")
        reg.record_success("host1")  # recovers via success call
        open_circuits = reg.get_open_circuits()
        assert "host1" not in open_circuits
        assert "host2" in open_circuits

    def test_stats(self):
        reg = CircuitBreakerRegistry(threshold=2, cooldown=60, enabled=True)
        reg.record_failure("host1")
        reg.record_failure("host1")
        reg.record_failure("host2")

        stats = reg.stats()
        assert stats["enabled"] is True
        assert stats["total_breakers"] == 2
        assert "host1" in stats["open_circuits"]
        assert "host2" not in stats["open_circuits"]
        assert "host1" in stats["breakers"]
        assert "host2" in stats["breakers"]

    def test_reset_single(self):
        reg = CircuitBreakerRegistry(threshold=1, cooldown=60, enabled=True)
        reg.record_failure("host1")
        reg.record_failure("host2")
        reg.reset("host1")
        assert reg.allow_request("host1") is True  # fresh breaker
        assert reg.allow_request("host2") is False  # still open

    def test_reset_all(self):
        reg = CircuitBreakerRegistry(threshold=1, cooldown=60, enabled=True)
        reg.record_failure("host1")
        reg.record_failure("host2")
        reg.reset()
        assert reg.allow_request("host1") is True
        assert reg.allow_request("host2") is True

    def test_disabled_record_operations_are_noop(self):
        reg = CircuitBreakerRegistry(threshold=1, cooldown=60, enabled=False)
        reg.record_failure("host1")
        reg.record_success("host1")
        # No breakers created when disabled
        assert reg.stats()["total_breakers"] == 0

    def test_enabled_property(self):
        reg = CircuitBreakerRegistry(enabled=True)
        assert reg.enabled is True
        reg2 = CircuitBreakerRegistry(enabled=False)
        assert reg2.enabled is False


# --- Integration with helpers ---


class TestCircuitBreakerHelperIntegration:
    """Test circuit breaker integration with run_show_command/run_config_command."""

    def test_show_command_blocked_when_circuit_open(self, mock_conn_mgr, mock_node):
        """When circuit breaker is open, run_show_command returns error without hitting device."""
        from network_mcp.helpers import circuit_breaker_registry, run_show_command
        from network_mcp.server import conn_mgr

        # Enable circuit breaker and force it open
        original_enabled = circuit_breaker_registry._enabled
        circuit_breaker_registry._enabled = True
        try:
            breaker = circuit_breaker_registry.get("test-switch")
            # Manually open the circuit
            for _ in range(3):
                breaker.record_failure()
            assert breaker.state == DeviceHealth.CIRCUIT_OPEN

            result = run_show_command(conn_mgr, "test-switch", ["show version"])
            assert result["status"] == "error"
            assert "Circuit breaker open" in result["error"]
            assert "3 consecutive failures" in result["error"]
            # Device should NOT have been called
            mock_node.run_commands.assert_not_called()
        finally:
            circuit_breaker_registry._enabled = original_enabled

    def test_show_command_records_success(self, mock_conn_mgr, mock_node):
        """Successful show command records success on circuit breaker."""
        from network_mcp.helpers import circuit_breaker_registry, run_show_command
        from network_mcp.server import conn_mgr

        original_enabled = circuit_breaker_registry._enabled
        circuit_breaker_registry._enabled = True
        try:
            mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
            result = run_show_command(conn_mgr, "test-switch", ["show version"])
            assert result["status"] == "success"

            breaker = circuit_breaker_registry.get("test-switch")
            assert breaker.state == DeviceHealth.HEALTHY
            assert breaker.consecutive_failures == 0
        finally:
            circuit_breaker_registry._enabled = original_enabled

    def test_show_command_records_failure_on_connection_error(self, mock_conn_mgr, mock_node):
        """Connection error records failure on circuit breaker."""
        import pyeapi.eapilib

        from network_mcp.helpers import circuit_breaker_registry, run_show_command
        from network_mcp.server import conn_mgr

        original_enabled = circuit_breaker_registry._enabled
        circuit_breaker_registry._enabled = True
        try:
            mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
            run_show_command(conn_mgr, "test-switch", ["show version"])

            breaker = circuit_breaker_registry.get("test-switch")
            assert breaker.consecutive_failures == 1
        finally:
            circuit_breaker_registry._enabled = original_enabled

    def test_show_command_command_error_counts_as_success(self, mock_conn_mgr, mock_node):
        """CommandError means device is reachable — treated as success for circuit breaker."""
        import pyeapi.eapilib

        from network_mcp.helpers import circuit_breaker_registry, run_show_command
        from network_mcp.server import conn_mgr

        original_enabled = circuit_breaker_registry._enabled
        circuit_breaker_registry._enabled = True
        try:
            # Add a prior failure
            breaker = circuit_breaker_registry.get("test-switch")
            breaker.record_failure()
            assert breaker.consecutive_failures == 1

            mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1000, "invalid command")
            run_show_command(conn_mgr, "test-switch", ["show invalid"])

            assert breaker.consecutive_failures == 0  # reset by record_success
        finally:
            circuit_breaker_registry._enabled = original_enabled

    def test_disabled_circuit_breaker_allows_all(self, mock_conn_mgr, mock_node):
        """When circuit breaker is disabled (default), requests always pass through."""
        from network_mcp.helpers import circuit_breaker_registry, run_show_command
        from network_mcp.server import conn_mgr

        assert circuit_breaker_registry._enabled is False
        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        result = run_show_command(conn_mgr, "test-switch", ["show version"])
        assert result["status"] == "success"


# --- Property vs get_state() side-effect tests ---


class TestCircuitBreakerSideEffects:
    """Verify .state property has no side effects; .get_state() performs transitions."""

    def test_state_property_does_not_transition_open_to_half_open(self):
        """Reading .state when OPEN and cooldown expired should NOT transition to HALF_OPEN."""
        cb = CircuitBreaker("host1", threshold=2, cooldown=10)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

        # Backdate so cooldown has expired
        cb._last_failure_time = time.monotonic() - 20

        # .state should still return CIRCUIT_OPEN (no transition)
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

    def test_get_state_transitions_open_to_half_open(self):
        """get_state() when OPEN and cooldown expired SHOULD transition to HALF_OPEN."""
        cb = CircuitBreaker("host1", threshold=2, cooldown=10)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

        cb._last_failure_time = time.monotonic() - 20

        # get_state() should transition
        assert cb.get_state() == DeviceHealth.HALF_OPEN
        # And the state should now reflect the transition
        assert cb.state == DeviceHealth.HALF_OPEN


# --- Exponential backoff on HALF_OPEN failures ---


class TestCircuitBreakerExponentialBackoff:
    """Verify exponential backoff on repeated HALF_OPEN probe failures."""

    def test_cooldown_doubles_on_half_open_failure(self):
        """Each HALF_OPEN failure should double the cooldown."""
        cb = CircuitBreaker("host1", threshold=2, cooldown=60)
        cb.record_failure()
        cb.record_failure()
        assert cb._cooldown == 60

        # Trigger HALF_OPEN via get_state
        cb._last_failure_time = time.monotonic() - 70
        assert cb.get_state() == DeviceHealth.HALF_OPEN

        # Fail during probe — cooldown doubles to 120
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN
        assert cb._cooldown == 120

    def test_exponential_backoff_sequence(self):
        """Cooldown should follow 60 → 120 → 240 → 300 (capped)."""
        cb = CircuitBreaker("host1", threshold=2, cooldown=60)
        cb.record_failure()
        cb.record_failure()

        expected_cooldowns = [120, 240, 300, 300]
        for expected in expected_cooldowns:
            # Wait for cooldown to expire
            cb._last_failure_time = time.monotonic() - (cb._cooldown + 1)
            assert cb.get_state() == DeviceHealth.HALF_OPEN
            cb.record_failure()
            assert cb._cooldown == expected

    def test_cooldown_capped_at_max(self):
        """Cooldown should never exceed _MAX_COOLDOWN (300s)."""
        cb = CircuitBreaker("host1", threshold=2, cooldown=200)
        cb.record_failure()
        cb.record_failure()

        # First HALF_OPEN failure: 200 * 2 = 400, capped to 300
        cb._last_failure_time = time.monotonic() - 210
        assert cb.get_state() == DeviceHealth.HALF_OPEN
        cb.record_failure()
        assert cb._cooldown == 300

    def test_success_resets_cooldown_to_base(self):
        """On successful probe, cooldown should reset to the base value."""
        cb = CircuitBreaker("host1", threshold=2, cooldown=60)
        cb.record_failure()
        cb.record_failure()

        # Fail in HALF_OPEN to double cooldown
        cb._last_failure_time = time.monotonic() - 70
        cb.get_state()
        cb.record_failure()
        assert cb._cooldown == 120

        # Now succeed in HALF_OPEN
        cb._last_failure_time = time.monotonic() - 130
        cb.get_state()
        cb.record_success()
        assert cb._cooldown == 60
        assert cb.state == DeviceHealth.HEALTHY
