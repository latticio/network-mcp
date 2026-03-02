"""Tests for graceful shutdown with request draining."""

import threading
import time

import pytest

import network_mcp.server as server_mod
from network_mcp.helpers import run_config_command, run_show_command
from network_mcp.server import (
    conn_mgr,
    decrement_in_flight,
    get_in_flight_count,
    increment_in_flight,
    is_shutting_down,
)


@pytest.fixture(autouse=True)
def _reset_shutdown_state():
    """Reset shutdown state before and after each test to prevent cross-test pollution."""
    server_mod._shutting_down = False
    server_mod._in_flight_count = 0
    server_mod._shutdown_event.clear()
    yield
    server_mod._shutting_down = False
    server_mod._in_flight_count = 0
    server_mod._shutdown_event.clear()


class TestInFlightTracking:
    """Tests for in-flight request counter and shutdown rejection."""

    def test_increment_accepts_when_not_shutting_down(self):
        """increment_in_flight returns True when server is running normally."""
        assert increment_in_flight() is True
        assert get_in_flight_count() == 1
        decrement_in_flight()

    def test_increment_rejects_when_shutting_down(self):
        """increment_in_flight returns False when server is shutting down."""
        server_mod._shutting_down = True
        assert increment_in_flight() is False
        assert get_in_flight_count() == 0

    def test_decrement_signals_shutdown_event_at_zero(self):
        """decrement_in_flight sets the shutdown event when count reaches zero during shutdown."""
        server_mod._shutting_down = True
        server_mod._in_flight_count = 1
        assert not server_mod._shutdown_event.is_set()
        decrement_in_flight()
        assert get_in_flight_count() == 0
        assert server_mod._shutdown_event.is_set()

    def test_decrement_does_not_signal_when_not_shutting_down(self):
        """decrement_in_flight does not set shutdown event when server is running."""
        server_mod._in_flight_count = 1
        decrement_in_flight()
        assert get_in_flight_count() == 0
        assert not server_mod._shutdown_event.is_set()

    def test_decrement_does_not_go_below_zero(self):
        """decrement_in_flight clamps to 0."""
        assert get_in_flight_count() == 0
        decrement_in_flight()
        assert get_in_flight_count() == 0

    def test_is_shutting_down_reflects_state(self):
        """is_shutting_down returns the current flag value."""
        assert is_shutting_down() is False
        server_mod._shutting_down = True
        assert is_shutting_down() is True

    def test_concurrent_increment_decrement(self):
        """Multiple threads can safely increment and decrement."""
        errors = []
        count = 100

        def worker():
            try:
                if increment_in_flight():
                    time.sleep(0.001)
                    decrement_in_flight()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert get_in_flight_count() == 0


class TestRunShowCommandShutdown:
    """Tests for run_show_command rejection during shutdown."""

    def test_rejects_during_shutdown(self):
        """run_show_command returns error when server is shutting down."""
        server_mod._shutting_down = True
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "error"
        assert result["error_code"] == "SERVER_SHUTTING_DOWN"
        assert "shutting down" in result["error"]

    def test_accepts_when_running(self, mock_conn_mgr, mock_node):
        """run_show_command works normally when server is not shutting down."""
        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["status"] == "success"


class TestRunConfigCommandShutdown:
    """Tests for run_config_command rejection during shutdown."""

    def test_rejects_during_shutdown(self):
        """run_config_command returns error when server is shutting down."""
        server_mod._shutting_down = True
        result = run_config_command(conn_mgr, "spine-01", ["vlan 100", "name TEST"], "create_vlan")
        assert result["status"] == "error"
        assert result["error_code"] == "SERVER_SHUTTING_DOWN"
        assert "shutting down" in result["error"]


class TestGracefulDrain:
    """Tests for draining in-flight requests during shutdown."""

    def test_drain_waits_for_in_flight(self):
        """Shutdown event is set only when all in-flight requests complete."""
        # Simulate 2 in-flight requests
        increment_in_flight()
        increment_in_flight()
        assert get_in_flight_count() == 2

        server_mod._shutting_down = True

        # First decrement — still 1 in-flight
        decrement_in_flight()
        assert get_in_flight_count() == 1
        assert not server_mod._shutdown_event.is_set()

        # Second decrement — should signal
        decrement_in_flight()
        assert get_in_flight_count() == 0
        assert server_mod._shutdown_event.is_set()

    def test_shutdown_timeout(self):
        """Drain times out when in-flight requests don't complete."""
        # Simulate a stuck in-flight request
        increment_in_flight()
        server_mod._shutting_down = True

        # Wait with a very short timeout
        drained = server_mod._shutdown_event.wait(timeout=0.05)
        assert drained is False
        assert get_in_flight_count() == 1

        # Clean up
        decrement_in_flight()

    def test_drain_completes_immediately_when_no_in_flight(self):
        """Shutdown event can be set immediately when no requests are in-flight."""
        server_mod._shutting_down = True
        # With 0 in-flight, a decrement would signal, but we should just check the count
        assert get_in_flight_count() == 0
        # The event isn't set automatically, but the lifespan would skip waiting


class TestInitiateShutdown:
    """Tests for the signal handler callback."""

    def test_initiate_shutdown_sets_flag(self):
        """_initiate_shutdown sets the _shutting_down flag."""
        assert server_mod._shutting_down is False
        server_mod._initiate_shutdown()
        assert server_mod._shutting_down is True
