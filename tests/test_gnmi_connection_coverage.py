"""Tests to close coverage gaps in network_mcp/gnmi_connection.py.

Covers: _map_grpc_error, GnmiManager methods (create_client, execute_get,
execute_subscribe_once, execute_subscribe_stream, execute_set,
execute_capabilities), and _flatten_notifications.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.gnmi_connection import (
    GNMI_NOT_ENABLED_ERROR,
    GNMI_NOT_INSTALLED_ERROR,
    GnmiManager,
    _flatten_notifications,
    _map_grpc_error,
)

# ---------------------------------------------------------------------------
# _map_grpc_error
# ---------------------------------------------------------------------------


def _make_mock_grpc():
    """Create a mock grpc module with a base RpcError class."""
    mock_grpc = MagicMock()

    class MockRpcError(Exception):
        pass

    mock_grpc.RpcError = MockRpcError
    return mock_grpc, MockRpcError


class TestMapGrpcError:
    def test_non_grpc_error_when_gnmi_available(self):
        """Non-gRPC errors should get 'Unexpected gNMI error' prefix."""
        mock_grpc, _ = _make_mock_grpc()
        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = _map_grpc_error(ValueError("some error"))
        assert "Unexpected gNMI error" in result

    def test_non_grpc_error_when_gnmi_unavailable(self):
        """When pygnmi is not installed, just return str(error)."""
        with patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", False):
            result = _map_grpc_error(ValueError("some error"))
        assert result == "some error"

    def test_future_timeout_error(self):
        """FutureTimeoutError should have a gNMI-specific message."""
        mock_grpc, _ = _make_mock_grpc()

        class FutureTimeoutError(Exception):
            pass

        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = _map_grpc_error(FutureTimeoutError("timeout"))
        assert "gNMI connection timeout" in result

    def test_grpc_rpc_error_with_details(self):
        """gRPC RpcError should map to friendly message with details."""
        mock_grpc, MockRpcError = _make_mock_grpc()

        mock_error = MockRpcError("test")
        mock_code = MagicMock()
        mock_code.name = "UNAVAILABLE"
        mock_error.code = MagicMock(return_value=mock_code)
        mock_error.details = MagicMock(return_value="host unreachable")

        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = _map_grpc_error(mock_error)
        assert "Device unreachable" in result
        assert "host unreachable" in result

    def test_grpc_rpc_error_without_details(self):
        """gRPC RpcError without details omits the details suffix."""
        mock_grpc, MockRpcError = _make_mock_grpc()

        mock_error = MockRpcError("test")
        mock_code = MagicMock()
        mock_code.name = "UNAUTHENTICATED"
        mock_error.code = MagicMock(return_value=mock_code)
        mock_error.details = MagicMock(return_value="")

        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = _map_grpc_error(mock_error)
        assert "Authentication failed" in result
        assert "Details:" not in result

    def test_grpc_rpc_error_unknown_code(self):
        """Unknown gRPC status codes get a generic message."""
        mock_grpc, MockRpcError = _make_mock_grpc()

        mock_error = MockRpcError("test")
        mock_code = MagicMock()
        mock_code.name = "RESOURCE_EXHAUSTED"
        mock_error.code = MagicMock(return_value=mock_code)
        mock_error.details = MagicMock(return_value="")

        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.grpc", mock_grpc),
        ):
            result = _map_grpc_error(mock_error)
        assert "gRPC error (RESOURCE_EXHAUSTED)" in result


# ---------------------------------------------------------------------------
# _flatten_notifications
# ---------------------------------------------------------------------------


class TestFlattenNotifications:
    def test_basic_flatten(self):
        data = {
            "notification": [
                {
                    "timestamp": 1234567890,
                    "update": [
                        {"path": "/interfaces/interface[name=eth0]/state/oper-status", "val": "UP"},
                        {"path": "/interfaces/interface[name=eth1]/state/oper-status", "val": "DOWN"},
                    ],
                }
            ]
        }
        result = _flatten_notifications(data)
        assert len(result) == 2
        assert result[0]["path"] == "/interfaces/interface[name=eth0]/state/oper-status"
        assert result[0]["val"] == "UP"
        assert result[0]["timestamp"] == 1234567890

    def test_empty_notifications(self):
        assert _flatten_notifications({}) == []
        assert _flatten_notifications({"notification": []}) == []

    def test_notification_without_updates(self):
        data = {"notification": [{"timestamp": 0}]}
        result = _flatten_notifications(data)
        assert result == []


# ---------------------------------------------------------------------------
# GnmiManager
# ---------------------------------------------------------------------------


def _make_mock_settings():
    """Create mock NetworkSettings for GnmiManager."""
    settings = MagicMock()
    settings.net_username = "admin"
    settings.net_password.get_secret_value.return_value = "password"
    settings.gnmi_port = 6030
    settings.gnmi_insecure = True
    settings.gnmi_timeout = 10
    settings.gnmi_tls_cert = ""
    settings.gnmi_tls_key = ""
    settings.gnmi_tls_ca = ""
    return settings


class TestGnmiManagerCreateClient:
    def test_create_client_when_gnmi_unavailable(self):
        """Should raise RuntimeError when pygnmi is not installed."""
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)
        with patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="pygnmi is not installed"):
                mgr.create_client("test-host")

    def test_create_client_with_tls(self):
        """Test TLS configuration when cert and key are provided."""
        settings = _make_mock_settings()
        settings.gnmi_tls_cert = "/path/to/cert.pem"
        settings.gnmi_tls_key = "/path/to/key.pem"
        settings.gnmi_tls_ca = "/path/to/ca.pem"
        mgr = GnmiManager(settings)

        mock_client_class = MagicMock()
        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.gNMIclient", mock_client_class),
        ):
            mgr.create_client("test-host", port=50051)

        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["insecure"] is False
        assert call_kwargs["path_cert"] == "/path/to/cert.pem"
        assert call_kwargs["path_key"] == "/path/to/key.pem"
        assert call_kwargs["path_root"] == "/path/to/ca.pem"
        assert call_kwargs["override"] == "test-host"

    def test_create_client_with_tls_no_ca(self):
        """Test TLS without CA cert."""
        settings = _make_mock_settings()
        settings.gnmi_tls_cert = "/path/to/cert.pem"
        settings.gnmi_tls_key = "/path/to/key.pem"
        settings.gnmi_tls_ca = ""
        mgr = GnmiManager(settings)

        mock_client_class = MagicMock()
        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.gNMIclient", mock_client_class),
        ):
            mgr.create_client("test-host")

        call_kwargs = mock_client_class.call_args[1]
        assert "path_root" not in call_kwargs

    def test_create_client_default_port(self):
        """Test client uses default port when none specified."""
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_client_class = MagicMock()
        with (
            patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True),
            patch("network_mcp.gnmi_connection.gNMIclient", mock_client_class),
        ):
            mgr.create_client("test-host")

        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["target"] == ("test-host", "6030")


class TestGnmiManagerExecuteGet:
    def test_execute_get_success(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.get.return_value = {
            "notification": [
                {"timestamp": 1, "update": [{"path": "/system/state", "val": "ok"}]}
            ]
        }

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_get("test-host", ["/system/state"])

        assert result["status"] == "success"
        assert result["device"] == "test-host"
        assert len(result["data"]) == 1

    def test_execute_get_error(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.get.side_effect = RuntimeError("connection failed")

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_get("test-host", ["/system/state"])

        assert result["status"] == "error"
        assert "connection failed" in result["error"]


class TestGnmiManagerExecuteSubscribeOnce:
    def test_subscribe_once_success(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2.return_value = iter([
            {"update": {"path": "/state", "val": "up"}},
            {"sync_response": True},
        ])

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_subscribe_once("test-host", ["/state"])

        assert result["status"] == "success"
        assert len(result["data"]) == 1

    def test_subscribe_once_error(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2.side_effect = RuntimeError("subscribe failed")

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_subscribe_once("test-host", ["/state"])

        assert result["status"] == "error"


class TestGnmiManagerExecuteSubscribeStream:
    def test_subscribe_stream_success(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        call_count = 0

        def mock_subscribe(subscribe):
            nonlocal call_count
            for _ in range(3):
                call_count += 1
                yield {"update": {"path": "/state", "val": f"up-{call_count}"}}

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2 = mock_subscribe

        # Patch time.monotonic in the gnmi_connection module's namespace
        with (
            patch.object(mgr, "create_client", return_value=mock_gc),
            patch("network_mcp.gnmi_connection.time") as mock_time,
        ):
            mock_time.monotonic.side_effect = [0.0, 0.1, 0.2, 100.0, 100.0]
            result = mgr.execute_subscribe_stream("test-host", ["/state"], duration=1)

        assert result["status"] == "success"
        assert len(result["data"]) >= 1

    def test_subscribe_stream_error(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2.side_effect = RuntimeError("stream failed")

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_subscribe_stream("test-host", ["/state"], duration=1)

        assert result["status"] == "error"

    def test_subscribe_stream_sync_response_continues(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        def mock_subscribe(subscribe):
            yield {"update": {"path": "/state", "val": "first"}}
            yield {"sync_response": True}
            yield {"update": {"path": "/state", "val": "second"}}

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2 = mock_subscribe

        with (
            patch.object(mgr, "create_client", return_value=mock_gc),
            patch("network_mcp.gnmi_connection.time") as mock_time,
        ):
            mock_time.monotonic.side_effect = [0.0, 0.1, 0.2, 0.3, 100.0, 100.0]
            result = mgr.execute_subscribe_stream("test-host", ["/state"], duration=1)

        assert result["status"] == "success"
        # Should have collected updates and skipped sync_response
        assert len(result["data"]) == 2


class TestGnmiManagerExecuteSet:
    def test_set_with_updates(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.set.return_value = {"response": "ok"}

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_set("test-host", updates=[("/system/hostname", {"hostname": "test"})])

        assert result["status"] == "success"
        mock_gc.set.assert_called_once()

    def test_set_with_deletes(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.set.return_value = {"response": "ok"}

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_set("test-host", deletes=["/system/hostname"])

        assert result["status"] == "success"

    def test_set_no_updates_or_deletes(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)
        result = mgr.execute_set("test-host")
        assert result["status"] == "error"
        assert "No updates or deletes" in result["error"]

    def test_set_error(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.set.side_effect = RuntimeError("set failed")

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_set("test-host", updates=[("/path", {"val": 1})])

        assert result["status"] == "error"


class TestGnmiManagerExecuteCapabilities:
    def test_capabilities_success(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.capabilities.return_value = {
            "gnmi_version": "0.7.0",
            "supported_encodings": ["JSON", "PROTO"],
            "supported_models": [
                {"name": "openconfig-interfaces", "organization": "OpenConfig", "version": "3.0.0"}
            ],
        }

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_capabilities("test-host")

        assert result["status"] == "success"
        assert result["data"]["gnmi_version"] == "0.7.0"
        assert len(result["data"]["supported_models"]) == 1

    def test_capabilities_error(self):
        settings = _make_mock_settings()
        mgr = GnmiManager(settings)

        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.capabilities.side_effect = RuntimeError("caps failed")

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_capabilities("test-host")

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestGnmiConstants:
    def test_not_installed_error(self):
        assert GNMI_NOT_INSTALLED_ERROR["status"] == "error"
        assert "pygnmi" in GNMI_NOT_INSTALLED_ERROR["error"]

    def test_not_enabled_error(self):
        assert GNMI_NOT_ENABLED_ERROR["status"] == "error"
        assert "gNMI" in GNMI_NOT_ENABLED_ERROR["error"]
