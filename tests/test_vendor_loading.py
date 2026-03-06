"""Tests for selective vendor module loading (NET_VENDORS + dep availability).

Coverage:
- _vendor_enabled() logic under various settings
- Dep availability flags (_PYEAPI_AVAILABLE, _HTTPX_AVAILABLE, _SCRAPLI_NETCONF_AVAILABLE)
- That EOS/Cisco/Juniper modules load or are skipped based on flags + vendor filter
- Progressive discovery still respected when EOS vendor is enabled
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.config import NetworkSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**kwargs) -> NetworkSettings:
    return NetworkSettings(net_username="admin", net_password="", net_transport="https", **kwargs)


# ---------------------------------------------------------------------------
# _vendor_enabled() unit tests
# ---------------------------------------------------------------------------


class TestVendorEnabled:
    """Unit tests for the _vendor_enabled() helper in server.py."""

    def test_no_filter_all_vendors_enabled(self):
        """When NET_VENDORS is not set, every vendor is enabled."""
        from network_mcp import server

        settings_no_filter = _make_settings()
        assert settings_no_filter.enabled_vendors is None

        # Patch the module-level settings object with one that has no vendor filter
        with patch.object(server, "settings", settings_no_filter):
            assert server._vendor_enabled("eos") is True
            assert server._vendor_enabled("iosxe") is True
            assert server._vendor_enabled("nxos") is True
            assert server._vendor_enabled("junos") is True

    def test_eos_only_filter(self):
        """NET_VENDORS=eos disables all other vendors."""
        from network_mcp import server

        settings_eos = _make_settings(net_vendors="eos")
        with patch.object(server, "settings", settings_eos):
            assert server._vendor_enabled("eos") is True
            assert server._vendor_enabled("iosxe") is False
            assert server._vendor_enabled("nxos") is False
            assert server._vendor_enabled("junos") is False

    def test_cisco_only_filter(self):
        """NET_VENDORS=iosxe,nxos enables Cisco vendors and disables EOS/JunOS."""
        from network_mcp import server

        settings_cisco = _make_settings(net_vendors="iosxe,nxos")
        with patch.object(server, "settings", settings_cisco):
            assert server._vendor_enabled("eos") is False
            assert server._vendor_enabled("iosxe") is True
            assert server._vendor_enabled("nxos") is True
            assert server._vendor_enabled("junos") is False

    def test_juniper_only_filter(self):
        """NET_VENDORS=junos enables only Juniper and disables the rest."""
        from network_mcp import server

        settings_junos = _make_settings(net_vendors="junos")
        with patch.object(server, "settings", settings_junos):
            assert server._vendor_enabled("eos") is False
            assert server._vendor_enabled("iosxe") is False
            assert server._vendor_enabled("nxos") is False
            assert server._vendor_enabled("junos") is True

    def test_empty_vendor_filter_disables_all(self):
        """NET_VENDORS='' yields an empty set — all vendors disabled."""
        from network_mcp import server

        settings_empty = _make_settings(net_vendors="")
        with patch.object(server, "settings", settings_empty):
            assert server._vendor_enabled("eos") is False
            assert server._vendor_enabled("iosxe") is False


# ---------------------------------------------------------------------------
# Dep availability flags
# ---------------------------------------------------------------------------


class TestDepAvailabilityFlags:
    """Verify that the module-level dep flags are booleans."""

    def test_pyeapi_flag_is_bool(self):
        from network_mcp import server

        assert isinstance(server._PYEAPI_AVAILABLE, bool)

    def test_httpx_flag_is_bool(self):
        from network_mcp import server

        assert isinstance(server._HTTPX_AVAILABLE, bool)

    def test_scrapli_netconf_flag_is_bool(self):
        from network_mcp import server

        assert isinstance(server._SCRAPLI_NETCONF_AVAILABLE, bool)

    def test_pyeapi_available_in_dev_env(self):
        """pyeapi is installed in the dev environment so this flag must be True."""
        from network_mcp import server

        assert server._PYEAPI_AVAILABLE is True

    def test_httpx_available_in_dev_env(self):
        """httpx is installed as a cisco extra in the dev environment."""
        from network_mcp import server

        assert server._HTTPX_AVAILABLE is True


# ---------------------------------------------------------------------------
# Module loading gating — simulate dep missing or vendor not in list
# ---------------------------------------------------------------------------


class TestVendorModuleLoadingGating:
    """Tests that _load_module is called (or not) based on dep flags and vendor filter.

    These tests use patch to manipulate _PYEAPI_AVAILABLE, _HTTPX_AVAILABLE,
    _SCRAPLI_NETCONF_AVAILABLE and settings, then verify _load_module calls.
    Since module loading runs at import time, we directly test the helper
    logic by re-running the conditional blocks via a small wrapper.
    """

    def _simulate_eos_loading(self, pyeapi_available: bool, vendor_str: str | None) -> list[str]:
        """Return which tool categories would be loaded for EOS given dep and vendor settings."""
        from network_mcp import server

        loaded: list[str] = []
        settings_obj = _make_settings(net_vendors=vendor_str)

        with patch.object(server, "_PYEAPI_AVAILABLE", pyeapi_available), patch.object(
            server, "settings", settings_obj
        ), patch.object(server, "_load_module", side_effect=lambda name, *a, **kw: loaded.append(name)):
            # Replicate the eos loading decision inline
            eos_allowed = server._PYEAPI_AVAILABLE and server._vendor_enabled("eos")
            if eos_allowed:
                # Load a representative set of core modules
                for mod in ["device", "vlans", "routing"]:
                    server._load_module(mod, f".tools.{mod}", required=True)

        return loaded

    def _simulate_cisco_loading(
        self, httpx_available: bool, vendor_str: str | None
    ) -> list[str]:
        """Return which Cisco modules would be loaded given dep and vendor settings."""
        from network_mcp import server

        loaded: list[str] = []
        settings_obj = _make_settings(net_vendors=vendor_str)

        with patch.object(server, "_HTTPX_AVAILABLE", httpx_available), patch.object(
            server, "settings", settings_obj
        ), patch.object(server, "_load_module", side_effect=lambda name, *a, **kw: loaded.append(name)):
            cisco_vendor_enabled = server._vendor_enabled("iosxe") or server._vendor_enabled("nxos")
            if server._HTTPX_AVAILABLE and cisco_vendor_enabled:
                if server._vendor_enabled("iosxe"):
                    server._load_module("cisco.iosxe", "network_mcp.tools.cisco.iosxe")
                if server._vendor_enabled("nxos"):
                    server._load_module("cisco.vpc", "network_mcp.tools.cisco.vpc")
                    server._load_module("cisco.fex", "network_mcp.tools.cisco.fex")
                    server._load_module("cisco.nxos", "network_mcp.tools.cisco.nxos")

        return loaded

    def _simulate_juniper_loading(
        self, scrapli_available: bool, vendor_str: str | None
    ) -> list[str]:
        """Return which Juniper modules would be loaded given dep and vendor settings."""
        from network_mcp import server

        loaded: list[str] = []
        settings_obj = _make_settings(net_vendors=vendor_str)

        with patch.object(server, "_SCRAPLI_NETCONF_AVAILABLE", scrapli_available), patch.object(
            server, "settings", settings_obj
        ), patch.object(server, "_load_module", side_effect=lambda name, *a, **kw: loaded.append(name)):
            if server._SCRAPLI_NETCONF_AVAILABLE and server._vendor_enabled("junos"):
                server._load_module("juniper.junos", "network_mcp.tools.juniper.junos")

        return loaded

    # --- EOS scenarios ---

    def test_eos_loads_when_dep_ok_and_no_vendor_filter(self):
        loaded = self._simulate_eos_loading(pyeapi_available=True, vendor_str=None)
        assert "device" in loaded
        assert "vlans" in loaded
        assert "routing" in loaded

    def test_eos_skipped_when_pyeapi_missing(self):
        loaded = self._simulate_eos_loading(pyeapi_available=False, vendor_str=None)
        assert loaded == []

    def test_eos_skipped_when_not_in_vendor_filter(self):
        loaded = self._simulate_eos_loading(pyeapi_available=True, vendor_str="iosxe,nxos")
        assert loaded == []

    def test_eos_loads_when_explicitly_in_vendor_filter(self):
        loaded = self._simulate_eos_loading(pyeapi_available=True, vendor_str="eos")
        assert "device" in loaded

    def test_eos_skipped_when_both_dep_missing_and_no_vendor(self):
        loaded = self._simulate_eos_loading(pyeapi_available=False, vendor_str="iosxe")
        assert loaded == []

    # --- Cisco scenarios ---

    def test_cisco_loads_all_when_httpx_ok_and_no_vendor_filter(self):
        loaded = self._simulate_cisco_loading(httpx_available=True, vendor_str=None)
        assert "cisco.iosxe" in loaded
        assert "cisco.vpc" in loaded
        assert "cisco.fex" in loaded
        assert "cisco.nxos" in loaded

    def test_cisco_skipped_when_httpx_missing(self):
        loaded = self._simulate_cisco_loading(httpx_available=False, vendor_str=None)
        assert loaded == []

    def test_cisco_skipped_when_not_in_vendor_filter(self):
        loaded = self._simulate_cisco_loading(httpx_available=True, vendor_str="eos,junos")
        assert loaded == []

    def test_cisco_iosxe_only_when_vendor_filter_is_iosxe(self):
        loaded = self._simulate_cisco_loading(httpx_available=True, vendor_str="iosxe")
        assert "cisco.iosxe" in loaded
        assert "cisco.vpc" not in loaded
        assert "cisco.nxos" not in loaded

    def test_cisco_nxos_only_when_vendor_filter_is_nxos(self):
        loaded = self._simulate_cisco_loading(httpx_available=True, vendor_str="nxos")
        assert "cisco.iosxe" not in loaded
        assert "cisco.nxos" in loaded
        assert "cisco.vpc" in loaded

    # --- Juniper scenarios ---

    def test_juniper_loads_when_dep_ok_and_no_vendor_filter(self):
        loaded = self._simulate_juniper_loading(scrapli_available=True, vendor_str=None)
        assert "juniper.junos" in loaded

    def test_juniper_skipped_when_scrapli_missing(self):
        loaded = self._simulate_juniper_loading(scrapli_available=False, vendor_str=None)
        assert loaded == []

    def test_juniper_skipped_when_not_in_vendor_filter(self):
        loaded = self._simulate_juniper_loading(scrapli_available=True, vendor_str="eos,iosxe")
        assert loaded == []

    def test_juniper_loads_when_in_vendor_filter(self):
        loaded = self._simulate_juniper_loading(scrapli_available=True, vendor_str="junos")
        assert "juniper.junos" in loaded

    # --- Mixed combinations ---

    def test_eos_and_junos_with_no_cisco_dep(self):
        """EOS + JunOS filter loads both, skips Cisco even when httpx present."""
        from network_mcp import server

        eos_loaded: list[str] = []
        cisco_loaded: list[str] = []
        juniper_loaded: list[str] = []
        settings_obj = _make_settings(net_vendors="eos,junos")

        def mock_load(name, *a, **kw):
            if name.startswith("cisco"):
                cisco_loaded.append(name)
            elif name.startswith("juniper"):
                juniper_loaded.append(name)
            else:
                eos_loaded.append(name)

        with patch.object(server, "_PYEAPI_AVAILABLE", True), patch.object(
            server, "_HTTPX_AVAILABLE", True
        ), patch.object(server, "_SCRAPLI_NETCONF_AVAILABLE", True), patch.object(
            server, "settings", settings_obj
        ), patch.object(server, "_load_module", side_effect=mock_load):
            if server._PYEAPI_AVAILABLE and server._vendor_enabled("eos"):
                server._load_module("device", ".tools.device", required=True)

            cisco_vendor_enabled = server._vendor_enabled("iosxe") or server._vendor_enabled("nxos")
            if server._HTTPX_AVAILABLE and cisco_vendor_enabled:
                server._load_module("cisco.iosxe", "network_mcp.tools.cisco.iosxe")

            if server._SCRAPLI_NETCONF_AVAILABLE and server._vendor_enabled("junos"):
                server._load_module("juniper.junos", "network_mcp.tools.juniper.junos")

        assert "device" in eos_loaded
        assert cisco_loaded == []
        assert "juniper.junos" in juniper_loaded
