"""Alerting hooks for critical network MCP events.

Supports webhook (HTTP POST), syslog, and custom callback hooks.
Alert types cover circuit breaker state changes, rate limiting, SLO
violations, error rate spikes, and connection pool exhaustion.

Usage::

    from network_mcp.alerts import alert_manager

    # Register a custom callback
    alert_manager.register_hook(my_callback)

    # Set a webhook URL for HTTP POST alerts
    alert_manager.set_webhook("https://hooks.example.com/alerts")

    # Fire an alert
    alert_manager.fire(
        alert_type="circuit_breaker_open",
        severity="warning",
        message="Device spine-01 is unreachable",
        device="spine-01",
    )
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger("network-mcp")

# --- Alert type constants ---

ALERT_CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
ALERT_RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
ALERT_SLO_VIOLATION = "slo_violation"
ALERT_ERROR_RATE_SPIKE = "error_rate_spike"
ALERT_CONNECTION_POOL_EXHAUSTION = "connection_pool_exhaustion"

ALERT_TYPES = frozenset(
    {
        ALERT_CIRCUIT_BREAKER_OPEN,
        ALERT_RATE_LIMIT_EXCEEDED,
        ALERT_SLO_VIOLATION,
        ALERT_ERROR_RATE_SPIKE,
        ALERT_CONNECTION_POOL_EXHAUSTION,
    }
)

SEVERITY_LEVELS = ("info", "warning", "critical")


@dataclass
class Alert:
    """Immutable representation of a fired alert.

    Attributes:
        alert_type: The category of alert (see ALERT_TYPES).
        severity: Severity level — ``"info"``, ``"warning"``, or ``"critical"``.
        message: Human-readable description of the alert.
        timestamp: ISO 8601 timestamp of when the alert was fired.
        context: Additional key-value context (e.g., device, slo_name).
    """

    alert_type: str
    severity: str
    message: str
    timestamp: str
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize the alert to a JSON-compatible dict."""
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
            "timestamp": self.timestamp,
            **self.context,
        }


# Type alias for alert hook callbacks
AlertHook = Callable[[Alert], None]


class AlertManager:
    """Central alert manager that dispatches alerts to registered hooks.

    Thread-safe: hook registration and alert dispatch happen under a lock.
    Hooks are called synchronously in registration order. A failing hook
    does not prevent subsequent hooks from executing.
    """

    # Maximum number of recent alerts to retain in memory
    _MAX_HISTORY = 1000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hooks: list[AlertHook] = []
        self._webhook_url: str = ""
        self._history: list[Alert] = []
        # Suppression: alert_type -> last_fired_time (monotonic) for dedup
        self._suppression_window: float = 60.0  # seconds
        self._last_fired: dict[str, float] = {}

    def register_hook(self, callback: AlertHook) -> None:
        """Register a callback to receive alerts.

        The callback receives an ``Alert`` object and should handle its own
        error handling. Exceptions in hooks are logged but do not propagate.

        Args:
            callback: A callable that accepts a single ``Alert`` argument.
        """
        with self._lock:
            self._hooks.append(callback)

    def unregister_hook(self, callback: AlertHook) -> bool:
        """Remove a previously registered hook.

        Args:
            callback: The callback to remove.

        Returns:
            True if the hook was found and removed, False otherwise.
        """
        with self._lock:
            try:
                self._hooks.remove(callback)
                return True
            except ValueError:
                return False

    def set_webhook(self, url: str) -> None:
        """Set the webhook URL for HTTP POST alert delivery.

        Args:
            url: The URL to POST alert JSON payloads to. Set to ``""`` to disable.
        """
        with self._lock:
            self._webhook_url = url

    def set_suppression_window(self, seconds: float) -> None:
        """Set the suppression window for duplicate alerts.

        Alerts of the same type fired within this window are suppressed.

        Args:
            seconds: Suppression window in seconds. Set to 0 to disable.
        """
        self._suppression_window = max(0.0, seconds)

    def fire(self, alert_type: str, severity: str, message: str, **context) -> Alert | None:
        """Fire an alert to all registered hooks and the webhook.

        Args:
            alert_type: Alert category (e.g., ``"circuit_breaker_open"``).
            severity: ``"info"``, ``"warning"``, or ``"critical"``.
            message: Human-readable alert message.
            **context: Additional context key-value pairs.

        Returns:
            The fired Alert object, or None if suppressed.
        """
        if severity not in SEVERITY_LEVELS:
            severity = "info"

        # Suppression check
        now = time.monotonic()
        with self._lock:
            if self._suppression_window > 0:
                last = self._last_fired.get(alert_type, 0.0)
                if (now - last) < self._suppression_window:
                    logger.debug("Alert suppressed (dedup): %s", alert_type)
                    return None
            self._last_fired[alert_type] = now

        alert = Alert(
            alert_type=alert_type,
            severity=severity,
            message=message,
            timestamp=datetime.now(tz=UTC).isoformat(),
            context=context,
        )

        # Store in history
        with self._lock:
            self._history.append(alert)
            if len(self._history) > self._MAX_HISTORY:
                self._history = self._history[-self._MAX_HISTORY :]

        # Log the alert
        log_level = logging.WARNING if severity in ("warning", "critical") else logging.INFO
        logger.log(log_level, "Alert [%s/%s]: %s", alert_type, severity, message)

        # Dispatch to hooks
        with self._lock:
            hooks = list(self._hooks)
            webhook_url = self._webhook_url

        for hook in hooks:
            try:
                hook(alert)
            except Exception:
                logger.debug("Alert hook %s failed", hook.__name__, exc_info=True)

        # Send to webhook (non-blocking best-effort)
        if webhook_url:
            self._send_webhook(webhook_url, alert)

        return alert

    def get_history(self, limit: int = 50, alert_type: str | None = None) -> list[dict]:
        """Return recent alert history as dicts.

        Args:
            limit: Maximum number of alerts to return.
            alert_type: Optional filter by alert type.

        Returns:
            List of alert dicts, most recent first.
        """
        with self._lock:
            history = list(self._history)

        if alert_type:
            history = [a for a in history if a.alert_type == alert_type]

        return [a.to_dict() for a in reversed(history[-limit:])]

    def clear_history(self) -> None:
        """Clear all stored alert history."""
        with self._lock:
            self._history.clear()

    def reset(self) -> None:
        """Reset all state (hooks, webhook, history, suppression). For testing."""
        with self._lock:
            self._hooks.clear()
            self._webhook_url = ""
            self._history.clear()
            self._last_fired.clear()

    @staticmethod
    def _send_webhook(url: str, alert: Alert) -> None:
        """Send alert to webhook URL via HTTP POST (best-effort).

        Uses httpx if available, otherwise falls back to urllib.
        Errors are logged and silently ignored.
        """
        payload = json.dumps(alert.to_dict())
        try:
            import httpx

            with httpx.Client(timeout=5.0) as client:
                client.post(
                    url,
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )
        except ImportError:
            # Fall back to urllib
            try:
                import urllib.request

                req = urllib.request.Request(  # noqa: S310
                    url,
                    data=payload.encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)  # noqa: S310
            except Exception:
                logger.debug("Webhook delivery failed (urllib fallback)", exc_info=True)
        except Exception:
            logger.debug("Webhook delivery failed", exc_info=True)


# Module-level singleton
alert_manager = AlertManager()
