"""Tests for enhanced gNMI operations: subscribe stream and Set."""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.gnmi_connection import GNMI_AVAILABLE, GnmiManager
from tests.conftest import make_test_settings

# --- Mock responses ---

MOCK_STREAM_RESPONSES = [
    {
        "update": {
            "timestamp": 1700000000000000000,
            "update": [{"path": "/interfaces/interface[name=Ethernet1]/state/counters/in-octets", "val": 100}],
        }
    },
    {
        "update": {
            "timestamp": 1700000001000000000,
            "update": [{"path": "/interfaces/interface[name=Ethernet1]/state/counters/in-octets", "val": 200}],
        }
    },
]

MOCK_SET_RESPONSE = {
    "timestamp": 1700000000000000000,
    "response": [{"op": "UPDATE", "path": "/interfaces/interface[name=Ethernet1]/config/description"}],
}


# --- GnmiManager.execute_subscribe_stream tests ---


class TestGnmiSubscribeStream:
    """Tests for GnmiManager.execute_subscribe_stream."""

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_subscribe_stream_success(self):
        mgr = GnmiManager(settings=make_test_settings())
        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2.return_value = iter(MOCK_STREAM_RESPONSES)

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_subscribe_stream("switch1", ["/interfaces/interface"], duration=1)

        assert result["status"] == "success"
        assert result["device"] == "switch1"
        assert isinstance(result["data"], list)
        assert "duration_seconds" in result

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_subscribe_stream_caps_duration_at_timeout(self):
        settings = make_test_settings()
        mgr = GnmiManager(settings=settings)
        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2.return_value = iter([])

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_subscribe_stream("switch1", ["/system/state"], duration=999)

        assert result["status"] == "success"
        # Duration should be capped, not 999
        assert result["duration_seconds"] <= settings.gnmi_timeout + 1

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_subscribe_stream_error(self):
        mgr = GnmiManager(settings=make_test_settings())
        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2.side_effect = Exception("connection failed")

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_subscribe_stream("switch1", ["/interfaces/interface"])

        assert result["status"] == "error"
        assert "connection failed" in result["error"]

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_subscribe_stream_empty(self):
        mgr = GnmiManager(settings=make_test_settings())
        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.subscribe2.return_value = iter([])

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_subscribe_stream("switch1", ["/interfaces/interface"], duration=1)

        assert result["status"] == "success"
        assert result["data"] == []


# --- GnmiManager.execute_set tests ---


class TestGnmiSet:
    """Tests for GnmiManager.execute_set."""

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_set_update_success(self):
        mgr = GnmiManager(settings=make_test_settings())
        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.set.return_value = MOCK_SET_RESPONSE

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_set(
                "switch1",
                updates=[("/interfaces/interface[name=Ethernet1]/config/description", {"val": "uplink"})],
            )

        assert result["status"] == "success"
        mock_gc.set.assert_called_once()

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_set_delete_success(self):
        mgr = GnmiManager(settings=make_test_settings())
        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.set.return_value = MOCK_SET_RESPONSE

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_set("switch1", deletes=["/interfaces/interface[name=Ethernet1]/config/description"])

        assert result["status"] == "success"
        mock_gc.set.assert_called_once()

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_set_no_operations(self):
        mgr = GnmiManager(settings=make_test_settings())
        result = mgr.execute_set("switch1")
        assert result["status"] == "error"
        assert "No updates or deletes" in result["error"]

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_set_error(self):
        mgr = GnmiManager(settings=make_test_settings())
        mock_gc = MagicMock()
        mock_gc.__enter__ = MagicMock(return_value=mock_gc)
        mock_gc.__exit__ = MagicMock(return_value=False)
        mock_gc.set.side_effect = Exception("permission denied")

        with patch.object(mgr, "create_client", return_value=mock_gc):
            result = mgr.execute_set("switch1", updates=[("/system/config/hostname", {"val": "newname"})])

        assert result["status"] == "error"
        assert "permission denied" in result["error"]


# --- Tool function tests ---


class TestGnmiSubscribeStreamTool:
    """Tests for eos_gnmi_subscribe_stream tool function."""

    @pytest.fixture(autouse=True)
    def _patch_gnmi(self):
        with (
            patch("network_mcp.tools.gnmi.gnmi_mgr") as mock_mgr,
            patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", True),
        ):
            self.mock_mgr = mock_mgr
            yield

    def test_success(self):
        from network_mcp.tools.gnmi import eos_gnmi_subscribe_stream

        self.mock_mgr.execute_subscribe_stream.return_value = {
            "status": "success",
            "device": "switch1",
            "data": [{"update": "data"}],
            "duration_seconds": 5.0,
        }
        result = eos_gnmi_subscribe_stream("switch1", ["/interfaces/interface"])
        assert result["status"] == "success"

    def test_validates_paths(self):
        from network_mcp.tools.gnmi import eos_gnmi_subscribe_stream

        result = eos_gnmi_subscribe_stream("switch1", ["no-leading-slash"])
        assert result["status"] == "error"
        assert "must start with /" in result["error"]

    def test_validates_empty_paths(self):
        from network_mcp.tools.gnmi import eos_gnmi_subscribe_stream

        result = eos_gnmi_subscribe_stream("switch1", [])
        assert result["status"] == "error"

    def test_validates_host(self):
        from network_mcp.tools.gnmi import eos_gnmi_subscribe_stream

        result = eos_gnmi_subscribe_stream("  ", ["/interfaces/interface"])
        assert result["status"] == "error"

    def test_negative_duration(self):
        from network_mcp.tools.gnmi import eos_gnmi_subscribe_stream

        result = eos_gnmi_subscribe_stream("switch1", ["/interfaces/interface"], duration=-1)
        assert result["status"] == "error"
        assert "positive" in result["error"].lower()

    def test_not_installed(self):
        with patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", False):
            from network_mcp.tools.gnmi import eos_gnmi_subscribe_stream

            result = eos_gnmi_subscribe_stream("switch1", ["/interfaces/interface"])
            assert result["status"] == "error"
            assert "not installed" in result["error"].lower()


class TestGnmiSetTool:
    """Tests for eos_gnmi_set tool function."""

    @pytest.fixture(autouse=True)
    def _patch_gnmi(self):
        with (
            patch("network_mcp.tools.gnmi.gnmi_mgr") as mock_mgr,
            patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", True),
            patch("network_mcp.tools.gnmi.check_read_only", return_value=None),
        ):
            self.mock_mgr = mock_mgr
            yield

    def test_success(self):
        from network_mcp.tools.gnmi import eos_gnmi_set

        self.mock_mgr.execute_set.return_value = {
            "status": "success",
            "device": "switch1",
            "data": MOCK_SET_RESPONSE,
        }
        result = eos_gnmi_set("switch1", updates=[{"path": "/system/config/hostname", "val": "newname"}])
        assert result["status"] == "success"

    def test_read_only_blocked(self):
        with (
            patch("network_mcp.tools.gnmi.gnmi_mgr"),
            patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", True),
            patch("network_mcp.tools.gnmi.check_read_only", return_value="Write operations blocked"),
        ):
            from network_mcp.tools.gnmi import eos_gnmi_set

            result = eos_gnmi_set("switch1", updates=[{"path": "/system/config/hostname", "val": "x"}])
            assert result["status"] == "error"
            assert "blocked" in result["error"].lower() or "write" in result["error"].lower()

    def test_validates_paths(self):
        from network_mcp.tools.gnmi import eos_gnmi_set

        result = eos_gnmi_set("switch1", updates=[{"path": "no-slash", "val": "test"}])
        assert result["status"] == "error"
        assert "must start with /" in result["error"]

    def test_validates_delete_paths(self):
        from network_mcp.tools.gnmi import eos_gnmi_set

        result = eos_gnmi_set("switch1", deletes=["no-slash"])
        assert result["status"] == "error"
        assert "must start with /" in result["error"]

    def test_no_operations(self):
        from network_mcp.tools.gnmi import eos_gnmi_set

        result = eos_gnmi_set("switch1")
        assert result["status"] == "error"
        assert "required" in result["error"].lower()

    def test_not_installed(self):
        with (
            patch("network_mcp.tools.gnmi.GNMI_AVAILABLE", False),
            patch("network_mcp.tools.gnmi.check_read_only", return_value=None),
        ):
            from network_mcp.tools.gnmi import eos_gnmi_set

            result = eos_gnmi_set("switch1", updates=[{"path": "/test", "val": "x"}])
            assert result["status"] == "error"
            assert "not installed" in result["error"].lower()
