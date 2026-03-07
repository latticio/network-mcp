"""Tests for gNMI telemetry tools."""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.gnmi_connection import GNMI_AVAILABLE, GnmiManager, _flatten_notifications, _map_grpc_error
from tests.conftest import make_test_settings

# --- Mock gNMI responses ---

MOCK_GNMI_CAPABILITIES = {
    "gnmi_version": "0.7.0",
    "supported_encodings": ["json", "json_ietf", "proto"],
    "supported_models": [
        {"name": "openconfig-interfaces", "organization": "OpenConfig working group", "version": "2.4.3"},
        {"name": "openconfig-bgp", "organization": "OpenConfig working group", "version": "6.0.0"},
    ],
}

MOCK_GNMI_GET_RESPONSE = {
    "notification": [
        {
            "timestamp": 1700000000000000000,
            "update": [
                {
                    "path": "interfaces/interface[name=Ethernet1]/state/oper-status",
                    "val": "UP",
                },
                {
                    "path": "interfaces/interface[name=Ethernet1]/state/admin-status",
                    "val": "UP",
                },
            ],
        }
    ]
}

MOCK_GNMI_SUBSCRIBE_RESPONSE = [
    {
        "update": {
            "timestamp": 1700000000000000000,
            "update": [
                {
                    "path": "/interfaces/interface[name=Ethernet1]/state/counters/in-octets",
                    "val": 123456789,
                }
            ],
        }
    },
    {"sync_response": True},
]


class TestGnmiManager:
    """Tests for GnmiManager class."""

    def test_init_defaults(self):
        """GnmiManager initializes with default env values."""
        mgr = GnmiManager(settings=make_test_settings())
        assert mgr._default_port == 6030
        assert mgr._default_username == "admin"
        assert mgr._insecure is False

    def test_init_custom_settings(self):
        """GnmiManager respects settings overrides."""
        mgr = GnmiManager(settings=make_test_settings(gnmi_port=6040, gnmi_insecure=False))
        assert mgr._default_port == 6040
        assert mgr._insecure is False

    @patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", False)
    def test_create_client_no_pygnmi(self):
        """create_client raises when pygnmi is not installed."""
        mgr = GnmiManager(settings=make_test_settings())
        with pytest.raises(RuntimeError, match="pygnmi is not installed"):
            mgr.create_client("switch1")


class TestGnmiManagerExecuteMethods:
    """Tests for GnmiManager execute methods with mocked pygnmi."""

    @pytest.fixture
    def gnmi_mgr(self):
        return GnmiManager(settings=make_test_settings())

    @pytest.fixture
    def mock_gc(self):
        """Create a mock gNMIclient that works as a context manager."""
        gc = MagicMock()
        gc.__enter__ = MagicMock(return_value=gc)
        gc.__exit__ = MagicMock(return_value=False)
        return gc

    def test_execute_get_success(self, gnmi_mgr, mock_gc):
        """execute_get returns normalised success dict."""
        mock_gc.get.return_value = MOCK_GNMI_GET_RESPONSE

        with patch.object(gnmi_mgr, "create_client", return_value=mock_gc):
            result = gnmi_mgr.execute_get("switch1", ["/interfaces/interface/state"])

        assert result["status"] == "success"
        assert result["device"] == "switch1"
        assert len(result["data"]) == 2
        assert result["data"][0]["val"] == "UP"

    def test_execute_get_error(self, gnmi_mgr):
        """execute_get returns error dict on exception."""
        with patch.object(gnmi_mgr, "create_client", side_effect=Exception("connection refused")):
            result = gnmi_mgr.execute_get("switch1", ["/interfaces"])

        assert result["status"] == "error"
        assert "connection refused" in result["error"]

    def test_execute_capabilities_success(self, gnmi_mgr, mock_gc):
        """execute_capabilities returns structured capabilities."""
        mock_gc.capabilities.return_value = MOCK_GNMI_CAPABILITIES

        with patch.object(gnmi_mgr, "create_client", return_value=mock_gc):
            result = gnmi_mgr.execute_capabilities("switch1")

        assert result["status"] == "success"
        assert result["data"]["gnmi_version"] == "0.7.0"
        assert len(result["data"]["supported_models"]) == 2
        assert result["data"]["supported_models"][0]["name"] == "openconfig-interfaces"

    def test_execute_subscribe_once_success(self, gnmi_mgr, mock_gc):
        """execute_subscribe_once collects until sync_response."""
        mock_gc.subscribe2.return_value = iter(MOCK_GNMI_SUBSCRIBE_RESPONSE)

        with patch.object(gnmi_mgr, "create_client", return_value=mock_gc):
            result = gnmi_mgr.execute_subscribe_once("switch1", ["/interfaces"])

        assert result["status"] == "success"
        assert len(result["data"]) == 1

    def test_execute_subscribe_once_error(self, gnmi_mgr):
        """execute_subscribe_once returns error on failure."""
        with patch.object(gnmi_mgr, "create_client", side_effect=Exception("timeout")):
            result = gnmi_mgr.execute_subscribe_once("switch1", ["/interfaces"])

        assert result["status"] == "error"
        assert "timeout" in result["error"]


class TestFlattenNotifications:
    """Tests for notification flattening helper."""

    def test_flatten_basic(self):
        """Flatten a standard notification response."""
        result = _flatten_notifications(MOCK_GNMI_GET_RESPONSE)
        assert len(result) == 2
        assert result[0]["val"] == "UP"
        assert result[0]["timestamp"] == 1700000000000000000

    def test_flatten_empty(self):
        """Flatten empty notification."""
        result = _flatten_notifications({})
        assert result == []

    def test_flatten_no_updates(self):
        """Flatten notification with no update key."""
        result = _flatten_notifications({"notification": [{"timestamp": 0}]})
        assert result == []


class TestGrpcErrorMapping:
    """Tests for gRPC error code to user-friendly message mapping."""

    @staticmethod
    def _make_grpc_error(status_code, details=""):
        """Create a mock that passes isinstance(error, grpc.RpcError).

        grpc.RpcError doesn't define .code()/.details() (they come from
        grpc.Call), so we create a dynamic subclass to satisfy isinstance
        while still allowing attribute access.
        """
        import grpc

        class MockRpcError(grpc.RpcError):
            pass

        error = MockRpcError()
        error.code = MagicMock(return_value=status_code)
        error.details = MagicMock(return_value=details)
        return error

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_map_unavailable(self):
        """UNAVAILABLE maps to connectivity message."""
        import grpc

        mock_error = self._make_grpc_error(grpc.StatusCode.UNAVAILABLE, "connection refused")
        msg = _map_grpc_error(mock_error)
        assert "unreachable" in msg.lower() or "not enabled" in msg.lower()

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_map_unauthenticated(self):
        """UNAUTHENTICATED maps to auth failure message."""
        import grpc

        mock_error = self._make_grpc_error(grpc.StatusCode.UNAUTHENTICATED)
        msg = _map_grpc_error(mock_error)
        assert "authentication" in msg.lower()

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_map_not_found(self):
        """NOT_FOUND maps to path not found message."""
        import grpc

        mock_error = self._make_grpc_error(grpc.StatusCode.NOT_FOUND)
        msg = _map_grpc_error(mock_error)
        assert "path not found" in msg.lower()

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_map_deadline_exceeded(self):
        """DEADLINE_EXCEEDED maps to timeout message."""
        import grpc

        mock_error = self._make_grpc_error(grpc.StatusCode.DEADLINE_EXCEEDED)
        msg = _map_grpc_error(mock_error)
        assert "timed out" in msg.lower()

    def test_map_generic_exception(self):
        """Generic exceptions get a reasonable message."""
        msg = _map_grpc_error(ValueError("something broke"))
        assert "something broke" in msg


class TestGnmiToolsIntegration:
    """Tests for the gNMI tool functions (tools/gnmi.py)."""

    @pytest.fixture(autouse=True)
    def _patch_gnmi_mgr(self):
        """Patch the gnmi_mgr and GNMI_AVAILABLE used by the tool module."""
        with (
            patch("network_mcp.tools.gnmi.gnmi_mgr") as mock_mgr,
            patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", True),
        ):
            self.mock_mgr = mock_mgr
            yield

    def test_gnmi_get_success(self):
        """gnmi_get returns data on success."""
        from network_mcp.tools.gnmi import eos_gnmi_get

        self.mock_mgr.execute_get.return_value = {
            "status": "success",
            "device": "switch1",
            "data": [{"path": "/interfaces/interface", "val": "UP"}],
        }

        result = eos_gnmi_get("switch1", ["/interfaces/interface/state"])
        assert result["status"] == "success"

    def test_gnmi_get_validates_paths(self):
        """gnmi_get rejects paths without leading /."""
        from network_mcp.tools.gnmi import eos_gnmi_get

        result = eos_gnmi_get("switch1", ["interfaces/interface"])
        assert result["status"] == "error"
        assert "must start with /" in result["error"]

    def test_gnmi_get_empty_paths(self):
        """gnmi_get rejects empty path list."""
        from network_mcp.tools.gnmi import eos_gnmi_get

        result = eos_gnmi_get("switch1", [])
        assert result["status"] == "error"
        assert "required" in result["error"].lower()

    def test_gnmi_get_validates_host(self):
        """gnmi_get rejects invalid hostnames."""
        from network_mcp.tools.gnmi import eos_gnmi_get

        result = eos_gnmi_get("", ["/interfaces"])
        assert result["status"] == "error"

    def test_gnmi_subscribe_once_success(self):
        """gnmi_subscribe_once returns collected data."""
        from network_mcp.tools.gnmi import eos_gnmi_subscribe_once

        self.mock_mgr.execute_subscribe_once.return_value = {
            "status": "success",
            "device": "switch1",
            "data": [{"timestamp": 123, "update": []}],
        }

        result = eos_gnmi_subscribe_once("switch1", ["/interfaces"])
        assert result["status"] == "success"

    def test_gnmi_subscribe_once_validates_paths(self):
        """gnmi_subscribe_once rejects invalid paths."""
        from network_mcp.tools.gnmi import eos_gnmi_subscribe_once

        result = eos_gnmi_subscribe_once("switch1", ["bad-path"])
        assert result["status"] == "error"
        assert "must start with /" in result["error"]

    def test_gnmi_capabilities_success(self):
        """gnmi_capabilities returns capabilities data."""
        from network_mcp.tools.gnmi import eos_gnmi_capabilities

        self.mock_mgr.execute_capabilities.return_value = {
            "status": "success",
            "device": "switch1",
            "data": {"gnmi_version": "0.7.0"},
        }

        result = eos_gnmi_capabilities("switch1")
        assert result["status"] == "success"
        assert result["data"]["gnmi_version"] == "0.7.0"

    def test_gnmi_capabilities_validates_host(self):
        """gnmi_capabilities rejects invalid hostnames."""
        from network_mcp.tools.gnmi import eos_gnmi_capabilities

        result = eos_gnmi_capabilities("")
        assert result["status"] == "error"


class TestGnmiNotInstalled:
    """Tests for graceful degradation when pygnmi is not installed."""

    def test_gnmi_get_not_installed(self):
        """gnmi_get returns install message when pygnmi is missing."""
        with patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", False):
            from network_mcp.tools.gnmi import eos_gnmi_get

            result = eos_gnmi_get("switch1", ["/interfaces"])
            assert result["status"] == "error"
            assert "pygnmi is not installed" in result["error"]

    def test_gnmi_capabilities_not_installed(self):
        """gnmi_capabilities returns install message when pygnmi is missing."""
        with patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", False):
            from network_mcp.tools.gnmi import eos_gnmi_capabilities

            result = eos_gnmi_capabilities("switch1")
            assert result["status"] == "error"
            assert "pygnmi is not installed" in result["error"]

    def test_gnmi_subscribe_once_not_installed(self):
        """gnmi_subscribe_once returns install message when pygnmi is missing."""
        with patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", False):
            from network_mcp.tools.gnmi import eos_gnmi_subscribe_once

            result = eos_gnmi_subscribe_once("switch1", ["/interfaces"])
            assert result["status"] == "error"
            assert "pygnmi is not installed" in result["error"]
