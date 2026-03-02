"""Tests for plugin security controls (allowlist, blocklist, audit logging, load isolation)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.plugins import (
    _get_plugin_metadata,
    _is_plugin_allowed,
    _log_plugin_event,
    _reset_plugins,
    load_compliance_plugins,
    load_driver_plugins,
    load_tool_plugins,
)


@pytest.fixture(autouse=True)
def _reset_plugin_cache():
    """Reset plugin cache before each test."""
    _reset_plugins()
    yield
    _reset_plugins()


def _make_mock_entry_point(name: str, value: str, group: str, pkg_name: str = "test-pkg", pkg_version: str = "1.0.0"):
    """Create a mock entry point with distribution metadata."""
    ep = MagicMock()
    ep.name = name
    ep.value = value
    ep.group = group
    ep.dist = MagicMock()
    ep.dist.name = pkg_name
    ep.dist.version = pkg_version
    return ep


def _make_settings(allowlist=None, blocklist=None):
    """Create a mock NetworkSettings with plugin allow/blocklist."""
    settings = MagicMock()
    settings.plugin_allowlist = allowlist or []
    settings.plugin_blocklist = blocklist or []
    return settings


# --- _is_plugin_allowed ---


class TestIsPluginAllowed:
    """Test allowlist/blocklist filtering logic."""

    def test_empty_lists_allows_all(self):
        """When both lists are empty, all plugins are allowed."""
        with patch("network_mcp.config.NetworkSettings", return_value=_make_settings()):
            assert _is_plugin_allowed("any-package") is True

    def test_allowlist_permits_listed_plugin(self):
        """Plugin in allowlist is allowed."""
        with patch("network_mcp.config.NetworkSettings", return_value=_make_settings(allowlist=["my-plugin"])):
            assert _is_plugin_allowed("my-plugin") is True

    def test_allowlist_blocks_unlisted_plugin(self):
        """Plugin not in allowlist is blocked when allowlist is set."""
        with patch("network_mcp.config.NetworkSettings", return_value=_make_settings(allowlist=["other-plugin"])):
            assert _is_plugin_allowed("my-plugin") is False

    def test_blocklist_blocks_listed_plugin(self):
        """Plugin in blocklist is blocked."""
        with patch("network_mcp.config.NetworkSettings", return_value=_make_settings(blocklist=["bad-plugin"])):
            assert _is_plugin_allowed("bad-plugin") is False

    def test_blocklist_allows_unlisted_plugin(self):
        """Plugin not in blocklist is allowed."""
        with patch("network_mcp.config.NetworkSettings", return_value=_make_settings(blocklist=["bad-plugin"])):
            assert _is_plugin_allowed("good-plugin") is True

    def test_blocklist_wins_over_allowlist(self):
        """Plugin in both allowlist and blocklist is blocked (blocklist takes precedence)."""
        with patch(
            "network_mcp.config.NetworkSettings",
            return_value=_make_settings(allowlist=["dual-plugin"], blocklist=["dual-plugin"]),
        ):
            assert _is_plugin_allowed("dual-plugin") is False


# --- _get_plugin_metadata ---


class TestGetPluginMetadata:
    """Test plugin metadata extraction."""

    def test_extracts_metadata_from_entry_point(self):
        """Extracts name, version, and entry point string from a valid entry point."""
        ep = _make_mock_entry_point("my_driver", "my_pkg:MyDriver", "network_mcp.drivers", "my-pkg", "2.1.0")
        metadata = _get_plugin_metadata(ep)

        assert metadata["name"] == "my-pkg"
        assert metadata["version"] == "2.1.0"
        assert metadata["entry_point"] == "network_mcp.drivers:my_driver"

    def test_handles_missing_dist(self):
        """Handles entry point with no distribution info."""
        ep = MagicMock()
        ep.name = "orphan"
        ep.group = "network_mcp.tools"
        ep.dist = None

        metadata = _get_plugin_metadata(ep)

        assert metadata["name"] == "unknown"
        assert metadata["version"] == "unknown"
        assert metadata["entry_point"] == "network_mcp.tools:orphan"


# --- _log_plugin_event ---


class TestLogPluginEvent:
    """Test audit logging for plugin lifecycle events."""

    def test_logs_load_event(self, caplog):
        """Plugin load event is written to application log."""
        with caplog.at_level(logging.INFO, logger="network-mcp"):
            _log_plugin_event("test-pkg", "network_mcp.drivers", "loaded", version="1.0.0")

        assert "test-pkg" in caplog.text
        assert "loaded" in caplog.text

    def test_logs_blocked_event(self, caplog):
        """Plugin blocked event is written to application log."""
        with caplog.at_level(logging.INFO, logger="network-mcp"):
            _log_plugin_event("bad-pkg", "network_mcp.tools", "blocked", version="0.5.0")

        assert "bad-pkg" in caplog.text
        assert "blocked" in caplog.text

    def test_logs_failed_event(self, caplog):
        """Plugin failed event is written to application log."""
        with caplog.at_level(logging.INFO, logger="network-mcp"):
            _log_plugin_event("broken-pkg", "network_mcp.compliance", "failed", detail="ImportError: no module")

        assert "broken-pkg" in caplog.text
        assert "failed" in caplog.text

    def test_audit_logger_receives_event(self):
        """Audit logger receives structured plugin event data."""
        with patch("network_mcp.plugins.audit_logger") as mock_audit:
            mock_logger = MagicMock()
            mock_audit._logger = mock_logger

            _log_plugin_event("test-pkg", "network_mcp.drivers", "loaded", version="1.0.0")

            mock_logger.makeRecord.assert_called_once()
            mock_logger.handle.assert_called_once()
            record = mock_logger.handle.call_args[0][0]
            assert record.audit_data["event"] == "plugin_lifecycle"
            assert record.audit_data["plugin_name"] == "test-pkg"
            assert record.audit_data["action"] == "loaded"
            assert record.audit_data["plugin_version"] == "1.0.0"


# --- Integration: load functions with allowlist/blocklist ---


class TestDriverPluginSecurity:
    """Test that driver plugin loading respects allowlist/blocklist."""

    def _setup_driver_ep(self, pkg_name="my-driver-pkg"):
        mock_ep = _make_mock_entry_point("custom_driver", "pkg:Driver", "network_mcp.drivers", pkg_name, "1.0.0")
        mock_ep.load.return_value = type("CustomDriver", (), {})

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [mock_ep]
            return []

        return mock_ep, mock_entry_points

    def test_allowed_driver_loads(self):
        """Driver plugin in allowlist is loaded."""
        mock_ep, mock_entry_points = self._setup_driver_ep("my-driver-pkg")

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings(allowlist=["my-driver-pkg"])),
        ):
            result = load_driver_plugins({})

        assert "custom_driver" in result

    def test_blocklisted_driver_blocked(self):
        """Driver plugin in blocklist is not loaded."""
        mock_ep, mock_entry_points = self._setup_driver_ep("bad-driver-pkg")

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings(blocklist=["bad-driver-pkg"])),
        ):
            result = load_driver_plugins({})

        assert "custom_driver" not in result
        mock_ep.load.assert_not_called()

    def test_driver_not_in_allowlist_blocked(self):
        """Driver plugin not in allowlist is not loaded when allowlist is set."""
        mock_ep, mock_entry_points = self._setup_driver_ep("unlisted-pkg")

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings(allowlist=["other-pkg"])),
        ):
            result = load_driver_plugins({})

        assert "custom_driver" not in result
        mock_ep.load.assert_not_called()

    def test_driver_load_failure_does_not_crash(self):
        """Driver plugin that raises during load does not crash the server."""
        mock_ep = _make_mock_entry_point("bad_driver", "pkg:Driver", "network_mcp.drivers", "bad-pkg", "1.0.0")
        mock_ep.load.side_effect = RuntimeError("driver init exploded")

        def mock_entry_points(group=None):
            if group == "network_mcp.drivers":
                return [mock_ep]
            return []

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings()),
        ):
            result = load_driver_plugins({})

        assert "bad_driver" not in result


class TestCompliancePluginSecurity:
    """Test that compliance plugin loading respects allowlist/blocklist."""

    def test_allowed_compliance_loads(self):
        mock_ep = _make_mock_entry_point("stig", "stig:CHECKS", "network_mcp.compliance", "stig-pkg", "1.0.0")
        mock_ep.load.return_value = [{"id": "STIG-001"}]

        def mock_entry_points(group=None):
            if group == "network_mcp.compliance":
                return [mock_ep]
            return []

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings(allowlist=["stig-pkg"])),
        ):
            result = load_compliance_plugins([])

        assert len(result) == 1
        assert result[0]["id"] == "STIG-001"

    def test_blocklisted_compliance_blocked(self):
        mock_ep = _make_mock_entry_point("bad", "bad:CHECKS", "network_mcp.compliance", "bad-pkg", "1.0.0")

        def mock_entry_points(group=None):
            if group == "network_mcp.compliance":
                return [mock_ep]
            return []

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings(blocklist=["bad-pkg"])),
        ):
            result = load_compliance_plugins([])

        assert result == []
        mock_ep.load.assert_not_called()

    def test_compliance_load_failure_does_not_crash(self):
        mock_ep = _make_mock_entry_point("broken", "broken:CHECKS", "network_mcp.compliance", "broken-pkg", "1.0.0")
        mock_ep.load.side_effect = ImportError("missing dep")

        def mock_entry_points(group=None):
            if group == "network_mcp.compliance":
                return [mock_ep]
            return []

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings()),
        ):
            result = load_compliance_plugins([])

        assert result == []


class TestToolPluginSecurity:
    """Test that tool plugin loading respects allowlist/blocklist."""

    def test_allowed_tool_loads(self):
        setup_fn = MagicMock()
        mock_ep = _make_mock_entry_point("my_tools", "tools:setup", "network_mcp.tools", "tools-pkg", "2.0.0")
        mock_ep.load.return_value = setup_fn

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        mcp = MagicMock()
        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings(allowlist=["tools-pkg"])),
        ):
            result = load_tool_plugins(mcp)

        assert result == ["my_tools"]
        setup_fn.assert_called_once_with(mcp)

    def test_blocklisted_tool_blocked(self):
        mock_ep = _make_mock_entry_point("evil", "evil:setup", "network_mcp.tools", "evil-pkg", "0.1.0")

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings(blocklist=["evil-pkg"])),
        ):
            result = load_tool_plugins(MagicMock())

        assert result == []
        mock_ep.load.assert_not_called()

    def test_tool_load_failure_does_not_crash(self):
        mock_ep = _make_mock_entry_point("broken", "broken:setup", "network_mcp.tools", "broken-pkg", "1.0.0")
        mock_ep.load.side_effect = RuntimeError("kaboom")

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings()),
        ):
            result = load_tool_plugins(MagicMock())

        assert result == []


class TestBackwardCompatibility:
    """Test that empty allowlist/blocklist preserves backward-compatible behavior."""

    def test_empty_lists_loads_all_plugins(self):
        """When both lists are empty, all discovered plugins are loaded."""
        setup_fn = MagicMock()
        mock_ep = _make_mock_entry_point("any_tool", "pkg:setup", "network_mcp.tools", "any-pkg", "1.0.0")
        mock_ep.load.return_value = setup_fn

        def mock_entry_points(group=None):
            if group == "network_mcp.tools":
                return [mock_ep]
            return []

        with (
            patch("network_mcp.plugins.importlib.metadata.entry_points", side_effect=mock_entry_points),
            patch("network_mcp.config.NetworkSettings", return_value=_make_settings()),
        ):
            result = load_tool_plugins(MagicMock())

        assert result == ["any_tool"]
        setup_fn.assert_called_once()
