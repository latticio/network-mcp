"""Tests for ServiceNow integration — incidents, CMDB sync, change validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from network_mcp.integrations.servicenow import (
    ServiceNowClient,
    _get_snow_client,
    net_create_incident,
    net_sync_cmdb,
    net_update_incident,
    net_validate_change,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def snow_settings():
    """Patch settings to have ServiceNow credentials configured."""
    with patch("network_mcp.integrations.servicenow.settings") as mock_settings:
        mock_settings.net_demo_mode = False
        mock_settings.snow_instance = "testcompany"
        mock_settings.snow_username = "admin"
        mock_settings.snow_password = MagicMock()
        mock_settings.snow_password.get_secret_value.return_value = "secret123"
        mock_settings.net_snow_ci_class = "cmdb_ci_ip_switch"
        yield mock_settings


@pytest.fixture
def snow_settings_missing():
    """Patch settings to have no ServiceNow credentials."""
    with patch("network_mcp.integrations.servicenow.settings") as mock_settings:
        mock_settings.net_demo_mode = False
        mock_settings.snow_instance = None
        mock_settings.snow_username = None
        mock_settings.snow_password = None
        yield mock_settings


@pytest.fixture
def mock_snow_transport():
    """Mock httpx transport for ServiceNow API."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method

        # Incident endpoints
        if "/table/incident" in url and method == "POST":
            return httpx.Response(
                200,
                json={"result": {"number": "INC0012345", "sys_id": "abc123"}},
            )
        if "/table/incident/" in url and method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "number": "INC0012345",
                        "sys_id": "abc123",
                        "state": "2",
                    }
                },
            )

        # CMDB endpoints
        if "/table/cmdb_ci_ip_switch" in url and method == "GET":
            return httpx.Response(200, json={"result": []})  # no existing CI
        if "/table/cmdb_ci_ip_switch" in url and method == "POST":
            return httpx.Response(200, json={"result": {"sys_id": "ci123"}})

        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_snow_transport_existing_ci():
    """Mock httpx transport where CI already exists in CMDB."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method

        if "/table/cmdb_ci_ip_switch" in url and method == "GET":
            return httpx.Response(
                200,
                json={"result": [{"sys_id": "existing-ci-456", "name": "spine-01"}]},
            )
        if "/table/cmdb_ci_ip_switch/existing-ci-456" in url and method == "PATCH":
            return httpx.Response(
                200,
                json={"result": {"sys_id": "existing-ci-456", "sys_updated_on": "2025-01-01"}},
            )

        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_snow_transport_auth_failure():
    """Mock httpx transport that returns 401."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(401, json={"error": {"message": "Unauthorized"}})

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_snow_transport_forbidden():
    """Mock httpx transport that returns 403."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(403, json={"error": {"message": "Forbidden"}})

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_snow_transport_rate_limited():
    """Mock httpx transport that returns 429."""

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, json={"error": {"message": "Rate limited"}})

    return httpx.MockTransport(handler)


def _make_snow_client(transport: httpx.MockTransport) -> ServiceNowClient:
    """Create a ServiceNowClient with a mock transport."""
    client = ServiceNowClient("testcompany", "admin", "secret123")
    client._client = httpx.Client(
        base_url=client._base_url,
        auth=("admin", "secret123"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        transport=transport,
        timeout=30.0,
    )
    return client


# ---------------------------------------------------------------------------
# ServiceNowClient unit tests
# ---------------------------------------------------------------------------


class TestServiceNowClient:
    """Tests for the ServiceNowClient class directly."""

    def test_create_incident(self, mock_snow_transport):
        client = _make_snow_client(mock_snow_transport)
        result = client.create_incident("Link down on Ethernet1", "Device spine-01 link down")
        assert result["number"] == "INC0012345"
        assert result["sys_id"] == "abc123"

    def test_create_incident_with_urgency(self, mock_snow_transport):
        client = _make_snow_client(mock_snow_transport)
        result = client.create_incident("Critical BGP failure", "All peers down", urgency=1)
        assert result["number"] == "INC0012345"

    def test_update_incident_work_notes(self, mock_snow_transport):
        client = _make_snow_client(mock_snow_transport)
        result = client.update_incident("abc123", work_notes="Rebooted device")
        assert result["sys_id"] == "abc123"
        assert result["state"] == "2"

    def test_update_incident_state(self, mock_snow_transport):
        client = _make_snow_client(mock_snow_transport)
        result = client.update_incident("abc123", state=6)
        assert result["sys_id"] == "abc123"

    def test_create_ci_new(self, mock_snow_transport):
        client = _make_snow_client(mock_snow_transport)
        result = client.create_or_update_ci(
            "spine-01",
            {"serial_number": "SN123", "os_version": "4.32.1F"},
        )
        assert result["sys_id"] == "ci123"

    def test_update_ci_existing(self, mock_snow_transport_existing_ci):
        client = _make_snow_client(mock_snow_transport_existing_ci)
        result = client.create_or_update_ci(
            "spine-01",
            {"serial_number": "SN123", "os_version": "4.32.1F"},
        )
        assert result["sys_id"] == "existing-ci-456"
        assert result["sys_updated_on"] == "2025-01-01"

    def test_auth_failure(self, mock_snow_transport_auth_failure):
        client = _make_snow_client(mock_snow_transport_auth_failure)
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.create_incident("Test", "Test")
        assert exc_info.value.response.status_code == 401

    def test_forbidden(self, mock_snow_transport_forbidden):
        client = _make_snow_client(mock_snow_transport_forbidden)
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.create_incident("Test", "Test")
        assert exc_info.value.response.status_code == 403

    def test_rate_limited(self, mock_snow_transport_rate_limited):
        client = _make_snow_client(mock_snow_transport_rate_limited)
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.create_incident("Test", "Test")
        assert exc_info.value.response.status_code == 429


# ---------------------------------------------------------------------------
# Incident creation tool tests
# ---------------------------------------------------------------------------


class TestNetCreateIncident:
    """Tests for the net_create_incident MCP tool."""

    def test_create_incident_with_diagnostics(self, snow_settings, mock_snow_transport):
        mock_diag = {"status": "success", "data": {"hostname": "spine-01", "version": "4.32.1F"}}
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            with patch("network_mcp.tools.common.device.net_get_device_info", return_value=mock_diag):
                result = net_create_incident("spine-01", "Link flapping on Ethernet1", include_diagnostics=True)

        assert result["status"] == "success"
        assert result["incident_number"] == "INC0012345"
        assert result["sys_id"] == "abc123"

    def test_create_incident_without_diagnostics(self, snow_settings, mock_snow_transport):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            result = net_create_incident("spine-01", "Manual incident", include_diagnostics=False)

        assert result["status"] == "success"
        assert result["incident_number"] == "INC0012345"

    def test_create_incident_urgency_levels(self, snow_settings, mock_snow_transport):
        for urgency in (1, 2, 3):
            with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
                mock_get_client.return_value = _make_snow_client(mock_snow_transport)
                result = net_create_incident(
                    "spine-01",
                    f"Urgency {urgency} test",
                    urgency=urgency,
                    include_diagnostics=False,
                )
            assert result["status"] == "success"

    def test_create_incident_not_configured(self, snow_settings_missing):
        result = net_create_incident("spine-01", "Test")
        assert result["status"] == "error"
        assert "not configured" in result["error"]
        assert result["device"] == "spine-01"

    def test_create_incident_connect_error(self, snow_settings):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = MagicMock()
            mock_get_client.return_value.create_incident.side_effect = httpx.ConnectError("Connection refused")
            result = net_create_incident("spine-01", "Test", include_diagnostics=False)
        assert result["status"] == "error"
        assert "Cannot connect" in result["error"]

    def test_create_incident_http_error(self, snow_settings, mock_snow_transport_auth_failure):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = _make_snow_client(mock_snow_transport_auth_failure)
            result = net_create_incident("spine-01", "Test", include_diagnostics=False)
        assert result["status"] == "error"
        assert "401" in result["error"]


# ---------------------------------------------------------------------------
# Incident update tool tests
# ---------------------------------------------------------------------------


class TestNetUpdateIncident:
    """Tests for the net_update_incident MCP tool."""

    def test_update_with_work_notes(self, snow_settings, mock_snow_transport):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_client = _make_snow_client(mock_snow_transport)
            mock_get_client.return_value = mock_client
            result = net_update_incident("spine-01", "abc123", work_notes="Rebooted device")

        assert result["status"] == "success"
        assert result["incident_number"] == "INC0012345"

    def test_update_state(self, snow_settings, mock_snow_transport):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            result = net_update_incident("spine-01", "abc123", state=2)

        assert result["status"] == "success"
        assert result["state"] == "2"

    def test_update_no_params(self, snow_settings):
        result = net_update_incident("spine-01", "abc123")
        assert result["status"] == "error"
        assert "At least one" in result["error"]

    def test_update_not_configured(self, snow_settings_missing):
        result = net_update_incident("spine-01", "abc123", work_notes="Test")
        assert result["status"] == "error"
        assert "not configured" in result["error"]

    def test_update_connect_error(self, snow_settings):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = MagicMock()
            mock_get_client.return_value.update_incident.side_effect = httpx.ConnectError("Connection refused")
            result = net_update_incident("spine-01", "abc123", work_notes="Test")
        assert result["status"] == "error"
        assert "Cannot connect" in result["error"]

    def test_update_http_error(self, snow_settings, mock_snow_transport_auth_failure):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = _make_snow_client(mock_snow_transport_auth_failure)
            result = net_update_incident("spine-01", "abc123", work_notes="Test")
        assert result["status"] == "error"
        assert "401" in result["error"]


# ---------------------------------------------------------------------------
# CMDB sync tool tests
# ---------------------------------------------------------------------------


class TestNetSyncCmdb:
    """Tests for the net_sync_cmdb MCP tool."""

    def test_sync_new_device(self, snow_settings, mock_snow_transport):
        mock_facts = {
            "status": "success",
            "data": {
                "hostname": "spine-01",
                "serial_number": "SN123",
                "version": "4.32.1F",
                "model": "DCS-7280SR-48C6",
            },
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.tools.common.device.net_get_device_info", return_value=mock_facts),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            result = net_sync_cmdb("spine-01")

        assert result["status"] == "success"
        assert result["cmdb_sys_id"] == "ci123"
        assert result["action"] == "created"

    def test_sync_existing_device(self, snow_settings, mock_snow_transport_existing_ci):
        mock_facts = {
            "status": "success",
            "data": {
                "hostname": "spine-01",
                "serial_number": "SN123",
                "version": "4.32.1F",
                "model": "DCS-7280SR-48C6",
            },
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.tools.common.device.net_get_device_info", return_value=mock_facts),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport_existing_ci)
            result = net_sync_cmdb("spine-01")

        assert result["status"] == "success"
        assert result["cmdb_sys_id"] == "existing-ci-456"
        assert result["action"] == "updated"

    def test_sync_device_facts_fail(self, snow_settings, mock_snow_transport):
        mock_facts = {"status": "error", "device": "spine-01", "error": "Connection refused"}
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.tools.common.device.net_get_device_info", return_value=mock_facts),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            result = net_sync_cmdb("spine-01")

        assert result["status"] == "error"
        assert "Connection refused" in result["error"]

    def test_sync_not_configured(self, snow_settings_missing):
        result = net_sync_cmdb("spine-01")
        assert result["status"] == "error"
        assert "not configured" in result["error"]

    def test_sync_connect_error(self, snow_settings):
        mock_facts = {
            "status": "success",
            "data": {"hostname": "spine-01", "serial_number": "SN123", "version": "4.32.1F", "model": "vEOS"},
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.tools.common.device.net_get_device_info", return_value=mock_facts),
        ):
            mock_client = MagicMock()
            mock_client.create_or_update_ci.side_effect = httpx.ConnectError("Connection refused")
            mock_get_client.return_value = mock_client
            result = net_sync_cmdb("spine-01")

        assert result["status"] == "error"
        assert "Cannot connect" in result["error"]


# ---------------------------------------------------------------------------
# Change validation tool tests
# ---------------------------------------------------------------------------


class TestNetValidateChange:
    """Tests for the net_validate_change MCP tool."""

    def test_pre_snapshot(self, snow_settings, mock_snow_transport):
        mock_show_result = {
            "status": "success",
            "device": "spine-01",
            "data": [{"interfaces": {"Ethernet1": {"linkStatus": "connected"}}}],
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.integrations.servicenow.run_show_command", return_value=mock_show_result),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            result = net_validate_change(
                "spine-01",
                "CHG0001234",
                pre_snapshot_commands=["show interfaces status"],
            )

        assert result["status"] == "success"
        assert result["phase"] == "pre"
        assert result["change_sys_id"] == "CHG0001234"
        assert "show interfaces status" in result["snapshot"]

    def test_post_snapshot(self, snow_settings, mock_snow_transport):
        mock_show_result = {
            "status": "success",
            "device": "spine-01",
            "data": [{"interfaces": {"Ethernet1": {"linkStatus": "connected"}}}],
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.integrations.servicenow.run_show_command", return_value=mock_show_result),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            result = net_validate_change(
                "spine-01",
                "CHG0001234",
                post_snapshot_commands=["show interfaces status"],
            )

        assert result["status"] == "success"
        assert result["phase"] == "post"
        assert result["diff_summary"] is not None
        assert "show interfaces status" in result["diff_summary"]

    def test_post_snapshot_with_diff(self, snow_settings, mock_snow_transport):
        mock_show_result = {
            "status": "success",
            "device": "spine-01",
            "data": [
                {"vlans": {"100": {"name": "SERVERS"}, "200": {"name": "NEW-VLAN"}}},
                {"interfaces": {"Ethernet1": {"linkStatus": "connected"}}},
            ],
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.integrations.servicenow.run_show_command", return_value=mock_show_result),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            result = net_validate_change(
                "spine-01",
                "CHG0001234",
                post_snapshot_commands=["show vlan", "show interfaces status"],
            )

        assert result["status"] == "success"
        assert result["phase"] == "post"
        assert "show vlan" in result["diff_summary"]
        assert "show interfaces status" in result["diff_summary"]

    def test_no_commands_provided(self, snow_settings):
        result = net_validate_change("spine-01", "CHG0001234")
        assert result["status"] == "error"
        assert "At least one" in result["error"]

    def test_not_configured(self, snow_settings_missing):
        result = net_validate_change("spine-01", "CHG0001234", pre_snapshot_commands=["show version"])
        assert result["status"] == "error"
        assert "not configured" in result["error"]

    def test_show_command_fails(self, snow_settings, mock_snow_transport):
        mock_show_result = {"status": "error", "device": "spine-01", "error": "Connection refused"}
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.integrations.servicenow.run_show_command", return_value=mock_show_result),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport)
            result = net_validate_change(
                "spine-01",
                "CHG0001234",
                pre_snapshot_commands=["show version"],
            )

        assert result["status"] == "error"
        assert "Connection refused" in result["error"]

    def test_snow_update_fails_on_pre(self, snow_settings, mock_snow_transport_auth_failure):
        mock_show_result = {
            "status": "success",
            "device": "spine-01",
            "data": [{"version": "4.32.1F"}],
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.integrations.servicenow.run_show_command", return_value=mock_show_result),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport_auth_failure)
            result = net_validate_change(
                "spine-01",
                "CHG0001234",
                pre_snapshot_commands=["show version"],
            )

        assert result["status"] == "error"
        assert "401" in result["error"]

    def test_connect_error(self, snow_settings):
        mock_show_result = {
            "status": "success",
            "device": "spine-01",
            "data": [{"version": "4.32.1F"}],
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.integrations.servicenow.run_show_command", return_value=mock_show_result),
        ):
            mock_client = MagicMock()
            mock_client.update_incident.side_effect = httpx.ConnectError("Connection refused")
            mock_get_client.return_value = mock_client
            result = net_validate_change(
                "spine-01",
                "CHG0001234",
                pre_snapshot_commands=["show version"],
            )

        assert result["status"] == "error"
        assert "Cannot connect" in result["error"]


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling across all ServiceNow tools."""

    def test_rate_limited_incident_create(self, snow_settings, mock_snow_transport_rate_limited):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = _make_snow_client(mock_snow_transport_rate_limited)
            result = net_create_incident("spine-01", "Test", include_diagnostics=False)
        assert result["status"] == "error"
        assert "429" in result["error"]

    def test_rate_limited_cmdb_sync(self, snow_settings, mock_snow_transport_rate_limited):
        mock_facts = {
            "status": "success",
            "data": {"hostname": "spine-01", "serial_number": "SN123", "version": "4.32.1F", "model": "vEOS"},
        }
        with (
            patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client,
            patch("network_mcp.tools.common.device.net_get_device_info", return_value=mock_facts),
        ):
            mock_get_client.return_value = _make_snow_client(mock_snow_transport_rate_limited)
            result = net_sync_cmdb("spine-01")
        assert result["status"] == "error"
        assert "429" in result["error"]

    def test_rate_limited_update_incident(self, snow_settings, mock_snow_transport_rate_limited):
        with patch("network_mcp.integrations.servicenow._get_snow_client") as mock_get_client:
            mock_get_client.return_value = _make_snow_client(mock_snow_transport_rate_limited)
            result = net_update_incident("spine-01", "abc123", work_notes="Test")
        assert result["status"] == "error"
        assert "429" in result["error"]


# ---------------------------------------------------------------------------
# Config / client creation tests
# ---------------------------------------------------------------------------


class TestConfig:
    """Tests for ServiceNow configuration and client creation."""

    def test_get_snow_client_missing_instance(self):
        with patch("network_mcp.integrations.servicenow.settings") as mock_settings:
            mock_settings.snow_instance = None
            mock_settings.snow_username = "admin"
            mock_settings.snow_password = MagicMock()
            with pytest.raises(RuntimeError, match="SNOW_INSTANCE"):
                _get_snow_client()

    def test_get_snow_client_missing_username(self):
        with patch("network_mcp.integrations.servicenow.settings") as mock_settings:
            mock_settings.snow_instance = "testcompany"
            mock_settings.snow_username = None
            mock_settings.snow_password = MagicMock()
            with pytest.raises(RuntimeError, match="SNOW_INSTANCE"):
                _get_snow_client()

    def test_get_snow_client_missing_password(self):
        with patch("network_mcp.integrations.servicenow.settings") as mock_settings:
            mock_settings.snow_instance = "testcompany"
            mock_settings.snow_username = "admin"
            mock_settings.snow_password = None
            with pytest.raises(RuntimeError, match="SNOW_INSTANCE"):
                _get_snow_client()

    def test_get_snow_client_all_present(self):
        with patch("network_mcp.integrations.servicenow.settings") as mock_settings:
            mock_settings.snow_instance = "testcompany"
            mock_settings.snow_username = "admin"
            mock_settings.snow_password = MagicMock()
            mock_settings.snow_password.get_secret_value.return_value = "secret123"
            client = _get_snow_client()
        assert isinstance(client, ServiceNowClient)
        assert "testcompany.service-now.com" in client._base_url

    def test_client_base_url_format(self):
        client = ServiceNowClient("mycompany", "admin", "pass")
        assert client._base_url == "https://mycompany.service-now.com/api/now"
