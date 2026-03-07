"""Tests for the /health HTTP endpoint via httpx AsyncClient.

Tests the GET /health route registered via mcp.custom_route() which is
available when running in streamable-http transport mode.
"""

from unittest.mock import patch

import httpx
import pytest
from starlette.applications import Starlette

import network_mcp.server as srv
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def health_app():
    """Minimal Starlette test app built from the registered custom routes."""
    return Starlette(routes=list(srv.mcp._custom_starlette_routes))


@pytest.fixture()
def _mock_health_response():
    """Fixed response dict returned by get_server_health to isolate the endpoint."""
    return {
        "status": "healthy",
        "version": "8.0.0",
        "uptime_seconds": 123.4,
        "connections": {"active": 1, "pooled": 0, "max": 10},
        "circuit_breakers": {"enabled": True, "open": 0, "devices": []},
        "cache": {"enabled": True, "entries": 3, "hit_rate": 0.75, "memory_mb": 0.1},
        "rate_limiter": {"throttled_total": 0},
        "vendor_calls": {},
        "modules": {
            "loaded": ["device", "interfaces", "vlans"],
            "failed": {},
        },
    }


@pytest.fixture()
def mocked_deps(_mock_health_response):
    """Patch all health endpoint dependencies to avoid real device/cache calls."""
    with (
        patch("network_mcp.observability.get_server_health", return_value=_mock_health_response),
        patch("network_mcp.config.get_safe_config", return_value={"read_only": True}),
        patch.object(conn_mgr, "list_devices", return_value=["spine-01", "leaf-01", "leaf-02"]),
        patch("network_mcp.server._transport_mode", "streamable-http"),
    ):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for GET /health via httpx AsyncClient."""

    async def test_health_returns_200_ok(self, health_app, mocked_deps):
        """GET /health returns HTTP 200 when server is healthy."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_health_response_is_json(self, health_app, mocked_deps):
        """GET /health response body is valid JSON."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert isinstance(data, dict)

    async def test_health_includes_version(self, health_app, mocked_deps):
        """Response includes 'version' key derived from __version__."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    async def test_health_includes_uptime_seconds(self, health_app, mocked_deps):
        """Response includes 'uptime_seconds' as a non-negative number."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0

    async def test_health_includes_connected_device_count(self, health_app, mocked_deps):
        """Response includes 'connected_device_count' matching conn_mgr.list_devices()."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert "connected_device_count" in data
        # mocked_deps patches list_devices to return 3 devices
        assert data["connected_device_count"] == 3

    async def test_health_includes_loaded_modules(self, health_app, mocked_deps):
        """Response includes loaded module names under modules.loaded."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert "modules" in data
        assert "loaded" in data["modules"]
        assert isinstance(data["modules"]["loaded"], list)
        assert len(data["modules"]["loaded"]) > 0

    async def test_health_includes_transport_mode(self, health_app, mocked_deps):
        """Response includes 'transport_mode' set by _transport_mode module variable."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert "transport_mode" in data
        assert data["transport_mode"] == "streamable-http"

    async def test_health_all_required_fields_present(self, health_app, mocked_deps):
        """All task-required fields are present in a single request."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert "version" in data
        assert "uptime_seconds" in data
        assert "connected_device_count" in data
        assert "modules" in data and "loaded" in data["modules"]
        assert "transport_mode" in data

    async def test_health_returns_503_when_degraded(self, health_app):
        """GET /health returns 503 when health status is not 'healthy'."""
        degraded_response = {
            "status": "degraded",
            "version": "8.0.0",
            "uptime_seconds": 10.0,
            "connections": {"active": 0, "pooled": 0, "max": 10},
            "circuit_breakers": {"enabled": True, "open": 1, "devices": ["spine-01"]},
            "cache": {"enabled": True, "entries": 0, "hit_rate": 0.0, "memory_mb": 0.0},
            "rate_limiter": {"throttled_total": 0},
            "vendor_calls": {},
            "modules": {"loaded": ["device"], "failed": {}},
        }
        with (
            patch("network_mcp.observability.get_server_health", return_value=degraded_response),
            patch("network_mcp.config.get_safe_config", return_value={}),
            patch.object(conn_mgr, "list_devices", return_value=[]),
            patch("network_mcp.server._transport_mode", "streamable-http"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=health_app),
                base_url="http://testserver",
            ) as client:
                resp = await client.get("/health")
        assert resp.status_code == 503

    async def test_health_connected_device_count_zero_when_no_inventory(self, health_app, _mock_health_response):
        """connected_device_count is 0 when inventory is empty."""
        with (
            patch("network_mcp.observability.get_server_health", return_value=_mock_health_response),
            patch("network_mcp.config.get_safe_config", return_value={}),
            patch.object(conn_mgr, "list_devices", return_value=[]),
            patch("network_mcp.server._transport_mode", "stdio"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=health_app),
                base_url="http://testserver",
            ) as client:
                resp = await client.get("/health")
        data = resp.json()
        assert data["connected_device_count"] == 0
        assert data["transport_mode"] == "stdio"

    async def test_health_transport_mode_reflects_server_setting(self, health_app, _mock_health_response):
        """transport_mode in response reflects the module-level _transport_mode variable."""
        with (
            patch("network_mcp.observability.get_server_health", return_value=_mock_health_response),
            patch("network_mcp.config.get_safe_config", return_value={}),
            patch.object(conn_mgr, "list_devices", return_value=["leaf-01"]),
            patch("network_mcp.server._transport_mode", "sse"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=health_app),
                base_url="http://testserver",
            ) as client:
                resp = await client.get("/health")
        data = resp.json()
        assert data["transport_mode"] == "sse"


class TestHealthEndpointRouteAvailability:
    """Tests that /health is reachable as a plain HTTP GET route."""

    async def test_health_accepts_get_method(self, health_app, mocked_deps):
        """GET /health is accepted (not 405 Method Not Allowed)."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        assert resp.status_code != 405

    async def test_health_content_type_is_json(self, health_app, mocked_deps):
        """Response Content-Type header indicates JSON."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=health_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/health")
        assert "application/json" in resp.headers.get("content-type", "")
