"""Tests for server startup robustness: module tracking, auth fail-fast, inventory validation."""

import importlib
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.observability import get_server_health


@pytest.fixture(autouse=True)
def _restore_server_after_reload():
    """Restore the server module after tests that use importlib.reload.

    Tests in this file may reload network_mcp.server with modified env vars,
    which replaces the module-level `settings` singleton. This fixture saves
    references to the original objects and restores them after each test so
    downstream tests (e.g. test_write_operations) are not affected.
    """
    import network_mcp.helpers as _helpers_mod
    import network_mcp.server as _server_mod

    orig_settings = _server_mod.settings
    orig_conn_mgr = _server_mod.conn_mgr
    orig_mcp = _server_mod.mcp
    orig_helpers_settings = _helpers_mod._settings
    yield
    # Restore original module-level references
    _server_mod.settings = orig_settings
    _server_mod.conn_mgr = orig_conn_mgr
    _server_mod.mcp = orig_mcp
    _helpers_mod._settings = orig_helpers_settings


# ---------------------------------------------------------------------------
# Module load tracking tests
# ---------------------------------------------------------------------------


class TestModuleLoadTracking:
    """Tests for _load_module, _loaded_modules, _failed_modules, get_module_status."""

    def setup_method(self):
        """Clear module tracking state before each test."""
        from network_mcp import server

        server._loaded_modules.clear()
        server._failed_modules.clear()

    def teardown_method(self):
        """Restore module tracking state after each test."""
        from network_mcp import server

        server._loaded_modules.clear()
        server._failed_modules.clear()

    def test_successful_module_load_tracked(self):
        """A successfully imported module appears in _loaded_modules."""
        from network_mcp.server import _load_module, _loaded_modules

        # json is always available
        _load_module("test_json", "json", required=True)
        assert "test_json" in _loaded_modules

    def test_failed_core_module_tracked(self):
        """A failed core module import is recorded in _failed_modules."""
        from network_mcp.server import _failed_modules, _load_module

        _load_module("nonexistent_core", ".tools.nonexistent_xyz_abc", required=True)
        assert "nonexistent_core" in _failed_modules
        assert _failed_modules["nonexistent_core"]  # non-empty error string

    def test_failed_optional_module_tracked(self):
        """A failed optional module import is recorded in _failed_modules."""
        from network_mcp.server import _failed_modules, _load_module

        _load_module("nonexistent_opt", ".tools.nonexistent_xyz_abc", required=False)
        assert "nonexistent_opt" in _failed_modules

    def test_get_module_status_structure(self):
        """get_module_status returns correct dict structure."""
        from network_mcp.server import _load_module, get_module_status

        _load_module("test_json", "json")
        _load_module("bad_mod", ".tools.nonexistent_xyz_abc")

        status = get_module_status()
        assert "loaded_modules" in status
        assert "failed_modules" in status
        assert "test_json" in status["loaded_modules"]
        assert "bad_mod" in status["failed_modules"]

    def test_failed_module_does_not_appear_in_loaded(self):
        """A failed module should NOT appear in _loaded_modules."""
        from network_mcp.server import _load_module, _loaded_modules

        _load_module("bad_mod", ".tools.nonexistent_xyz_abc", required=True)
        assert "bad_mod" not in _loaded_modules


class TestHealthEndpointModuleStatus:
    """Tests for module status in health endpoint."""

    def _make_mock_conn_mgr(self):
        mgr = MagicMock()
        mgr.pool_stats.return_value = {"active": 0, "pooled": 0, "max": 10}
        return mgr

    def _make_mock_cache(self):
        cache = MagicMock()
        cache.stats.return_value = {"enabled": True, "entries": 0, "hit_rate": 0.0, "memory_mb": 0.0}
        return cache

    def _make_mock_cb_registry(self):
        cb = MagicMock()
        cb.stats.return_value = {"enabled": False, "open_circuits": []}
        return cb

    def _make_mock_metrics(self):
        m = MagicMock()
        m.get_metrics_json.return_value = {"rate_limit_throttled": 0}
        return m

    def test_health_degraded_when_modules_failed(self):
        """Health status is 'degraded' when failed_modules is non-empty."""
        module_status = {
            "loaded_modules": ["device", "vlans"],
            "failed_modules": {"routing": "ImportError: No module named 'xyz'"},
        }
        health = get_server_health(
            self._make_mock_conn_mgr(),
            self._make_mock_cache(),
            self._make_mock_cb_registry(),
            self._make_mock_metrics(),
            module_status,
        )
        assert health["status"] == "degraded"
        assert health["modules"]["failed"] == {"routing": "ImportError: No module named 'xyz'"}

    def test_health_healthy_when_all_modules_loaded(self):
        """Health status is 'healthy' when no modules failed."""
        module_status = {
            "loaded_modules": ["device", "vlans", "routing"],
            "failed_modules": {},
        }
        health = get_server_health(
            self._make_mock_conn_mgr(),
            self._make_mock_cache(),
            self._make_mock_cb_registry(),
            self._make_mock_metrics(),
            module_status,
        )
        assert health["status"] == "healthy"
        assert health["modules"]["loaded"] == ["device", "vlans", "routing"]
        assert health["modules"]["failed"] == {}

    def test_health_no_module_key_when_status_is_none(self):
        """When module_status is None, 'modules' key is absent from health."""
        health = get_server_health(
            self._make_mock_conn_mgr(),
            self._make_mock_cache(),
            self._make_mock_cb_registry(),
            self._make_mock_metrics(),
            None,
        )
        assert "modules" not in health

    def test_health_backward_compat_no_module_status_arg(self):
        """get_server_health still works without the module_status argument (backward compat)."""
        health = get_server_health(
            self._make_mock_conn_mgr(),
            self._make_mock_cache(),
            self._make_mock_cb_registry(),
            self._make_mock_metrics(),
        )
        assert health["status"] == "healthy"
        assert "modules" not in health


# ---------------------------------------------------------------------------
# Auth fail-fast tests
# ---------------------------------------------------------------------------


class TestAuthFailFast:
    """Tests for auth configuration validation at startup."""

    def test_auth_enabled_empty_secret_raises_system_exit(self):
        """AUTH_ENABLED=true with empty AUTH_SECRET_KEY raises SystemExit."""
        with patch.dict(
            "os.environ",
            {
                "AUTH_ENABLED": "true",
                "AUTH_SECRET_KEY": "",
                "NET_DEMO_MODE": "true",
            },
            clear=False,
        ):
            with pytest.raises(SystemExit, match="AUTH_SECRET_KEY is empty"):
                importlib.reload(importlib.import_module("network_mcp.server"))

    def test_auth_enabled_no_secret_raises_system_exit(self):
        """AUTH_ENABLED=true with no AUTH_SECRET_KEY at all raises SystemExit."""
        env = {
            "AUTH_ENABLED": "true",
            "NET_DEMO_MODE": "true",
        }
        # Remove AUTH_SECRET_KEY if present
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("os.environ", {}, clear=False):
                import os

                os.environ.pop("AUTH_SECRET_KEY", None)
                with pytest.raises(SystemExit, match="AUTH_SECRET_KEY is empty"):
                    importlib.reload(importlib.import_module("network_mcp.server"))

    def test_rbac_enabled_without_auth_logs_warning(self, caplog):
        """RBAC_ENABLED=true with AUTH_ENABLED=false logs a warning."""
        import logging
        import os

        with patch.dict(
            "os.environ",
            {
                "AUTH_ENABLED": "false",
                "RBAC_ENABLED": "true",
                "NET_DEMO_MODE": "true",
            },
            clear=False,
        ):
            os.environ.pop("AUTH_SECRET_KEY", None)
            os.environ.pop("NET_INVENTORY_FILE", None)
            with caplog.at_level(logging.WARNING, logger="network-mcp"):
                importlib.reload(importlib.import_module("network_mcp.server"))

        assert any("RBAC requires authentication" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Inventory file validation tests
# ---------------------------------------------------------------------------


class TestInventoryFileValidation:
    """Tests for inventory file existence validation at startup."""

    def _nonexistent_path(self):
        """Return a path that definitely does not exist."""
        d = tempfile.mkdtemp()
        import os
        import shutil

        shutil.rmtree(d)
        return os.path.join(d, "devices.yaml")

    def test_nonexistent_inventory_file_raises_system_exit(self):
        """NET_INVENTORY_FILE pointing to non-existent file raises SystemExit."""
        with patch.dict(
            "os.environ",
            {
                "NET_INVENTORY_FILE": self._nonexistent_path(),
                "NET_DEMO_MODE": "false",
                "AUTH_ENABLED": "false",
            },
            clear=False,
        ):
            with pytest.raises(SystemExit, match="does not exist"):
                importlib.reload(importlib.import_module("network_mcp.server"))

    def test_demo_mode_skips_inventory_validation(self):
        """In demo mode, missing inventory file does NOT raise SystemExit."""
        with patch.dict(
            "os.environ",
            {
                "NET_INVENTORY_FILE": self._nonexistent_path(),
                "NET_DEMO_MODE": "true",
                "AUTH_ENABLED": "false",
            },
            clear=False,
        ):
            # Should NOT raise
            importlib.reload(importlib.import_module("network_mcp.server"))
