"""Reusable chaos engineering test utilities.

Provides fixtures and context managers for simulating adverse network conditions:
- Slow devices (configurable latency injection)
- Flaky devices (probabilistic failure injection)
- Unreachable devices (immediate connection failures)
- Redis outage simulation (for distributed backend fallback testing)
"""

import random
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Mock response data
# ---------------------------------------------------------------------------

MOCK_VERSION = {
    "modelName": "vEOS-lab",
    "version": "4.32.1F",
    "serialNumber": "TEST123",
    "hostname": "test-switch",
}


# ---------------------------------------------------------------------------
# Device simulation helpers
# ---------------------------------------------------------------------------


def slow_device(delay_seconds: float = 1.0) -> MagicMock:
    """Create a mock device node that introduces latency on every command.

    Args:
        delay_seconds: Seconds to sleep before returning data. Defaults to 1.0.

    Returns:
        A MagicMock node whose run_commands sleeps for the specified delay.
    """
    node = MagicMock()
    node.platform = "eos"
    node.is_connected.return_value = True

    def _slow_response(*args, **kwargs):  # noqa: ARG001
        time.sleep(delay_seconds)
        return [MOCK_VERSION]

    node.run_commands.side_effect = _slow_response
    node.run_show = node.run_commands
    node.config.return_value = []
    node.run_config = node.config
    return node


def flaky_device(failure_rate: float = 0.5, seed: int | None = None) -> MagicMock:
    """Create a mock device node that fails with a given probability.

    Args:
        failure_rate: Probability of failure per command (0.0–1.0). Defaults to 0.5.
        seed: Optional random seed for reproducible behavior.

    Returns:
        A MagicMock node whose run_commands raises ConnectionError randomly.
    """
    rng = random.Random(seed)  # noqa: S311
    node = MagicMock()
    node.platform = "eos"
    node.is_connected.return_value = True

    def _flaky_response(*args, **kwargs):  # noqa: ARG001
        if rng.random() < failure_rate:
            raise ConnectionError("device unreachable (injected flake)")
        return [MOCK_VERSION]

    node.run_commands.side_effect = _flaky_response
    node.run_show = node.run_commands
    node.config.return_value = []
    node.run_config = node.config
    return node


def unreachable_device(error_cls: type[Exception] = ConnectionError) -> MagicMock:
    """Create a mock device node that always fails with a connection error.

    Args:
        error_cls: Exception class to raise. Defaults to ConnectionError.

    Returns:
        A MagicMock node that always raises the specified error.
    """
    node = MagicMock()
    node.platform = "eos"
    node.is_connected.return_value = False

    node.run_commands.side_effect = error_cls("device unreachable (injected outage)")
    node.run_show = node.run_commands
    node.config.side_effect = error_cls("device unreachable (injected outage)")
    node.run_config = node.config
    return node


def healthy_device() -> MagicMock:
    """Create a mock device node that always succeeds.

    Returns:
        A MagicMock node that returns MOCK_VERSION on every show command.
    """
    node = MagicMock()
    node.platform = "eos"
    node.is_connected.return_value = True
    node.run_commands.return_value = [MOCK_VERSION]
    node.run_show = node.run_commands
    node.config.return_value = []
    node.run_config = node.config
    return node


# ---------------------------------------------------------------------------
# Context managers for device pool simulation
# ---------------------------------------------------------------------------


@contextmanager
def multi_device_conn_mgr(device_map: dict[str, MagicMock]):
    """Patch conn_mgr to route requests to different mock nodes per host.

    Args:
        device_map: Mapping of hostname -> mock node.
    """

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


@contextmanager
def redis_outage():
    """Simulate Redis being unavailable for distributed rate limiter / cache / circuit breaker.

    Patches the Redis client constructor to raise ConnectionError, forcing the
    distributed backends to fall back to fail-open behavior.
    """
    with patch("redis.from_url", side_effect=ConnectionError("Redis connection refused (injected outage)")):
        yield


# ---------------------------------------------------------------------------
# Pytest fixtures (importable by tests that need them)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_slow_device():
    """Fixture providing a slow mock device (1s delay per command)."""
    return slow_device(delay_seconds=1.0)


@pytest.fixture
def mock_flaky_device():
    """Fixture providing a flaky mock device (50% failure rate, seed=42)."""
    return flaky_device(failure_rate=0.5, seed=42)


@pytest.fixture
def mock_unreachable_device():
    """Fixture providing a permanently unreachable mock device."""
    return unreachable_device()


@pytest.fixture
def mock_healthy_device():
    """Fixture providing a healthy mock device that always succeeds."""
    return healthy_device()
