"""Tests for CloudVision Portal integration tools."""

import json
from unittest.mock import MagicMock, patch

import pytest

# --- Mock CVP API responses ---

MOCK_CVP_INVENTORY = {
    "result": [
        {
            "value": {
                "hostname": "spine-01",
                "ipAddress": "10.0.0.1",
                "modelName": "DCS-7050SX3-48YC12",
                "softwareVersion": "4.32.1F",
                "serialNumber": "ABC123",
                "streamingStatus": "active",
            }
        },
        {
            "value": {
                "hostname": "leaf-01",
                "ipAddress": "10.0.0.2",
                "modelName": "DCS-7280SR-48C6",
                "softwareVersion": "4.31.2F",
                "serialNumber": "DEF456",
                "streamingStatus": "active",
            }
        },
    ]
}

MOCK_CVP_EVENTS = {
    "result": [
        {
            "value": {
                "title": "BGP session down",
                "severity": "CRITICAL",
                "deviceId": "spine-01",
                "timestamp": "2026-02-25T10:30:00Z",
            }
        },
        {
            "value": {
                "title": "Interface flap",
                "severity": "WARNING",
                "deviceId": "leaf-01",
                "timestamp": "2026-02-25T10:31:00Z",
            }
        },
        {
            "value": {
                "title": "Config saved",
                "severity": "INFO",
                "deviceId": "spine-01",
                "timestamp": "2026-02-25T10:32:00Z",
            }
        },
    ]
}


_URLOPEN = "network_mcp.tools.cloudvision.urllib.request.urlopen"


def _mock_urlopen(data: dict):
    """Create a mock urlopen context manager that returns JSON data."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(data).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


class TestGetCvpInventory:
    """Tests for eos_get_cvp_inventory."""

    @pytest.fixture(autouse=True)
    def _patch_settings(self):
        from pydantic import SecretStr

        with (
            patch("network_mcp.tools.cloudvision.settings") as mock_settings,
        ):
            mock_settings.eos_cvp_url = "https://cvp.example.com"
            mock_settings.eos_cvp_token = SecretStr("test-token")
            mock_settings.timeout_connect = 10.0
            self.mock_settings = mock_settings
            yield

    def test_success_all_devices(self):
        from network_mcp.tools.cloudvision import eos_get_cvp_inventory

        with patch(_URLOPEN, return_value=_mock_urlopen(MOCK_CVP_INVENTORY)):
            result = eos_get_cvp_inventory()

        assert result["status"] == "success"
        assert result["data"]["total"] == 2
        assert len(result["data"]["devices"]) == 2
        assert result["data"]["devices"][0]["hostname"] == "spine-01"

    def test_success_filtered_by_host(self):
        from network_mcp.tools.cloudvision import eos_get_cvp_inventory

        with patch(_URLOPEN, return_value=_mock_urlopen(MOCK_CVP_INVENTORY)):
            result = eos_get_cvp_inventory(host="spine-01")

        assert result["status"] == "success"
        assert result["data"]["total"] == 1
        assert result["data"]["devices"][0]["hostname"] == "spine-01"

    def test_not_configured(self):
        from network_mcp.tools.cloudvision import eos_get_cvp_inventory

        self.mock_settings.eos_cvp_url = None
        self.mock_settings.eos_cvp_token = None
        result = eos_get_cvp_inventory()
        assert result["status"] == "error"
        assert "not configured" in result["error"].lower()

    def test_network_error(self):
        import urllib.error

        from network_mcp.tools.cloudvision import eos_get_cvp_inventory

        with patch(
            "network_mcp.tools.cloudvision.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = eos_get_cvp_inventory()

        assert result["status"] == "error"
        assert "failed to connect" in result["error"].lower()


class TestGetCvpEvents:
    """Tests for eos_get_cvp_events."""

    @pytest.fixture(autouse=True)
    def _patch_settings(self):
        from pydantic import SecretStr

        with (
            patch("network_mcp.tools.cloudvision.settings") as mock_settings,
        ):
            mock_settings.eos_cvp_url = "https://cvp.example.com"
            mock_settings.eos_cvp_token = SecretStr("test-token")
            mock_settings.timeout_connect = 10.0
            self.mock_settings = mock_settings
            yield

    def test_success_all_events(self):
        from network_mcp.tools.cloudvision import eos_get_cvp_events

        with patch(_URLOPEN, return_value=_mock_urlopen(MOCK_CVP_EVENTS)):
            result = eos_get_cvp_events()

        assert result["status"] == "success"
        assert result["data"]["total"] == 3

    def test_severity_filter(self):
        from network_mcp.tools.cloudvision import eos_get_cvp_events

        with patch(_URLOPEN, return_value=_mock_urlopen(MOCK_CVP_EVENTS)):
            result = eos_get_cvp_events(severity="CRITICAL")

        assert result["status"] == "success"
        assert result["data"]["total"] == 1
        assert result["data"]["events"][0]["title"] == "BGP session down"

    def test_host_filter(self):
        from network_mcp.tools.cloudvision import eos_get_cvp_events

        with patch(_URLOPEN, return_value=_mock_urlopen(MOCK_CVP_EVENTS)):
            result = eos_get_cvp_events(host="leaf-01")

        assert result["status"] == "success"
        assert result["data"]["total"] == 1
        assert result["data"]["events"][0]["device"] == "leaf-01"

    def test_invalid_severity(self):
        from network_mcp.tools.cloudvision import eos_get_cvp_events

        result = eos_get_cvp_events(severity="INVALID")
        assert result["status"] == "error"
        assert "invalid severity" in result["error"].lower()

    def test_not_configured(self):
        from network_mcp.tools.cloudvision import eos_get_cvp_events

        self.mock_settings.eos_cvp_url = None
        self.mock_settings.eos_cvp_token = None
        result = eos_get_cvp_events()
        assert result["status"] == "error"
        assert "not configured" in result["error"].lower()

    def test_network_error(self):
        import urllib.error

        from network_mcp.tools.cloudvision import eos_get_cvp_events

        with patch(
            "network_mcp.tools.cloudvision.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            result = eos_get_cvp_events()

        assert result["status"] == "error"
        assert "failed to connect" in result["error"].lower()
