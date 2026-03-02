"""gNMI connection management tests.

Tests for GnmiManager: per-request client creation, error mapping,
execute_get, execute_set, execute_subscribe, and capabilities.

Note: GnmiManager creates a fresh gNMIclient per request (no pooling),
as gRPC channels are not thread-safe. These tests verify the design and
behavior of the manager's connection creation and error handling.
"""

from __future__ import annotations

import threading
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_grpc():
    """Create a mock grpc module with a real RpcError class.

    MagicMock() can't be used directly for grpc because isinstance()
    requires a real type for the second argument. This builds a module
    with a proper RpcError base class.
    """
    mock_grpc = types.ModuleType("grpc")
    mock_grpc.RpcError = type("RpcError", (Exception,), {})
    return mock_grpc


def _make_settings(**overrides):
    """Create a mock NetworkSettings for GnmiManager."""
    from pydantic import SecretStr

    settings = MagicMock()
    settings.net_username = overrides.get("username", "admin")
    settings.net_password = SecretStr(overrides.get("password", "password"))
    settings.gnmi_port = overrides.get("gnmi_port", 6030)
    settings.gnmi_insecure = overrides.get("gnmi_insecure", True)
    settings.gnmi_timeout = overrides.get("gnmi_timeout", 10.0)
    settings.gnmi_tls_cert = overrides.get("gnmi_tls_cert", None)
    settings.gnmi_tls_key = overrides.get("gnmi_tls_key", None)
    settings.gnmi_tls_ca = overrides.get("gnmi_tls_ca", None)
    return settings


def _make_gnmi_manager(**overrides):
    """Create a GnmiManager with mock settings."""
    from network_mcp.gnmi_connection import GnmiManager

    return GnmiManager(_make_settings(**overrides))


# ---------------------------------------------------------------------------
# Client creation tests
# ---------------------------------------------------------------------------


class TestGnmiManagerClientCreation:
    """Tests for GnmiManager.create_client."""

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_create_client_with_defaults(self, mock_gnmi_client):
        """Create client with default settings → correct target/credentials."""
        manager = _make_gnmi_manager()
        manager.create_client("192.168.1.1")

        mock_gnmi_client.assert_called_once()
        call_kwargs = mock_gnmi_client.call_args[1]
        assert call_kwargs["target"] == ("192.168.1.1", "6030")
        assert call_kwargs["username"] == "admin"
        assert call_kwargs["password"] == "password"
        assert call_kwargs["insecure"] is True

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_create_client_with_custom_port(self, mock_gnmi_client):
        """Create client with custom port → port override used."""
        manager = _make_gnmi_manager()
        manager.create_client("192.168.1.1", port=50051)

        call_kwargs = mock_gnmi_client.call_args[1]
        assert call_kwargs["target"] == ("192.168.1.1", "50051")

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_create_client_with_tls(self, mock_gnmi_client):
        """Create client with TLS cert/key/CA → TLS config applied."""
        manager = _make_gnmi_manager(
            gnmi_tls_cert="/certs/cert.pem",
            gnmi_tls_key="/certs/key.pem",
            gnmi_tls_ca="/certs/ca.pem",
        )
        manager.create_client("192.168.1.1")

        call_kwargs = mock_gnmi_client.call_args[1]
        assert call_kwargs["insecure"] is False
        assert call_kwargs["path_cert"] == "/certs/cert.pem"
        assert call_kwargs["path_key"] == "/certs/key.pem"
        assert call_kwargs["path_root"] == "/certs/ca.pem"

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", False)
    def test_create_client_without_pygnmi(self):
        """pygnmi not installed → RuntimeError raised."""
        manager = _make_gnmi_manager()

        with pytest.raises(RuntimeError, match="pygnmi is not installed"):
            manager.create_client("192.168.1.1")


# ---------------------------------------------------------------------------
# execute_get tests
# ---------------------------------------------------------------------------


class TestGnmiExecuteGet:
    """Tests for GnmiManager.execute_get."""

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_execute_get_success(self, mock_gnmi_client_cls):
        """Successful gNMI Get → returns success dict with data."""
        mock_gc = MagicMock()
        mock_gc.get.return_value = {
            "notification": [
                {
                    "timestamp": 1234567890,
                    "update": [{"path": "/interfaces/interface[name=eth0]/state/oper-status", "val": "UP"}],
                }
            ]
        }
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gnmi_client_cls.return_value = mock_gc

        manager = _make_gnmi_manager()
        result = manager.execute_get("192.168.1.1", ["/interfaces/interface/state/oper-status"])

        assert result["status"] == "success"
        assert result["device"] == "192.168.1.1"
        assert len(result["data"]) == 1
        assert result["data"][0]["val"] == "UP"

    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_execute_get_connection_failure(self, mock_gnmi_client_cls):
        """gNMI connection failure → returns error dict."""
        mock_grpc = _make_mock_grpc()

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(side_effect=Exception("Connection refused"))
        mock_gnmi_client_cls.return_value = mock_gc

        manager = _make_gnmi_manager()
        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = manager.execute_get("192.168.1.1", ["/system/state"])

        assert result["status"] == "error"
        assert result["device"] == "192.168.1.1"
        assert "error" in result

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_execute_get_empty_notification(self, mock_gnmi_client_cls):
        """gNMI Get returns empty notification → empty data list."""
        mock_gc = MagicMock()
        mock_gc.get.return_value = {"notification": []}
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gnmi_client_cls.return_value = mock_gc

        manager = _make_gnmi_manager()
        result = manager.execute_get("192.168.1.1", ["/system/state"])

        assert result["status"] == "success"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# execute_set tests
# ---------------------------------------------------------------------------


class TestGnmiExecuteSet:
    """Tests for GnmiManager.execute_set."""

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_execute_set_success(self, mock_gnmi_client_cls):
        """Successful gNMI Set → returns success dict."""
        mock_gc = MagicMock()
        mock_gc.set.return_value = {"response": [{"op": "UPDATE"}]}
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gnmi_client_cls.return_value = mock_gc

        manager = _make_gnmi_manager()
        result = manager.execute_set(
            "192.168.1.1",
            updates=[("/interfaces/interface[name=eth0]/config/description", {"description": "Test"})],
        )

        assert result["status"] == "success"
        assert result["device"] == "192.168.1.1"

    def test_execute_set_no_updates_no_deletes(self):
        """No updates or deletes provided → returns error."""
        manager = _make_gnmi_manager()
        result = manager.execute_set("192.168.1.1")

        assert result["status"] == "error"
        assert "No updates or deletes" in result["error"]


# ---------------------------------------------------------------------------
# execute_capabilities tests
# ---------------------------------------------------------------------------


class TestGnmiExecuteCapabilities:
    """Tests for GnmiManager.execute_capabilities."""

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_execute_capabilities_success(self, mock_gnmi_client_cls):
        """Successful capabilities → returns gnmi_version and models."""
        mock_gc = MagicMock()
        mock_gc.capabilities.return_value = {
            "gnmi_version": "0.8.0",
            "supported_encodings": ["JSON", "JSON_IETF"],
            "supported_models": [{"name": "openconfig-interfaces", "organization": "OpenConfig", "version": "3.0.0"}],
        }
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gnmi_client_cls.return_value = mock_gc

        manager = _make_gnmi_manager()
        result = manager.execute_capabilities("192.168.1.1")

        assert result["status"] == "success"
        assert result["data"]["gnmi_version"] == "0.8.0"
        assert len(result["data"]["supported_models"]) == 1


# ---------------------------------------------------------------------------
# execute_subscribe_once tests
# ---------------------------------------------------------------------------


class TestGnmiExecuteSubscribeOnce:
    """Tests for GnmiManager.execute_subscribe_once."""

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_subscribe_once_success(self, mock_gnmi_client_cls):
        """Subscribe ONCE → collects updates until sync_response."""
        mock_gc = MagicMock()
        mock_gc.subscribe2.return_value = iter(
            [
                {"update": {"path": "/system/state/hostname", "val": "spine-01"}},
                {"update": {"path": "/system/state/uptime", "val": "864000"}},
                {"sync_response": True},
            ]
        )
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gnmi_client_cls.return_value = mock_gc

        manager = _make_gnmi_manager()
        result = manager.execute_subscribe_once("192.168.1.1", ["/system/state"])

        assert result["status"] == "success"
        assert len(result["data"]) == 2

    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_subscribe_once_failure(self, mock_gnmi_client_cls):
        """Subscribe ONCE fails → error dict returned."""
        mock_grpc = _make_mock_grpc()

        mock_gc = MagicMock()
        mock_gc.subscribe2.side_effect = Exception("Subscription failed")
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gnmi_client_cls.return_value = mock_gc

        manager = _make_gnmi_manager()
        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = manager.execute_subscribe_once("192.168.1.1", ["/system/state"])

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# gRPC error mapping tests
# ---------------------------------------------------------------------------


class TestGrpcErrorMapping:
    """Tests for _map_grpc_error utility."""

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    def test_unauthenticated_error(self):
        """gRPC UNAUTHENTICATED → friendly auth error message."""
        from network_mcp.gnmi_connection import _map_grpc_error

        mock_error = MagicMock()
        mock_code = MagicMock()
        mock_code.name = "UNAUTHENTICATED"
        mock_error.code.return_value = mock_code
        mock_error.details.return_value = ""

        with patch("network_mcp.gnmi_connection.grpc") as mock_grpc:
            mock_grpc.RpcError = type(mock_error)
            result = _map_grpc_error(mock_error)

        assert "Authentication failed" in result

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    def test_unavailable_error(self):
        """gRPC UNAVAILABLE → friendly connectivity error message."""
        from network_mcp.gnmi_connection import _map_grpc_error

        mock_error = MagicMock()
        mock_code = MagicMock()
        mock_code.name = "UNAVAILABLE"
        mock_error.code.return_value = mock_code
        mock_error.details.return_value = "tcp dial failed"

        with patch("network_mcp.gnmi_connection.grpc") as mock_grpc:
            mock_grpc.RpcError = type(mock_error)
            result = _map_grpc_error(mock_error)

        assert "unreachable" in result.lower() or "not enabled" in result.lower()
        assert "tcp dial failed" in result

    def test_generic_exception_mapping(self):
        """Non-gRPC exception → 'Unexpected gNMI error' message."""
        from network_mcp.gnmi_connection import _map_grpc_error

        mock_grpc = _make_mock_grpc()
        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = _map_grpc_error(ValueError("something went wrong"))
        assert "Unexpected gNMI error" in result

    def test_timeout_error_mapping(self):
        """FutureTimeoutError → gNMI timeout message."""
        from network_mcp.gnmi_connection import _map_grpc_error

        mock_grpc = _make_mock_grpc()

        class FutureTimeoutError(Exception):
            pass

        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = _map_grpc_error(FutureTimeoutError("timed out"))
        assert "timeout" in result.lower()


# ---------------------------------------------------------------------------
# _flatten_notifications tests
# ---------------------------------------------------------------------------


class TestFlattenNotifications:
    """Tests for _flatten_notifications utility."""

    def test_flatten_multiple_notifications(self):
        """Multiple notifications with updates → flattened to list."""
        from network_mcp.gnmi_connection import _flatten_notifications

        result = _flatten_notifications(
            {
                "notification": [
                    {
                        "timestamp": 1000,
                        "update": [
                            {"path": "/a/b", "val": "x"},
                            {"path": "/c/d", "val": "y"},
                        ],
                    },
                    {
                        "timestamp": 2000,
                        "update": [
                            {"path": "/e/f", "val": "z"},
                        ],
                    },
                ]
            }
        )

        assert len(result) == 3
        assert result[0]["path"] == "/a/b"
        assert result[0]["timestamp"] == 1000
        assert result[2]["path"] == "/e/f"
        assert result[2]["timestamp"] == 2000

    def test_empty_notification(self):
        """Empty notification list → empty result."""
        from network_mcp.gnmi_connection import _flatten_notifications

        assert _flatten_notifications({"notification": []}) == []

    def test_no_notification_key(self):
        """Missing notification key → empty result."""
        from network_mcp.gnmi_connection import _flatten_notifications

        assert _flatten_notifications({}) == []

    def test_notification_without_updates(self):
        """Notification without update key → nothing added."""
        from network_mcp.gnmi_connection import _flatten_notifications

        result = _flatten_notifications({"notification": [{"timestamp": 1000}]})
        assert result == []


# ---------------------------------------------------------------------------
# Concurrent access tests
# ---------------------------------------------------------------------------


class TestGnmiConcurrentAccess:
    """Tests for concurrent GnmiManager access."""

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True)
    @patch("network_mcp.gnmi_connection.gNMIclient")
    def test_concurrent_execute_get_creates_separate_clients(self, mock_gnmi_client_cls):
        """Multiple threads calling execute_get → each gets a fresh client."""
        mock_gc = MagicMock()
        mock_gc.get.return_value = {"notification": []}
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gnmi_client_cls.return_value = mock_gc

        manager = _make_gnmi_manager()
        results = []
        errors = []

        def do_get(host):
            try:
                result = manager.execute_get(host, ["/system/state"])
                results.append(result)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=do_get, args=(f"10.0.0.{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert len(results) == 5
        # Each thread creates a new gNMIclient → at least 5 calls
        assert mock_gnmi_client_cls.call_count >= 5
