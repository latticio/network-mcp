"""Tests for alerting hooks system."""

import time
from unittest.mock import patch

import pytest

from network_mcp.alerts import (
    ALERT_CIRCUIT_BREAKER_OPEN,
    ALERT_CONNECTION_POOL_EXHAUSTION,
    ALERT_ERROR_RATE_SPIKE,
    ALERT_RATE_LIMIT_EXCEEDED,
    ALERT_SLO_VIOLATION,
    ALERT_TYPES,
    SEVERITY_LEVELS,
    Alert,
    AlertManager,
    alert_manager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_alert_manager():
    """Reset the global alert manager before each test."""
    alert_manager.reset()
    yield
    alert_manager.reset()


@pytest.fixture
def manager():
    """Return a fresh AlertManager instance."""
    am = AlertManager()
    am.set_suppression_window(0)  # Disable suppression for tests
    return am


# ---------------------------------------------------------------------------
# 1. Alert dataclass
# ---------------------------------------------------------------------------


class TestAlertDataclass:
    def test_alert_fields(self):
        a = Alert(
            alert_type="test",
            severity="warning",
            message="Test alert",
            timestamp="2024-01-01T00:00:00Z",
            context={"device": "spine-01"},
        )
        assert a.alert_type == "test"
        assert a.severity == "warning"
        assert a.message == "Test alert"
        assert a.context["device"] == "spine-01"

    def test_alert_to_dict(self):
        a = Alert(
            alert_type="test",
            severity="info",
            message="Test",
            timestamp="2024-01-01T00:00:00Z",
            context={"device": "leaf-01"},
        )
        d = a.to_dict()
        assert d["alert_type"] == "test"
        assert d["severity"] == "info"
        assert d["message"] == "Test"
        assert d["device"] == "leaf-01"
        assert d["timestamp"] == "2024-01-01T00:00:00Z"

    def test_alert_empty_context(self):
        a = Alert(alert_type="test", severity="info", message="Test", timestamp="now")
        d = a.to_dict()
        assert "alert_type" in d
        assert len(d) == 4  # alert_type, severity, message, timestamp


# ---------------------------------------------------------------------------
# 2. Alert type constants
# ---------------------------------------------------------------------------


class TestAlertTypeConstants:
    def test_all_types_defined(self):
        assert ALERT_CIRCUIT_BREAKER_OPEN in ALERT_TYPES
        assert ALERT_RATE_LIMIT_EXCEEDED in ALERT_TYPES
        assert ALERT_SLO_VIOLATION in ALERT_TYPES
        assert ALERT_ERROR_RATE_SPIKE in ALERT_TYPES
        assert ALERT_CONNECTION_POOL_EXHAUSTION in ALERT_TYPES

    def test_five_types(self):
        assert len(ALERT_TYPES) == 5

    def test_severity_levels(self):
        assert SEVERITY_LEVELS == ("info", "warning", "critical")


# ---------------------------------------------------------------------------
# 3. Register and fire hooks
# ---------------------------------------------------------------------------


class TestRegisterAndFireHooks:
    def test_register_and_fire(self, manager):
        received = []
        manager.register_hook(lambda a: received.append(a))
        manager.fire("test", "info", "Hello")
        assert len(received) == 1
        assert received[0].alert_type == "test"
        assert received[0].message == "Hello"

    def test_multiple_hooks(self, manager):
        r1, r2 = [], []
        manager.register_hook(lambda a: r1.append(a))
        manager.register_hook(lambda a: r2.append(a))
        manager.fire("test", "info", "Hello")
        assert len(r1) == 1
        assert len(r2) == 1

    def test_hook_receives_context(self, manager):
        received = []
        manager.register_hook(lambda a: received.append(a))
        manager.fire("circuit_breaker_open", "warning", "Down", device="spine-01")
        assert received[0].context["device"] == "spine-01"

    def test_failing_hook_does_not_block_others(self, manager):
        r1 = []

        def bad_hook(a):
            raise RuntimeError("boom")

        manager.register_hook(bad_hook)
        manager.register_hook(lambda a: r1.append(a))
        manager.fire("test", "info", "Hello")
        assert len(r1) == 1

    def test_unregister_hook(self, manager):
        received = []
        hook = lambda a: received.append(a)  # noqa: E731
        manager.register_hook(hook)
        assert manager.unregister_hook(hook) is True
        manager.fire("test", "info", "Hello")
        assert len(received) == 0

    def test_unregister_nonexistent_hook(self, manager):
        assert manager.unregister_hook(lambda a: None) is False


# ---------------------------------------------------------------------------
# 4. Webhook support
# ---------------------------------------------------------------------------


class TestWebhookSupport:
    def test_set_webhook(self, manager):
        manager.set_webhook("https://hooks.example.com/alert")
        with manager._lock:
            assert manager._webhook_url == "https://hooks.example.com/alert"

    def test_clear_webhook(self, manager):
        manager.set_webhook("https://hooks.example.com/alert")
        manager.set_webhook("")
        with manager._lock:
            assert manager._webhook_url == ""

    def test_webhook_called_on_fire(self, manager):
        manager.set_webhook("https://hooks.example.com/alert")
        with patch.object(manager, "_send_webhook") as mock_send:
            manager.fire("test", "info", "Hello")
            mock_send.assert_called_once()
            alert = mock_send.call_args[0][1]
            assert alert.alert_type == "test"

    def test_webhook_not_called_when_empty(self, manager):
        with patch.object(manager, "_send_webhook") as mock_send:
            manager.fire("test", "info", "Hello")
            mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Alert history
# ---------------------------------------------------------------------------


class TestAlertHistory:
    def test_history_stores_alerts(self, manager):
        manager.fire("test1", "info", "First")
        manager.fire("test2", "warning", "Second")
        history = manager.get_history()
        assert len(history) == 2

    def test_history_most_recent_first(self, manager):
        manager.fire("first", "info", "1")
        manager.fire("second", "info", "2")
        history = manager.get_history()
        assert history[0]["alert_type"] == "second"
        assert history[1]["alert_type"] == "first"

    def test_history_limit(self, manager):
        for i in range(10):
            manager.fire(f"type_{i}", "info", f"Alert {i}")
        history = manager.get_history(limit=3)
        assert len(history) == 3

    def test_history_filter_by_type(self, manager):
        manager.fire("type_a", "info", "A1")
        manager.fire("type_b", "info", "B1")
        manager.fire("type_a", "info", "A2")
        history = manager.get_history(alert_type="type_a")
        assert len(history) == 2
        for h in history:
            assert h["alert_type"] == "type_a"

    def test_clear_history(self, manager):
        manager.fire("test", "info", "Hello")
        manager.clear_history()
        assert len(manager.get_history()) == 0

    def test_history_max_size(self, manager):
        for i in range(manager._MAX_HISTORY + 100):
            manager.fire(f"type_{i}", "info", f"Alert {i}")
        assert len(manager._history) <= manager._MAX_HISTORY


# ---------------------------------------------------------------------------
# 6. Alert suppression (dedup)
# ---------------------------------------------------------------------------


class TestAlertSuppression:
    def test_suppression_prevents_duplicate(self):
        manager = AlertManager()
        manager.set_suppression_window(60.0)
        a1 = manager.fire("test", "info", "First")
        a2 = manager.fire("test", "info", "Second")
        assert a1 is not None
        assert a2 is None  # suppressed

    def test_suppression_different_types_not_suppressed(self):
        manager = AlertManager()
        manager.set_suppression_window(60.0)
        a1 = manager.fire("type_a", "info", "A")
        a2 = manager.fire("type_b", "info", "B")
        assert a1 is not None
        assert a2 is not None

    def test_suppression_disabled(self, manager):
        # manager fixture has suppression=0
        a1 = manager.fire("test", "info", "First")
        a2 = manager.fire("test", "info", "Second")
        assert a1 is not None
        assert a2 is not None

    def test_suppression_window_expired(self):
        manager = AlertManager()
        manager.set_suppression_window(0.01)  # 10ms
        a1 = manager.fire("test", "info", "First")
        time.sleep(0.02)  # Wait for window to expire
        a2 = manager.fire("test", "info", "Second")
        assert a1 is not None
        assert a2 is not None


# ---------------------------------------------------------------------------
# 7. Severity handling
# ---------------------------------------------------------------------------


class TestSeverityHandling:
    def test_valid_severities(self, manager):
        for sev in SEVERITY_LEVELS:
            a = manager.fire("test", sev, f"Severity: {sev}")
            assert a is not None
            assert a.severity == sev

    def test_invalid_severity_defaults_to_info(self, manager):
        a = manager.fire("test", "bogus", "Bad severity")
        assert a is not None
        assert a.severity == "info"


# ---------------------------------------------------------------------------
# 8. Alert timestamp
# ---------------------------------------------------------------------------


class TestAlertTimestamp:
    def test_timestamp_is_iso_format(self, manager):
        a = manager.fire("test", "info", "Hello")
        assert a is not None
        # ISO 8601 format check
        assert "T" in a.timestamp
        assert a.timestamp.endswith("+00:00")


# ---------------------------------------------------------------------------
# 9. Reset
# ---------------------------------------------------------------------------


class TestAlertManagerReset:
    def test_reset_clears_hooks(self, manager):
        received = []
        manager.register_hook(lambda a: received.append(a))
        manager.reset()
        manager.fire("test", "info", "After reset")
        assert len(received) == 0

    def test_reset_clears_webhook(self, manager):
        manager.set_webhook("https://example.com")
        manager.reset()
        with manager._lock:
            assert manager._webhook_url == ""

    def test_reset_clears_history(self, manager):
        manager.fire("test", "info", "Before reset")
        manager.reset()
        assert len(manager.get_history()) == 0

    def test_reset_clears_suppression(self):
        manager = AlertManager()
        manager.set_suppression_window(60.0)
        manager.fire("test", "info", "First")
        manager.reset()
        manager.set_suppression_window(60.0)
        a = manager.fire("test", "info", "After reset")
        assert a is not None  # Not suppressed because reset cleared last_fired


# ---------------------------------------------------------------------------
# 10. Global singleton
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def test_alert_manager_is_singleton(self):
        from network_mcp.alerts import alert_manager as am1
        from network_mcp.alerts import alert_manager as am2

        assert am1 is am2

    def test_global_manager_works(self):
        received = []
        alert_manager.set_suppression_window(0)
        alert_manager.register_hook(lambda a: received.append(a))
        alert_manager.fire("test", "info", "Global test")
        assert len(received) == 1


# ---------------------------------------------------------------------------
# 11. Alert on SLO violation pattern
# ---------------------------------------------------------------------------


class TestSLOViolationAlert:
    def test_fire_slo_violation(self, manager):
        received = []
        manager.register_hook(lambda a: received.append(a))
        manager.fire(
            alert_type=ALERT_SLO_VIOLATION,
            severity="warning",
            message="SLO tool_call_success_rate violated: 98.5% < 99.9%",
            slo_name="tool_call_success_rate",
            actual=0.985,
            target=0.999,
        )
        assert len(received) == 1
        assert received[0].alert_type == ALERT_SLO_VIOLATION
        assert received[0].context["slo_name"] == "tool_call_success_rate"

    def test_fire_circuit_breaker_alert(self, manager):
        received = []
        manager.register_hook(lambda a: received.append(a))
        manager.fire(
            alert_type=ALERT_CIRCUIT_BREAKER_OPEN,
            severity="critical",
            message="Device spine-01 circuit breaker opened",
            device="spine-01",
            consecutive_failures=5,
        )
        assert len(received) == 1
        assert received[0].context["device"] == "spine-01"
        assert received[0].context["consecutive_failures"] == 5

    def test_fire_connection_pool_alert(self, manager):
        received = []
        manager.register_hook(lambda a: received.append(a))
        manager.fire(
            alert_type=ALERT_CONNECTION_POOL_EXHAUSTION,
            severity="critical",
            message="Connection pool at 95% utilization",
            pool_size=100,
            active=95,
        )
        assert len(received) == 1
        assert received[0].context["pool_size"] == 100

    def test_fire_rate_limit_alert(self, manager):
        received = []
        manager.register_hook(lambda a: received.append(a))
        manager.fire(
            alert_type=ALERT_RATE_LIMIT_EXCEEDED,
            severity="warning",
            message="Rate limiting active on spine-01",
            device="spine-01",
        )
        assert len(received) == 1

    def test_fire_error_rate_spike_alert(self, manager):
        received = []
        manager.register_hook(lambda a: received.append(a))
        manager.fire(
            alert_type=ALERT_ERROR_RATE_SPIKE,
            severity="warning",
            message="Error rate 8% over 5min window",
            error_rate=0.08,
            window_minutes=5,
        )
        assert len(received) == 1
        assert received[0].context["error_rate"] == 0.08
