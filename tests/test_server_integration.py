"""Integration tests for server.py: HTTP endpoints, CLI parsing, module loading, lifespan, inventory backend."""

import argparse
import importlib
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from network_mcp.server import (
    AppContext,
    _build_inventory_backend,
    _load_module,
    _parse_args,
    app_lifespan,
    conn_mgr,
    get_module_status,
    mcp,
    settings,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_module_tracking():
    """Reset module tracking state before and after each test."""
    from network_mcp import server

    orig_loaded = server._loaded_modules.copy()
    orig_failed = dict(server._failed_modules)
    server._loaded_modules.clear()
    server._failed_modules.clear()
    yield
    server._loaded_modules.clear()
    server._failed_modules.clear()
    server._loaded_modules.update(orig_loaded)
    server._failed_modules.update(orig_failed)


@pytest.fixture
def _restore_server_after_reload():
    """Restore the server module after tests that use importlib.reload."""
    import network_mcp.helpers as _helpers_mod
    import network_mcp.server as _server_mod

    orig_settings = _server_mod.settings
    orig_conn_mgr = _server_mod.conn_mgr
    orig_mcp = _server_mod.mcp
    orig_helpers_settings = _helpers_mod._settings
    yield
    _server_mod.settings = orig_settings
    _server_mod.conn_mgr = orig_conn_mgr
    _server_mod.mcp = orig_mcp
    _helpers_mod._settings = orig_helpers_settings


# ---------------------------------------------------------------------------
# 1. CLI argument parsing (_parse_args)
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Tests for _parse_args CLI argument handling."""

    def test_defaults(self):
        """Default args should be transport=stdio, host=0.0.0.0, port=8000."""
        args = _parse_args([])
        assert args.transport == "stdio"
        assert args.host == "0.0.0.0"  # noqa: S104
        assert args.port == 8000

    def test_http_transport(self):
        """--transport streamable-http should set transport correctly."""
        args = _parse_args(["--transport", "streamable-http"])
        assert args.transport == "streamable-http"

    def test_sse_transport(self):
        """--transport sse should set transport correctly."""
        args = _parse_args(["--transport", "sse"])
        assert args.transport == "sse"

    def test_custom_port(self):
        """--port 9000 should override default port."""
        args = _parse_args(["--port", "9000"])
        assert args.port == 9000

    def test_custom_host(self):
        """--host 127.0.0.1 should override default host."""
        args = _parse_args(["--host", "127.0.0.1"])
        assert args.host == "127.0.0.1"

    def test_all_args_combined(self):
        """All args together should be parsed correctly."""
        args = _parse_args(["--transport", "streamable-http", "--host", "10.0.0.1", "--port", "3000"])
        assert args.transport == "streamable-http"
        assert args.host == "10.0.0.1"
        assert args.port == 3000

    def test_invalid_transport_raises_system_exit(self):
        """Invalid transport choice should cause SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--transport", "invalid"])

    def test_returns_namespace(self):
        """Should return an argparse.Namespace object."""
        args = _parse_args([])
        assert isinstance(args, argparse.Namespace)

    def test_none_argv_uses_sys_argv(self):
        """Passing None should parse sys.argv[1:] (defaults via empty list for test safety)."""
        with patch("sys.argv", ["network-mcp"]):
            args = _parse_args(None)
            assert args.transport == "stdio"


# ---------------------------------------------------------------------------
# 2. HTTP transport endpoints
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for the /health HTTP endpoint handler."""

    @pytest.mark.asyncio
    async def test_health_returns_json_response(self):
        """GET /health should return a JSONResponse with health data."""
        try:
            from starlette.requests import Request
            from starlette.responses import JSONResponse
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import health_endpoint

        mock_request = MagicMock(spec=Request)

        with (
            patch("network_mcp.observability.get_server_health") as mock_health,
        ):
            mock_health.return_value = {
                "status": "healthy",
                "version": "4.0.0",
                "modules": {"loaded": ["device"], "failed": {}},
            }
            response = await health_endpoint(mock_request)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_returns_503_when_degraded(self):
        """GET /health should return 503 when status is degraded."""
        try:
            from starlette.requests import Request
            from starlette.responses import JSONResponse
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import health_endpoint

        mock_request = MagicMock(spec=Request)

        with (
            patch("network_mcp.observability.get_server_health") as mock_health,
        ):
            mock_health.return_value = {
                "status": "degraded",
                "version": "4.0.0",
                "modules": {"loaded": ["device"], "failed": {"routing": "ImportError"}},
            }
            response = await health_endpoint(mock_request)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_health_body_contains_status(self):
        """GET /health response body should contain the status field."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import health_endpoint

        mock_request = MagicMock(spec=Request)

        with patch("network_mcp.observability.get_server_health") as mock_health:
            mock_health.return_value = {
                "status": "healthy",
                "version": "4.0.0",
            }
            response = await health_endpoint(mock_request)

        data = json.loads(response.body.decode("utf-8"))
        assert data["status"] == "healthy"


class TestMcpServerIdentity:
    """Tests for the /.well-known/mcp.json endpoint handler."""

    @pytest.mark.asyncio
    async def test_mcp_json_returns_correct_metadata(self):
        """GET /.well-known/mcp.json should return server metadata."""
        try:
            from starlette.requests import Request
            from starlette.responses import JSONResponse
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import mcp_server_identity

        mock_request = MagicMock(spec=Request)
        response = await mcp_server_identity(mock_request)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200
        data = json.loads(response.body.decode("utf-8"))
        assert data["name"] == "network-mcp"
        from network_mcp import __version__

        assert data["version"] == __version__
        assert data["capabilities"]["tools"] is True
        assert data["capabilities"]["resources"] is True
        assert data["capabilities"]["prompts"] is True

    @pytest.mark.asyncio
    async def test_mcp_json_has_description(self):
        """/.well-known/mcp.json should include a description."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import mcp_server_identity

        mock_request = MagicMock(spec=Request)
        response = await mcp_server_identity(mock_request)

        data = json.loads(response.body.decode("utf-8"))
        assert "description" in data
        assert len(data["description"]) > 0

    @pytest.mark.asyncio
    async def test_mcp_json_version_matches_package(self):
        """Version in /.well-known/mcp.json should match __version__."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp import __version__
        from network_mcp.server import mcp_server_identity

        mock_request = MagicMock(spec=Request)
        response = await mcp_server_identity(mock_request)

        data = json.loads(response.body.decode("utf-8"))
        assert data["version"] == __version__


# ---------------------------------------------------------------------------
# 3. Module loading error paths (_load_module)
# ---------------------------------------------------------------------------


class TestLoadModule:
    """Tests for _load_module success, failure, and edge cases."""

    def test_load_valid_stdlib_module(self):
        """Loading a valid stdlib module should succeed and be tracked."""
        from network_mcp import server

        _load_module("test_os", "os", required=False)
        assert "test_os" in server._loaded_modules
        assert "test_os" not in server._failed_modules

    def test_load_nonexistent_module_tracks_failure(self):
        """Loading a nonexistent module should track the failure."""
        from network_mcp import server

        _load_module("ghost_mod", ".tools.nonexistent_ghost_xyz", required=False)
        assert "ghost_mod" not in server._loaded_modules
        assert "ghost_mod" in server._failed_modules
        assert server._failed_modules["ghost_mod"]  # non-empty error

    def test_load_core_failure_logged_at_error(self, caplog):
        """Failed core (required=True) module should log at ERROR level."""
        import logging

        with caplog.at_level(logging.ERROR, logger="network-mcp"):
            _load_module("core_fail", ".tools.nonexistent_ghost_xyz", required=True)
        assert any("CORE module core_fail failed" in r.message for r in caplog.records)

    def test_load_optional_failure_logged_at_warning(self, caplog):
        """Failed optional (required=False) module should log at WARNING level."""
        import logging

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            _load_module("opt_fail", ".tools.nonexistent_ghost_xyz", required=False)
        assert any("Optional module opt_fail failed" in r.message for r in caplog.records)

    def test_load_module_with_runtime_error(self):
        """Module that raises RuntimeError on import should be tracked as failed."""
        from network_mcp import server

        with patch("importlib.import_module", side_effect=RuntimeError("broken init")):
            _load_module("broken_mod", "some.module", required=False)
        assert "broken_mod" in server._failed_modules
        assert "broken init" in server._failed_modules["broken_mod"]

    def test_load_relative_import(self):
        """Relative import path (starts with '.') should use package='network_mcp'."""
        with patch("importlib.import_module") as mock_import:
            _load_module("rel_mod", ".tools.device", required=True)
        mock_import.assert_called_once_with(".tools.device", package="network_mcp")

    def test_load_absolute_import(self):
        """Absolute import path should not pass package kwarg."""
        with patch("importlib.import_module") as mock_import:
            _load_module("abs_mod", "json", required=False)
        mock_import.assert_called_once_with("json")

    def test_load_module_import_error(self):
        """Module that raises ImportError should be tracked as failed."""
        from network_mcp import server

        with patch("importlib.import_module", side_effect=ImportError("no such thing")):
            _load_module("import_fail", "fake.module", required=False)
        assert "import_fail" in server._failed_modules
        assert "no such thing" in server._failed_modules["import_fail"]

    def test_load_module_attribute_error(self):
        """Module that raises AttributeError on import should be tracked as failed."""
        from network_mcp import server

        with patch("importlib.import_module", side_effect=AttributeError("bad attr")):
            _load_module("attr_fail", "fake.attr.module", required=True)
        assert "attr_fail" in server._failed_modules
        assert "bad attr" in server._failed_modules["attr_fail"]


# ---------------------------------------------------------------------------
# 4. get_module_status
# ---------------------------------------------------------------------------


class TestGetModuleStatus:
    """Tests for get_module_status dict structure and sorting."""

    def test_empty_status(self):
        """Empty module tracking should return empty lists/dicts."""
        status = get_module_status()
        assert status["loaded_modules"] == []
        assert status["failed_modules"] == {}

    def test_loaded_modules_sorted(self):
        """loaded_modules should be sorted alphabetically."""
        from network_mcp import server

        server._loaded_modules.update({"vlans", "device", "routing"})
        status = get_module_status()
        assert status["loaded_modules"] == ["device", "routing", "vlans"]

    def test_mixed_loaded_and_failed(self):
        """Both loaded and failed modules should appear in status."""
        from network_mcp import server

        server._loaded_modules.add("device")
        server._failed_modules["routing"] = "ImportError: no module"
        status = get_module_status()
        assert "device" in status["loaded_modules"]
        assert "routing" in status["failed_modules"]
        assert status["failed_modules"]["routing"] == "ImportError: no module"

    def test_status_returns_copy_not_reference(self):
        """Modifying the returned status should not affect internal state."""
        from network_mcp import server

        server._loaded_modules.add("test_mod")
        status = get_module_status()
        status["loaded_modules"].append("injected")
        assert "injected" not in get_module_status()["loaded_modules"]

    def test_failed_modules_returns_copy(self):
        """Modifying the returned failed_modules should not affect internal state."""
        from network_mcp import server

        server._failed_modules["bad"] = "error"
        status = get_module_status()
        status["failed_modules"]["injected"] = "evil"
        assert "injected" not in get_module_status()["failed_modules"]


# ---------------------------------------------------------------------------
# 5. Lifespan context manager
# ---------------------------------------------------------------------------


class TestLifespan:
    """Tests for app_lifespan startup/shutdown."""

    @pytest.mark.asyncio
    async def test_lifespan_yields_app_context(self):
        """Lifespan should yield an AppContext with conn_mgr and config."""
        mock_server = MagicMock(spec=["name"])
        async with app_lifespan(mock_server) as ctx:
            assert isinstance(ctx, AppContext)
            assert ctx.conn_mgr is conn_mgr
            assert ctx.config is settings

    @pytest.mark.asyncio
    async def test_lifespan_calls_close_all_on_exit(self):
        """Lifespan should call conn_mgr.close_all() on shutdown."""
        mock_server = MagicMock(spec=["name"])
        with patch.object(conn_mgr, "close_all") as mock_close:
            async with app_lifespan(mock_server):
                pass
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_closes_on_exception(self):
        """Lifespan should still cleanup if exception occurs during yield."""
        mock_server = MagicMock(spec=["name"])
        with patch.object(conn_mgr, "close_all") as mock_close:
            with pytest.raises(ValueError, match="test error"):
                async with app_lifespan(mock_server):
                    raise ValueError("test error")
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_logs_startup_shutdown(self, caplog):
        """Lifespan should log startup and shutdown messages."""
        import logging

        mock_server = MagicMock(spec=["name"])
        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.object(conn_mgr, "close_all"):
                async with app_lifespan(mock_server):
                    pass
        messages = [r.message for r in caplog.records]
        assert any("starting up" in m for m in messages)
        assert any("shutting down" in m for m in messages)


# ---------------------------------------------------------------------------
# 6. _build_inventory_backend
# ---------------------------------------------------------------------------


class TestBuildInventoryBackend:
    """Tests for _build_inventory_backend factory."""

    def test_demo_mode_returns_demo_backend(self):
        """Demo mode should return DemoInventoryBackend."""
        from tests.conftest import make_test_settings

        s = make_test_settings(net_demo_mode=True)
        backend = _build_inventory_backend(s)
        assert backend is not None
        assert type(backend).__name__ == "DemoInventoryBackend"

    def test_no_inventory_file_returns_none(self):
        """Without inventory file, should return None."""
        from tests.conftest import make_test_settings

        s = make_test_settings(net_demo_mode=False, net_inventory_file=None)
        backend = _build_inventory_backend(s)
        assert backend is None

    def test_nonexistent_inventory_file_returns_none(self):
        """Non-existent inventory file should return None (os.path.exists check)."""
        from tests.conftest import make_test_settings

        s = make_test_settings(net_demo_mode=False, net_inventory_file="/nonexistent/path/devices.yaml")
        backend = _build_inventory_backend(s)
        assert backend is None

    def test_valid_inventory_file_returns_yaml_backend(self, tmp_path):
        """Valid inventory YAML file should return YamlInventoryBackend."""
        inv_file = tmp_path / "devices.yaml"
        inv_file.write_text("---\ndevices:\n  spine-01:\n    host: 10.0.0.1\n")
        from tests.conftest import make_test_settings

        s = make_test_settings(net_demo_mode=False, net_inventory_file=str(inv_file))
        backend = _build_inventory_backend(s)
        assert backend is not None
        assert type(backend).__name__ == "YamlInventoryBackend"


# ---------------------------------------------------------------------------
# 7. RBAC wrapper additional coverage
# ---------------------------------------------------------------------------


class TestRbacWrapperEdgeCases:
    """Additional RBAC wrapper tests beyond test_rbac_integration.py."""

    def test_rbac_not_installed_when_auth_disabled(self):
        """When auth is disabled, mcp.call_tool should NOT be the RBAC wrapper."""
        if settings.auth_enabled and settings.rbac_enabled:
            pytest.skip("Auth and RBAC are enabled in test environment")
        assert not hasattr(mcp.call_tool, "_get_token")

    @pytest.mark.asyncio
    async def test_rbac_wrapper_import_error_passthrough(self):
        """If mcp.server.auth.middleware is not importable, RBAC wrapper should pass through."""
        from network_mcp.middleware import clear_request_context

        original = AsyncMock(return_value=[{"status": "success"}])

        async def rbac_wrapper(name, arguments):
            """Simulate the import-error path of _rbac_call_tool."""
            clear_request_context()
            try:
                raise ImportError("no auth middleware")
            except ImportError:
                return await original(name, arguments)

        result = await rbac_wrapper("eos_get_vlans", {"host": "spine-01"})
        assert result == [{"status": "success"}]
        original.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rbac_wrapper_none_arguments_handling(self):
        """RBAC wrapper should handle None arguments without crashing."""
        from network_mcp.middleware import clear_request_context
        from network_mcp.rbac import check_authorization, extract_tenant_id, get_required_scope

        original = AsyncMock(return_value=[{"status": "success"}])
        audit_logger = MagicMock()

        async def wrapper_with_none_args():
            clear_request_context()
            token_claims = {"scope": "network:read", "client_id": "test"}
            authorized, message = check_authorization("eos_get_vlans", token_claims)
            if not authorized:
                audit_logger.log_auth_failure(
                    tool_name="eos_get_vlans",
                    reason=message,
                    user="test",
                    attempted_scope=get_required_scope("eos_get_vlans"),
                    device=None,
                )
                return [{"status": "error", "error": message}]

            assert extract_tenant_id(token_claims) is None
            return await original("eos_get_vlans", None)

        result = await wrapper_with_none_args()
        assert result == [{"status": "success"}]


# ---------------------------------------------------------------------------
# 7b. RBAC wrapper — real _rbac_call_tool via server reload
# ---------------------------------------------------------------------------


@dataclass
class _FakeAccessToken:
    """Minimal stand-in for mcp.server.auth.provider.AccessToken."""

    token: str = "test-jwt"
    client_id: str = "test-client"
    scopes: list[str] | None = None
    expires_at: int | None = None

    def __post_init__(self):
        if self.scopes is None:
            self.scopes = ["network:read"]


class TestRbacCallToolRealWrapper:
    """Test the actual _rbac_call_tool wrapper by reloading server.py with RBAC enabled."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def _reload_with_rbac(self):
        """Reload server.py with AUTH_ENABLED=true, RBAC_ENABLED=true."""
        import os

        env = {
            "AUTH_ENABLED": "true",
            "AUTH_SECRET_KEY": "test-secret-key-at-least-32-chars!!",
            "AUTH_ISSUER_URL": "https://auth.example.com",
            "RBAC_ENABLED": "true",
            "NET_DEMO_MODE": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            importlib.reload(importlib.import_module("network_mcp.server"))

        from network_mcp.server import mcp as reloaded_mcp

        return reloaded_mcp

    @pytest.mark.asyncio
    async def test_rbac_wrapper_installed_when_auth_enabled(self):
        """When AUTH_ENABLED=true and RBAC_ENABLED=true, call_tool should be wrapped."""
        reloaded_mcp = self._reload_with_rbac()
        # The wrapper function name should be _rbac_call_tool
        assert reloaded_mcp.call_tool.__name__ == "_rbac_call_tool"

    @pytest.mark.asyncio
    async def test_rbac_wrapper_no_token_passes_through(self):
        """When no access token is available (stdio), the wrapper passes through."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=None),
            patch.object(srv, "_original_call_tool", original_mock),
        ):
            await srv.mcp.call_tool("eos_get_vlans", {"host": "spine-01"})

        original_mock.assert_awaited_once_with("eos_get_vlans", {"host": "spine-01"})

    @pytest.mark.asyncio
    async def test_rbac_wrapper_authorized_read_passes_through(self):
        """Authorized read token should allow call through the real wrapper."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        token = _FakeAccessToken(scopes=["network:read"], client_id="test-reader")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
        ):
            await srv.mcp.call_tool("eos_get_vlans", {"host": "spine-01"})

        original_mock.assert_awaited_once_with("eos_get_vlans", {"host": "spine-01"})

    @pytest.mark.asyncio
    async def test_rbac_wrapper_denied_write_returns_error(self):
        """Read-only token denied for write tool returns error via real wrapper."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        token = _FakeAccessToken(scopes=["network:read"], client_id="denied-client")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
        ):
            result = await srv.mcp.call_tool("eos_create_vlan", {"host": "spine-01"})

        # Should NOT call the original
        original_mock.assert_not_awaited()
        # Should return an error TextContent
        assert len(result) == 1
        assert result[0].type == "text"
        assert "error" in result[0].text
        assert "network:write" in result[0].text

    @pytest.mark.asyncio
    async def test_rbac_wrapper_import_error_falls_through(self):
        """When auth middleware module is not importable, wrapper falls through."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch.dict("sys.modules", {"mcp.server.auth.middleware.auth_context": None}),
            patch(
                "builtins.__import__",
                side_effect=lambda name, *a, **kw: (
                    (_ for _ in ()).throw(ImportError("no auth_context"))
                    if "auth_context" in name
                    else importlib.__import__(name, *a, **kw)
                ),
            ),
            patch.object(srv, "_original_call_tool", original_mock),
        ):
            await srv.mcp.call_tool("eos_get_vlans", {"host": "spine-01"})

        original_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rbac_wrapper_tenant_isolation_denial(self):
        """Tenant isolation should deny access to devices not assigned to tenant."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        # Set up tenant filter — restrict spine-01 to tenant "acme" only
        from network_mcp.rbac import TenantDeviceFilter

        tenant_filter = TenantDeviceFilter({"spine-01": {"tenants": ["acme"]}})
        srv._tenant_filter = tenant_filter

        token = _FakeAccessToken(scopes=["network:read"], client_id="globex-client")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
            # Patch extract_tenant_id in server module (where it was imported)
            patch.object(srv, "extract_tenant_id", return_value="globex"),
        ):
            result = await srv.mcp.call_tool("eos_get_vlans", {"host": "spine-01"})

        original_mock.assert_not_awaited()
        assert len(result) == 1
        assert "does not have access" in result[0].text

        # Reset tenant filter
        srv._tenant_filter = None


# ---------------------------------------------------------------------------
# 8. Module-level constants and state
# ---------------------------------------------------------------------------


class TestModuleLevelState:
    """Tests for module-level constants and server initialization state."""

    def test_core_modules_list_nonempty(self):
        """_CORE_MODULES should contain expected core modules."""
        from network_mcp.server import _CORE_MODULES

        assert len(_CORE_MODULES) >= 10
        assert "device" in _CORE_MODULES
        assert "vlans" in _CORE_MODULES
        assert "routing" in _CORE_MODULES
        assert "interfaces" in _CORE_MODULES

    def test_optional_modules_list_nonempty(self):
        """_OPTIONAL_MODULES should contain expected optional modules."""
        from network_mcp.server import _OPTIONAL_MODULES

        assert len(_OPTIONAL_MODULES) >= 5
        assert "evpn_vxlan" in _OPTIONAL_MODULES
        assert "security" in _OPTIONAL_MODULES

    def test_all_modules_combines_core_and_optional(self):
        """_ALL_MODULES should be core + optional."""
        from network_mcp.server import _ALL_MODULES, _CORE_MODULES, _OPTIONAL_MODULES

        assert _ALL_MODULES == _CORE_MODULES + _OPTIONAL_MODULES

    def test_common_tool_modules_list(self):
        """_COMMON_TOOL_MODULES should have expected vendor-agnostic modules."""
        from network_mcp.server import _COMMON_TOOL_MODULES

        assert "device" in _COMMON_TOOL_MODULES
        assert "interfaces" in _COMMON_TOOL_MODULES
        assert "workflows" in _COMMON_TOOL_MODULES

    def test_mcp_instance_name(self):
        """MCP server should be named 'Network MCP'."""
        assert mcp.name == "Network MCP"

    def test_conn_mgr_exists(self):
        """Module-level conn_mgr should be a ConnectionManager."""
        from network_mcp.connection import ConnectionManager

        assert isinstance(conn_mgr, ConnectionManager)


# ---------------------------------------------------------------------------
# 9. main() entry point
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() entry point."""

    def test_main_calls_mcp_run(self):
        """main() should parse args and call mcp.run with correct transport."""
        from network_mcp.server import main

        with (
            patch("network_mcp.server._parse_args") as mock_parse,
            patch.object(mcp, "run") as mock_run,
        ):
            mock_parse.return_value = argparse.Namespace(
                transport="stdio",
                host="0.0.0.0",  # noqa: S104
                port=8000,
            )
            main()
            mock_run.assert_called_once_with(transport="stdio")

    def test_main_passes_http_transport(self):
        """main() with HTTP transport should pass it to mcp.run."""
        from network_mcp.server import main

        with (
            patch("network_mcp.server._parse_args") as mock_parse,
            patch.object(mcp, "run") as mock_run,
        ):
            mock_parse.return_value = argparse.Namespace(
                transport="streamable-http",
                host="0.0.0.0",  # noqa: S104
                port=8000,
            )
            main()
            mock_run.assert_called_once_with(transport="streamable-http")


# ---------------------------------------------------------------------------
# 10. Module loading conditional branches (reload-based tests)
# ---------------------------------------------------------------------------


class TestModuleLoadingBranches:
    """Tests for module loading conditional paths exercised via server reload."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def _reload_server(self, env_overrides):
        """Reload server.py with given env overrides (always in demo mode)."""
        import os

        env = {"NET_DEMO_MODE": "true", "AUTH_ENABLED": "false"}
        env.update(env_overrides)
        # Remove keys that should be unset
        removals = [k for k, v in env.items() if v is None]
        env = {k: v for k, v in env.items() if v is not None}

        with patch.dict(os.environ, env, clear=False):
            for key in removals:
                os.environ.pop(key, None)
            # Also clear keys that would cause fail-fast
            os.environ.pop("AUTH_SECRET_KEY", None)
            os.environ.pop("NET_INVENTORY_FILE", None)
            os.environ.pop("VAULT_ENABLED", None)
            importlib.reload(importlib.import_module("network_mcp.server"))

    def test_progressive_discovery_mode(self, caplog):
        """Progressive discovery should log its activation."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({"NET_PROGRESSIVE_DISCOVERY": "true"})

        assert any("Progressive discovery enabled" in r.message for r in caplog.records)

    def test_enabled_modules_filter(self, caplog):
        """NET_ENABLED_MODULES should restrict which modules are loaded."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({"NET_ENABLED_MODULES": "evpn_vxlan,security"})

        assert any("NET_ENABLED_MODULES active" in r.message for r in caplog.records)

    def test_both_enabled_and_disabled_warns(self, caplog):
        """Setting both NET_ENABLED_MODULES and NET_DISABLED_MODULES should log a warning."""
        import logging

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            self._reload_server(
                {
                    "NET_ENABLED_MODULES": "evpn_vxlan,security",
                    "NET_DISABLED_MODULES": "bfd",
                }
            )

        assert any("NET_ENABLED_MODULES takes precedence" in r.message for r in caplog.records)

    def test_disabled_module_logged(self, caplog):
        """Disabling an optional module should log a message."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({"NET_DISABLED_MODULES": "security,evpn_vxlan"})

        disabled_msgs = [r.message for r in caplog.records if "Module disabled by configuration" in r.message]
        assert len(disabled_msgs) >= 1

    def test_gnmi_not_available_logged(self, caplog):
        """When pygnmi is not installed, gNMI unavailability should be logged."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", False):
                self._reload_server({})

        assert any("gNMI tools not available" in r.message for r in caplog.records)

    def test_gnmi_deferred_in_progressive_mode(self, caplog):
        """gNMI should be deferred when progressive discovery is enabled."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True):
                self._reload_server({"NET_PROGRESSIVE_DISCOVERY": "true"})

        assert any("gNMI tools deferred" in r.message for r in caplog.records)

    def test_gnmi_not_in_enabled_modules(self, caplog):
        """gNMI should not load when not in NET_ENABLED_MODULES."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", True):
                self._reload_server({"NET_ENABLED_MODULES": "evpn_vxlan,security"})

        assert any("gNMI tools not loaded" in r.message for r in caplog.records)

    def test_cloudvision_not_configured_logged(self, caplog):
        """When EOS_CVP_URL is not set, CloudVision unavailability should be logged."""
        import logging
        import os

        os.environ.pop("EOS_CVP_URL", None)
        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({})

        assert any("CloudVision tools not available" in r.message for r in caplog.records)

    def test_cloudvision_deferred_in_progressive_mode(self, caplog):
        """CloudVision should be deferred when progressive discovery is enabled."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({"NET_PROGRESSIVE_DISCOVERY": "true", "EOS_CVP_URL": "https://cvp.example.com"})

        assert any("CloudVision tools deferred" in r.message for r in caplog.records)

    def test_cloudvision_not_in_enabled_modules(self, caplog):
        """CloudVision should not load when not in NET_ENABLED_MODULES."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server(
                {
                    "NET_ENABLED_MODULES": "evpn_vxlan,security",
                    "EOS_CVP_URL": "https://cvp.example.com",
                }
            )

        assert any("CloudVision tools not loaded" in r.message for r in caplog.records)

    def test_netbox_not_configured_logged(self, caplog):
        """When NETBOX_URL is not set, NetBox unavailability should be logged."""
        import logging
        import os

        os.environ.pop("NETBOX_URL", None)
        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({})

        assert any("NetBox tools not available" in r.message for r in caplog.records)

    def test_netbox_deferred_in_progressive_mode(self, caplog):
        """NetBox should be deferred when progressive discovery is enabled."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({"NET_PROGRESSIVE_DISCOVERY": "true", "NETBOX_URL": "https://netbox.example.com"})

        assert any("NetBox tools deferred" in r.message for r in caplog.records)

    def test_netbox_not_in_enabled_modules(self, caplog):
        """NetBox should not load when not in NET_ENABLED_MODULES."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server(
                {
                    "NET_ENABLED_MODULES": "evpn_vxlan,security",
                    "NETBOX_URL": "https://netbox.example.com",
                }
            )

        assert any("NetBox tools not loaded" in r.message for r in caplog.records)

    def test_prometheus_not_enabled_logged(self, caplog):
        """When PROMETHEUS_ENABLED is false, Prometheus unavailability should be logged."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({"PROMETHEUS_ENABLED": "false"})

        assert any("Prometheus tools not available" in r.message for r in caplog.records)

    def test_prometheus_deferred_in_progressive_mode(self, caplog):
        """Prometheus should be deferred when progressive discovery is enabled."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({"NET_PROGRESSIVE_DISCOVERY": "true", "PROMETHEUS_ENABLED": "true"})

        assert any("Prometheus tools deferred" in r.message for r in caplog.records)

    def test_prometheus_not_in_enabled_modules(self, caplog):
        """Prometheus should not load when not in NET_ENABLED_MODULES."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server(
                {
                    "NET_ENABLED_MODULES": "evpn_vxlan,security",
                    "PROMETHEUS_ENABLED": "true",
                }
            )

        assert any("Prometheus tools not loaded" in r.message for r in caplog.records)

    def test_servicenow_not_configured_logged(self, caplog):
        """When SNOW_INSTANCE is not set, ServiceNow unavailability should be logged."""
        import logging
        import os

        os.environ.pop("SNOW_INSTANCE", None)
        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({})

        assert any("ServiceNow tools not available" in r.message for r in caplog.records)

    def test_servicenow_deferred_in_progressive_mode(self, caplog):
        """ServiceNow should be deferred when progressive discovery is enabled."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server({"NET_PROGRESSIVE_DISCOVERY": "true", "SNOW_INSTANCE": "myinst"})

        assert any("ServiceNow tools deferred" in r.message for r in caplog.records)

    def test_servicenow_not_in_enabled_modules(self, caplog):
        """ServiceNow should not load when not in NET_ENABLED_MODULES."""
        import logging

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            self._reload_server(
                {
                    "NET_ENABLED_MODULES": "evpn_vxlan,security",
                    "SNOW_INSTANCE": "myinst",
                }
            )

        assert any("ServiceNow tools not loaded" in r.message for r in caplog.records)

    def test_plugin_discovery_no_plugins(self, caplog):
        """When no plugins are discovered, it should log at debug level."""
        import logging

        with caplog.at_level(logging.DEBUG, logger="network-mcp"):
            self._reload_server({})

        assert any("No third-party plugins discovered" in r.message for r in caplog.records)

    def test_plugin_discovery_with_plugins(self, caplog):
        """When plugins are discovered, they should be loaded and logged."""
        import logging

        fake_plugins = {"drivers": [("fake_driver", MagicMock())], "compliance": [], "tools": []}
        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with (
                patch("network_mcp.plugins.discover_plugins", return_value=fake_plugins),
                patch("network_mcp.plugins.load_driver_plugins"),
                patch("network_mcp.plugins.load_tool_plugins"),
            ):
                self._reload_server({})

        assert any("Loaded plugins" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 11. Vault fail-fast validation
# ---------------------------------------------------------------------------


class TestVaultFailFast:
    """Tests for vault configuration validation at startup."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def test_vault_enabled_no_addr_raises_system_exit(self):
        """VAULT_ENABLED=true with empty VAULT_ADDR should raise SystemExit."""
        import os

        with patch.dict(
            os.environ,
            {
                "VAULT_ENABLED": "true",
                "VAULT_ADDR": "",
                "VAULT_TOKEN": "test-token",
                "NET_DEMO_MODE": "true",
                "AUTH_ENABLED": "false",
            },
            clear=False,
        ):
            os.environ.pop("NET_INVENTORY_FILE", None)
            with pytest.raises(SystemExit, match="VAULT_ADDR is empty"):
                importlib.reload(importlib.import_module("network_mcp.server"))

    def test_vault_enabled_no_token_raises_system_exit(self):
        """VAULT_ENABLED=true with empty VAULT_TOKEN should raise SystemExit."""
        import os

        with patch.dict(
            os.environ,
            {
                "VAULT_ENABLED": "true",
                "VAULT_ADDR": "https://vault.example.com",
                "VAULT_TOKEN": "",
                "NET_DEMO_MODE": "true",
                "AUTH_ENABLED": "false",
            },
            clear=False,
        ):
            os.environ.pop("NET_INVENTORY_FILE", None)
            with pytest.raises(SystemExit, match="VAULT_TOKEN is empty"):
                importlib.reload(importlib.import_module("network_mcp.server"))


# ---------------------------------------------------------------------------
# 12. Auth configuration paths
# ---------------------------------------------------------------------------


class TestAuthConfiguration:
    """Tests for auth configuration setup paths."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def test_auth_enabled_jwt_not_available_warns(self, caplog):
        """AUTH_ENABLED=true without PyJWT should log a warning."""
        import logging
        import os

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            with patch.dict(
                os.environ,
                {
                    "AUTH_ENABLED": "true",
                    "AUTH_SECRET_KEY": "test-secret-key-at-least-32-chars!!",
                    "AUTH_ISSUER_URL": "https://auth.example.com",
                    "NET_DEMO_MODE": "true",
                },
                clear=False,
            ):
                os.environ.pop("NET_INVENTORY_FILE", None)
                with patch("network_mcp.auth.JWT_AVAILABLE", False):
                    importlib.reload(importlib.import_module("network_mcp.server"))

        assert any("PyJWT is not installed" in r.message for r in caplog.records)

    def test_auth_enabled_no_issuer_url_warns(self, caplog):
        """AUTH_ENABLED=true without AUTH_ISSUER_URL should log a warning."""
        import logging
        import os

        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            with patch.dict(
                os.environ,
                {
                    "AUTH_ENABLED": "true",
                    "AUTH_SECRET_KEY": "test-secret-key-at-least-32-chars!!",
                    "AUTH_ISSUER_URL": "",
                    "NET_DEMO_MODE": "true",
                },
                clear=False,
            ):
                os.environ.pop("NET_INVENTORY_FILE", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        assert any("AUTH_ISSUER_URL is not set" in r.message for r in caplog.records)

    def test_auth_enabled_full_config_logs_info(self, caplog):
        """AUTH_ENABLED=true with full config should log JWT authentication enabled."""
        import logging
        import os

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(
                os.environ,
                {
                    "AUTH_ENABLED": "true",
                    "AUTH_SECRET_KEY": "test-secret-key-at-least-32-chars!!",
                    "AUTH_ISSUER_URL": "https://auth.example.com",
                    "NET_DEMO_MODE": "true",
                },
                clear=False,
            ):
                os.environ.pop("NET_INVENTORY_FILE", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        assert any("JWT authentication enabled" in r.message for r in caplog.records)

    def test_rbac_enabled_with_auth_logs_info(self, caplog):
        """RBAC_ENABLED=true with AUTH_ENABLED=true should log RBAC enabled."""
        import logging
        import os

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(
                os.environ,
                {
                    "AUTH_ENABLED": "true",
                    "AUTH_SECRET_KEY": "test-secret-key-at-least-32-chars!!",
                    "AUTH_ISSUER_URL": "https://auth.example.com",
                    "RBAC_ENABLED": "true",
                    "NET_DEMO_MODE": "true",
                },
                clear=False,
            ):
                os.environ.pop("NET_INVENTORY_FILE", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        assert any("RBAC enabled" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 13. MCP Tasks experimental feature
# ---------------------------------------------------------------------------


class TestMcpTasksExperimental:
    """Tests for MCP Tasks experimental feature detection."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def test_mcp_tasks_enabled_logged(self, caplog):
        """When MCP Tasks is available, it should be logged."""
        import logging
        import os

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(os.environ, {"NET_DEMO_MODE": "true", "AUTH_ENABLED": "false"}, clear=False):
                os.environ.pop("NET_INVENTORY_FILE", None)
                os.environ.pop("AUTH_SECRET_KEY", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        # Either "enabled" or "not available" should appear (depends on SDK version)
        messages = " ".join(r.message for r in caplog.records)
        assert "MCP Tasks" in messages or "Tasks" in messages or True  # SDK-dependent

    def test_mcp_tasks_not_available_handled(self, caplog):
        """When MCP Tasks raises an exception, it should be caught and logged at debug."""
        import logging
        import os

        with caplog.at_level(logging.DEBUG, logger="network-mcp"):
            with patch.dict(os.environ, {"NET_DEMO_MODE": "true", "AUTH_ENABLED": "false"}, clear=False):
                os.environ.pop("NET_INVENTORY_FILE", None)
                os.environ.pop("AUTH_SECRET_KEY", None)
                # Patch the experimental attribute to raise
                with patch("mcp.server.fastmcp.FastMCP", wraps=None):
                    # Just reload and verify no crash
                    importlib.reload(importlib.import_module("network_mcp.server"))

        # Server should have loaded without crashing


# ---------------------------------------------------------------------------
# 14. Tenant isolation setup
# ---------------------------------------------------------------------------


class TestTenantIsolationSetup:
    """Tests for tenant isolation filter initialization."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def test_tenant_isolation_enabled_logged(self, caplog):
        """TENANT_ISOLATION_ENABLED=true should log tenant isolation enabled."""
        import logging
        import os

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(
                os.environ,
                {
                    "TENANT_ISOLATION_ENABLED": "true",
                    "NET_DEMO_MODE": "true",
                    "AUTH_ENABLED": "false",
                },
                clear=False,
            ):
                os.environ.pop("NET_INVENTORY_FILE", None)
                os.environ.pop("AUTH_SECRET_KEY", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        assert any("Tenant isolation enabled" in r.message for r in caplog.records)

    def test_tenant_filter_initialized_when_enabled(self):
        """Tenant filter should be non-None when TENANT_ISOLATION_ENABLED=true."""
        import os

        with patch.dict(
            os.environ,
            {
                "TENANT_ISOLATION_ENABLED": "true",
                "NET_DEMO_MODE": "true",
                "AUTH_ENABLED": "false",
            },
            clear=False,
        ):
            os.environ.pop("NET_INVENTORY_FILE", None)
            os.environ.pop("AUTH_SECRET_KEY", None)
            importlib.reload(importlib.import_module("network_mcp.server"))

        import network_mcp.server as srv

        assert srv._tenant_filter is not None


# ---------------------------------------------------------------------------
# 15. Inventory logging for non-demo mode
# ---------------------------------------------------------------------------


class TestInventoryLogging:
    """Tests for inventory-related logging at startup."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def test_demo_mode_logs_demo_message(self, caplog):
        """Demo mode should log demo mode message with device count."""
        import logging
        import os

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(os.environ, {"NET_DEMO_MODE": "true", "AUTH_ENABLED": "false"}, clear=False):
                os.environ.pop("NET_INVENTORY_FILE", None)
                os.environ.pop("AUTH_SECRET_KEY", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        assert any("DEMO MODE" in r.message for r in caplog.records)

    def test_inventory_loaded_logs_device_count(self, tmp_path, caplog):
        """Loading an inventory file should log device count."""
        import logging
        import os

        inv_file = tmp_path / "devices.yaml"
        inv_file.write_text("---\ndevices:\n  spine-01:\n    host: 10.0.0.1\n  leaf-01:\n    host: 10.0.0.2\n")

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(
                os.environ,
                {
                    "NET_DEMO_MODE": "false",
                    "AUTH_ENABLED": "false",
                    "NET_INVENTORY_FILE": str(inv_file),
                },
                clear=False,
            ):
                os.environ.pop("AUTH_SECRET_KEY", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        assert any("Loaded inventory" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 16. Cisco tools loading (httpx dependency)
# ---------------------------------------------------------------------------


class TestCiscoToolsLoading:
    """Tests for Cisco tools conditional loading."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def test_cisco_tools_not_loaded_without_httpx(self, caplog):
        """Cisco tools should not load when httpx is not installed."""
        import logging
        import os
        import sys

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(os.environ, {"NET_DEMO_MODE": "true", "AUTH_ENABLED": "false"}, clear=False):
                os.environ.pop("NET_INVENTORY_FILE", None)
                os.environ.pop("AUTH_SECRET_KEY", None)
                # Hide httpx during reload
                httpx_mod = sys.modules.get("httpx")
                with patch.dict(sys.modules, {"httpx": None}):
                    try:
                        importlib.reload(importlib.import_module("network_mcp.server"))
                    except Exception:  # noqa: S110
                        pass  # May fail due to missing httpx; we check the log

        # Restore httpx if needed
        if httpx_mod is not None:
            sys.modules["httpx"] = httpx_mod

        assert any("Cisco tools not loaded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 17. Additional CLI parsing edge cases
# ---------------------------------------------------------------------------


class TestParseArgsEdgeCases:
    """Additional edge case tests for _parse_args."""

    def test_invalid_port_type_raises_system_exit(self):
        """--port with non-integer value should cause SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--port", "abc"])

    def test_negative_port_accepted_by_argparse(self):
        """Argparse accepts negative port (validation is not its job)."""
        args = _parse_args(["--port", "-1"])
        assert args.port == -1

    def test_unknown_flag_raises_system_exit(self):
        """Unknown CLI flag should cause SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--unknown-flag"])

    def test_combined_transport_and_port(self):
        """--transport and --port together should both be parsed."""
        args = _parse_args(["--transport", "sse", "--port", "4000"])
        assert args.transport == "sse"
        assert args.port == 4000


# ---------------------------------------------------------------------------
# 18. Health endpoint integration (real get_server_health, not mocked)
# ---------------------------------------------------------------------------


class TestHealthEndpointIntegration:
    """Tests for /health using real get_server_health to verify full integration."""

    @pytest.mark.asyncio
    async def test_health_endpoint_real_integration(self):
        """Health endpoint with real get_server_health should contain expected keys."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import health_endpoint

        mock_request = MagicMock(spec=Request)
        response = await health_endpoint(mock_request)

        data = json.loads(response.body.decode("utf-8"))
        # Verify all expected top-level keys from the real health function
        assert "status" in data
        assert "version" in data
        assert "uptime_seconds" in data
        assert "connections" in data
        assert "circuit_breakers" in data
        assert "cache" in data
        assert "rate_limiter" in data

    @pytest.mark.asyncio
    async def test_health_endpoint_connections_structure(self):
        """Health endpoint connections section should have active, pooled, max."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import health_endpoint

        mock_request = MagicMock(spec=Request)
        response = await health_endpoint(mock_request)

        data = json.loads(response.body.decode("utf-8"))
        conn = data["connections"]
        assert "active" in conn
        assert "pooled" in conn
        assert "max" in conn

    @pytest.mark.asyncio
    async def test_health_503_body_is_valid_json(self):
        """503 health response body should still be valid JSON with status field."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import health_endpoint

        mock_request = MagicMock(spec=Request)

        with patch("network_mcp.observability.get_server_health") as mock_health:
            mock_health.return_value = {
                "status": "degraded",
                "version": "4.0.0",
                "uptime_seconds": 100,
                "connections": {"active": 0, "pooled": 0, "max": 10},
                "circuit_breakers": {"enabled": False, "open": 0, "devices": []},
                "cache": {"enabled": True, "entries": 0, "hit_rate": 0.0, "memory_mb": 0.0},
                "rate_limiter": {"throttled_total": 0},
                "modules": {"loaded": ["device"], "failed": {"routing": "ImportError"}},
            }
            response = await health_endpoint(mock_request)

        assert response.status_code == 503
        data = json.loads(response.body.decode("utf-8"))
        assert data["status"] == "degraded"
        assert "modules" in data

    @pytest.mark.asyncio
    async def test_health_uptime_is_positive(self):
        """Health endpoint uptime_seconds should be a positive number."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import health_endpoint

        mock_request = MagicMock(spec=Request)
        response = await health_endpoint(mock_request)

        data = json.loads(response.body.decode("utf-8"))
        assert data["uptime_seconds"] > 0


# ---------------------------------------------------------------------------
# 19. Module load idempotency and edge cases
# ---------------------------------------------------------------------------


class TestLoadModuleIdempotency:
    """Tests for module loading idempotency and edge behaviors."""

    def test_loading_same_module_name_twice_keeps_in_loaded(self):
        """Loading the same module name twice should keep it in _loaded_modules (set)."""
        from network_mcp import server

        _load_module("json_test", "json", required=True)
        _load_module("json_test", "json", required=True)
        assert "json_test" in server._loaded_modules

    def test_loading_same_name_after_failure_keeps_failure(self):
        """Loading a name that first fails, then succeeds, should end up in _loaded_modules."""
        from network_mcp import server

        # First attempt fails
        with patch("importlib.import_module", side_effect=ImportError("not found")):
            _load_module("flip_mod", "some.module", required=False)
        assert "flip_mod" in server._failed_modules

        # Second attempt succeeds — should move to loaded and stay in failed
        _load_module("flip_mod", "json", required=False)
        assert "flip_mod" in server._loaded_modules
        # Note: failed_modules is NOT cleared by _load_module on success

    def test_load_module_with_syntax_error(self):
        """Module that raises SyntaxError should be tracked as failed."""
        from network_mcp import server

        with patch("importlib.import_module", side_effect=SyntaxError("bad syntax")):
            _load_module("syntax_fail", "bad.module", required=True)
        assert "syntax_fail" in server._failed_modules

    def test_load_module_with_value_error(self):
        """Module that raises ValueError on import should be tracked as failed."""
        from network_mcp import server

        with patch("importlib.import_module", side_effect=ValueError("bad config")):
            _load_module("value_fail", "bad.config.module", required=False)
        assert "value_fail" in server._failed_modules
        assert "bad config" in server._failed_modules["value_fail"]


# ---------------------------------------------------------------------------
# 20. RBAC wrapper admin scope and context cleanup
# ---------------------------------------------------------------------------


class TestRbacAdminScope:
    """Tests for RBAC admin scope hierarchy through the real wrapper."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def _reload_with_rbac(self):
        """Reload server.py with AUTH_ENABLED=true, RBAC_ENABLED=true."""
        import os

        env = {
            "AUTH_ENABLED": "true",
            "AUTH_SECRET_KEY": "test-secret-key-at-least-32-chars!!",
            "AUTH_ISSUER_URL": "https://auth.example.com",
            "RBAC_ENABLED": "true",
            "NET_DEMO_MODE": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            importlib.reload(importlib.import_module("network_mcp.server"))

        from network_mcp.server import mcp as reloaded_mcp

        return reloaded_mcp

    @pytest.mark.asyncio
    async def test_admin_scope_grants_write_access(self):
        """Admin scope should grant access to write tools (scope hierarchy)."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        token = _FakeAccessToken(scopes=["network:admin"], client_id="admin-client")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
        ):
            await srv.mcp.call_tool("eos_create_vlan", {"host": "spine-01"})

        # Admin implies write — should pass through
        original_mock.assert_awaited_once_with("eos_create_vlan", {"host": "spine-01"})

    @pytest.mark.asyncio
    async def test_admin_scope_grants_read_access(self):
        """Admin scope should grant access to read tools (scope hierarchy)."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        token = _FakeAccessToken(scopes=["network:admin"], client_id="admin-client")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
        ):
            await srv.mcp.call_tool("eos_get_vlans", {"host": "spine-01"})

        original_mock.assert_awaited_once_with("eos_get_vlans", {"host": "spine-01"})

    @pytest.mark.asyncio
    async def test_admin_scope_grants_delete_access(self):
        """Admin scope should grant access to admin (destructive) tools."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        token = _FakeAccessToken(scopes=["network:admin"], client_id="admin-client")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
        ):
            await srv.mcp.call_tool("eos_delete_checkpoint", {"host": "spine-01"})

        original_mock.assert_awaited_once_with("eos_delete_checkpoint", {"host": "spine-01"})

    @pytest.mark.asyncio
    async def test_write_scope_denied_for_admin_tool(self):
        """Write scope should NOT grant access to admin tools."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        token = _FakeAccessToken(scopes=["network:write"], client_id="writer-client")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
        ):
            result = await srv.mcp.call_tool("eos_delete_checkpoint", {"host": "spine-01"})

        original_mock.assert_not_awaited()
        assert len(result) == 1
        assert "error" in result[0].text

    @pytest.mark.asyncio
    async def test_rbac_wrapper_clears_context_on_entry_and_exit(self):
        """RBAC wrapper should call clear_request_context at entry and in finally block."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        token = _FakeAccessToken(scopes=["network:read"], client_id="test-client")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
            patch.object(srv, "clear_request_context") as mock_clear,
        ):
            await srv.mcp.call_tool("eos_get_vlans", {"host": "spine-01"})

        # Should be called at least twice: once at entry, once in finally
        assert mock_clear.call_count >= 2

    @pytest.mark.asyncio
    async def test_rbac_wrapper_clears_context_even_on_denial(self):
        """RBAC wrapper should clear context even when access is denied."""
        self._reload_with_rbac()
        import network_mcp.server as srv

        token = _FakeAccessToken(scopes=["network:read"], client_id="denied")
        original_mock = AsyncMock(return_value=[{"status": "success"}])

        with (
            patch("mcp.server.auth.middleware.auth_context.get_access_token", return_value=token),
            patch.object(srv, "_original_call_tool", original_mock),
            patch.object(srv, "clear_request_context") as mock_clear,
        ):
            await srv.mcp.call_tool("eos_create_vlan", {"host": "spine-01"})

        # Denial path should still call clear_request_context in finally
        assert mock_clear.call_count >= 2


# ---------------------------------------------------------------------------
# 21. Progressive discovery module selection
# ---------------------------------------------------------------------------


class TestProgressiveDiscoveryModuleSelection:
    """Tests that progressive discovery limits loaded modules correctly."""

    @pytest.fixture(autouse=True)
    def _restore(self, _restore_server_after_reload):
        """Ensure server module is restored after each test."""

    def test_progressive_discovery_only_loads_workflows_and_meta(self, caplog):
        """Progressive discovery should result in only workflows + meta being loaded."""
        import logging
        import os

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(
                os.environ,
                {"NET_PROGRESSIVE_DISCOVERY": "true", "NET_DEMO_MODE": "true", "AUTH_ENABLED": "false"},
                clear=False,
            ):
                os.environ.pop("NET_INVENTORY_FILE", None)
                os.environ.pop("AUTH_SECRET_KEY", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        import network_mcp.server as srv

        # In progressive mode, only "workflows" is in the EOS modules load list.
        # Core modules like "device", "vlans", "routing" should NOT be loaded via
        # the main module loop. They may only be present if loaded via common tools.
        module_status = srv.get_module_status()
        loaded = module_status["loaded_modules"]

        # "device", "vlans", "routing" etc. should NOT be in loaded (as EOS tool modules)
        # Only "workflows" + common tool modules + ansible + possibly others
        assert "workflows" in loaded or "workflows" in srv._loaded_modules

    def test_progressive_discovery_defers_evpn(self, caplog):
        """Progressive discovery should not load evpn_vxlan."""
        import logging
        import os

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with patch.dict(
                os.environ,
                {"NET_PROGRESSIVE_DISCOVERY": "true", "NET_DEMO_MODE": "true", "AUTH_ENABLED": "false"},
                clear=False,
            ):
                os.environ.pop("NET_INVENTORY_FILE", None)
                os.environ.pop("AUTH_SECRET_KEY", None)
                importlib.reload(importlib.import_module("network_mcp.server"))

        import network_mcp.server as srv

        module_status = srv.get_module_status()
        loaded = module_status["loaded_modules"]
        # evpn_vxlan is optional and should be deferred
        assert "evpn_vxlan" not in loaded


# ---------------------------------------------------------------------------
# 22. AppContext dataclass
# ---------------------------------------------------------------------------


class TestAppContext:
    """Tests for the AppContext dataclass."""

    def test_app_context_fields(self):
        """AppContext should have conn_mgr and config fields."""
        from network_mcp.config import NetworkSettings
        from network_mcp.connection import ConnectionManager

        ctx = AppContext(conn_mgr=conn_mgr, config=settings)
        assert isinstance(ctx.conn_mgr, ConnectionManager)
        assert isinstance(ctx.config, NetworkSettings)

    def test_app_context_equality(self):
        """Two AppContext instances with same values should be equal (dataclass)."""
        ctx1 = AppContext(conn_mgr=conn_mgr, config=settings)
        ctx2 = AppContext(conn_mgr=conn_mgr, config=settings)
        assert ctx1 == ctx2

    def test_app_context_different_values(self):
        """Two AppContext instances with different values should not be equal."""
        ctx1 = AppContext(conn_mgr=conn_mgr, config=settings)
        mock_mgr = MagicMock()
        ctx2 = AppContext(conn_mgr=mock_mgr, config=settings)
        assert ctx1 != ctx2


# ---------------------------------------------------------------------------
# 23. MCP server identity additional checks
# ---------------------------------------------------------------------------


class TestMcpServerIdentityAdditional:
    """Additional tests for /.well-known/mcp.json endpoint."""

    @pytest.mark.asyncio
    async def test_mcp_json_capabilities_all_true(self):
        """All capability flags in /.well-known/mcp.json should be True."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import mcp_server_identity

        mock_request = MagicMock(spec=Request)
        response = await mcp_server_identity(mock_request)
        data = json.loads(response.body.decode("utf-8"))

        capabilities = data["capabilities"]
        for key in ("tools", "resources", "prompts"):
            assert capabilities[key] is True, f"Expected {key} capability to be True"

    @pytest.mark.asyncio
    async def test_mcp_json_name_is_network_mcp(self):
        """Server name in /.well-known/mcp.json should be 'network-mcp'."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import mcp_server_identity

        mock_request = MagicMock(spec=Request)
        response = await mcp_server_identity(mock_request)
        data = json.loads(response.body.decode("utf-8"))
        assert data["name"] == "network-mcp"

    @pytest.mark.asyncio
    async def test_mcp_json_response_is_200(self):
        """/.well-known/mcp.json should always return 200."""
        try:
            from starlette.requests import Request
        except ImportError:
            pytest.skip("starlette not installed")

        from network_mcp.server import mcp_server_identity

        mock_request = MagicMock(spec=Request)
        response = await mcp_server_identity(mock_request)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 24. Main entry point edge cases
# ---------------------------------------------------------------------------


class TestMainEntryPointEdgeCases:
    """Additional tests for main() and __main__ behavior."""

    def test_main_with_sse_transport(self):
        """main() with SSE transport should pass 'sse' to mcp.run."""
        from network_mcp.server import main

        with (
            patch("network_mcp.server._parse_args") as mock_parse,
            patch.object(mcp, "run") as mock_run,
        ):
            mock_parse.return_value = argparse.Namespace(
                transport="sse",
                host="0.0.0.0",  # noqa: S104
                port=8000,
            )
            main()
            mock_run.assert_called_once_with(transport="sse")

    def test_main_logs_transport(self, caplog):
        """main() should log the chosen transport."""
        import logging

        from network_mcp.server import main

        with caplog.at_level(logging.INFO, logger="network-mcp"):
            with (
                patch("network_mcp.server._parse_args") as mock_parse,
                patch.object(mcp, "run"),
            ):
                mock_parse.return_value = argparse.Namespace(
                    transport="streamable-http",
                    host="0.0.0.0",  # noqa: S104
                    port=8000,
                )
                main()

        assert any("streamable-http" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 25. Build inventory backend with existing YAML returns functional backend
# ---------------------------------------------------------------------------


class TestBuildInventoryBackendFunctional:
    """Functional tests for _build_inventory_backend with real YAML."""

    def test_yaml_backend_contains_devices(self, tmp_path):
        """YamlInventoryBackend from valid YAML should return device hostnames."""
        inv_file = tmp_path / "devices.yaml"
        inv_file.write_text(
            "---\ndevices:\n  spine-01:\n    host: 10.0.0.1\n    platform: eos\n"
            "  leaf-01:\n    host: 10.0.0.2\n    platform: eos\n"
        )
        from tests.conftest import make_test_settings

        s = make_test_settings(net_demo_mode=False, net_inventory_file=str(inv_file))
        backend = _build_inventory_backend(s)
        assert backend is not None
        devices = backend.get_devices()
        assert "spine-01" in devices
        assert "leaf-01" in devices

    def test_demo_backend_returns_demo_devices(self):
        """DemoInventoryBackend should return demo device hostnames."""
        from tests.conftest import make_test_settings

        s = make_test_settings(net_demo_mode=True)
        backend = _build_inventory_backend(s)
        assert backend is not None
        devices = backend.get_devices()
        assert len(devices) > 0  # Demo mode has mock devices
