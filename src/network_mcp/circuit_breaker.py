"""Per-device circuit breaker for fail-fast behavior on unreachable devices.

State machine:
    HEALTHY → CIRCUIT_OPEN after N consecutive failures (default 3)
    CIRCUIT_OPEN → HALF_OPEN after cooldown period (default 60s)
    HALF_OPEN → HEALTHY on success
    HALF_OPEN → CIRCUIT_OPEN on failure (resets cooldown)

Disabled by default (NET_CIRCUIT_BREAKER=false). When disabled, all
requests pass through regardless of device health.
"""

import logging
import threading
import time
from enum import Enum

logger = logging.getLogger("network-mcp")


class DeviceHealth(Enum):
    """Health states for a device's circuit breaker."""

    HEALTHY = "healthy"
    CIRCUIT_OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-device circuit breaker tracking consecutive failures.

    Thread-safe: all state transitions happen under a lock.
    """

    _MAX_COOLDOWN = 300  # Cap exponential backoff at 5 minutes

    def __init__(self, host: str, threshold: int = 3, cooldown: int = 60) -> None:
        self._host = host
        self._threshold = threshold
        self._base_cooldown = cooldown
        self._cooldown = cooldown
        self._lock = threading.Lock()
        self._state = DeviceHealth.HEALTHY
        self._consecutive_failures = 0
        self._last_failure_time: float = 0.0

    @property
    def host(self) -> str:
        return self._host

    @property
    def state(self) -> DeviceHealth:
        """Current state without side effects. Use get_state() for transition check."""
        with self._lock:
            return self._state

    def get_state(self) -> DeviceHealth:
        """Get current state, transitioning from OPEN to HALF_OPEN if cooldown expired."""
        with self._lock:
            if self._state == DeviceHealth.CIRCUIT_OPEN:
                if time.monotonic() - self._last_failure_time >= self._cooldown:
                    self._state = DeviceHealth.HALF_OPEN
                    logger.info(
                        "Circuit breaker for '%s': OPEN → HALF_OPEN (cooldown elapsed)",
                        self._host,
                    )
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def allow_request(self) -> bool:
        """Return True if a request should be allowed through.

        HEALTHY / DEGRADED / HALF_OPEN → allow
        CIRCUIT_OPEN → deny (fail fast), unless cooldown has elapsed
        """
        current = self.get_state()  # triggers OPEN→HALF_OPEN check
        return current != DeviceHealth.CIRCUIT_OPEN

    def record_success(self) -> None:
        """Record a successful operation. Resets failure count, cooldown, and transitions to HEALTHY."""
        with self._lock:
            old_state = self._state
            self._consecutive_failures = 0
            self._cooldown = self._base_cooldown
            self._state = DeviceHealth.HEALTHY
            if old_state != DeviceHealth.HEALTHY:
                logger.info(
                    "Circuit breaker for '%s': %s → HEALTHY (success recorded)",
                    self._host,
                    old_state.value,
                )

    def record_failure(self) -> None:
        """Record a failed operation. Increments failure count and may open the circuit."""
        with self._lock:
            self._consecutive_failures += 1
            self._last_failure_time = time.monotonic()

            if self._state == DeviceHealth.HALF_OPEN:
                # Failed during probe — reopen with exponential backoff on cooldown
                self._cooldown = min(self._cooldown * 2, self._MAX_COOLDOWN)
                self._state = DeviceHealth.CIRCUIT_OPEN
                logger.warning(
                    "Circuit breaker for '%s': HALF_OPEN → OPEN (probe failed, %d failures, cooldown=%ds)",
                    self._host,
                    self._consecutive_failures,
                    self._cooldown,
                )
            elif self._consecutive_failures >= self._threshold:
                old_state = self._state
                self._state = DeviceHealth.CIRCUIT_OPEN
                if old_state != DeviceHealth.CIRCUIT_OPEN:
                    logger.warning(
                        "Circuit breaker for '%s': %s → OPEN (%d consecutive failures)",
                        self._host,
                        old_state.value,
                        self._consecutive_failures,
                    )

    def to_dict(self) -> dict:
        """Return breaker state as a dict for observability."""
        current = self.get_state()  # triggers OPEN→HALF_OPEN check
        with self._lock:
            return {
                "host": self._host,
                "state": current.value,
                "consecutive_failures": self._consecutive_failures,
                "threshold": self._threshold,
                "cooldown_seconds": self._cooldown,
            }


class CircuitBreakerRegistry:
    """Manages per-device circuit breakers.

    Thread-safe: breaker creation and lookup happen under a lock.
    Individual breakers have their own locks for state transitions.
    """

    def __init__(self, threshold: int = 3, cooldown: int = 60, enabled: bool = False) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self._threshold = threshold
        self._cooldown = cooldown
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get(self, host: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a device."""
        with self._lock:
            breaker = self._breakers.get(host)
            if breaker is None:
                breaker = CircuitBreaker(host, self._threshold, self._cooldown)
                self._breakers[host] = breaker
            return breaker

    def allow_request(self, host: str) -> bool:
        """Check if a request to the given host is allowed.

        When the registry is disabled, always returns True.
        """
        if not self._enabled:
            return True
        return self.get(host).allow_request()

    def record_success(self, host: str) -> None:
        """Record a success for a host. No-op when disabled."""
        if not self._enabled:
            return
        self.get(host).record_success()

    def record_failure(self, host: str) -> None:
        """Record a failure for a host. No-op when disabled."""
        if not self._enabled:
            return
        self.get(host).record_failure()

    def get_open_circuits(self) -> list[str]:
        """Return list of hosts with open circuits."""
        with self._lock:
            return [
                host for host, breaker in self._breakers.items() if breaker.get_state() == DeviceHealth.CIRCUIT_OPEN
            ]

    def stats(self) -> dict:
        """Return registry stats for observability."""
        with self._lock:
            breaker_states = {host: breaker.to_dict() for host, breaker in self._breakers.items()}
        open_circuits = [h for h, b in breaker_states.items() if b["state"] == "open"]
        return {
            "enabled": self._enabled,
            "total_breakers": len(breaker_states),
            "open_circuits": open_circuits,
            "breakers": breaker_states,
        }

    def reset(self, host: str | None = None) -> None:
        """Reset breaker(s). If host is None, reset all."""
        with self._lock:
            if host is not None:
                self._breakers.pop(host, None)
            else:
                self._breakers.clear()
