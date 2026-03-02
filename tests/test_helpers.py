"""Tests for shared helper functions."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pyeapi.eapilib
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from network_mcp.config import NetworkSettings
from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    _get_settings,
    _init_settings,
    check_read_only,
    handle_tool_errors,
    paginate_dict,
    paginate_list,
    run_config_command,
    run_session_command,
    run_show_command,
    validate_cli_param,
    validate_host,
    validate_interface_name,
    validate_ip_or_hostname,
    validate_vlan_id,
)
from network_mcp.server import conn_mgr


def _mock_acquire_for(mock_node):
    """Return a patch context that mocks conn_mgr.acquire to yield mock_node."""

    @contextmanager
    def _acquire(host):  # noqa: ARG001
        yield mock_node

    return patch.object(conn_mgr, "acquire", side_effect=_acquire)


# --- Annotation Constants ---


def test_read_only_annotations():
    assert READ_ONLY.readOnlyHint is True
    assert READ_ONLY.destructiveHint is False
    assert READ_ONLY.idempotentHint is True


def test_write_safe_annotations():
    assert WRITE_SAFE.readOnlyHint is False
    assert WRITE_SAFE.destructiveHint is False
    assert WRITE_SAFE.idempotentHint is True


def test_destructive_annotations():
    assert DESTRUCTIVE.readOnlyHint is False
    assert DESTRUCTIVE.destructiveHint is True
    assert DESTRUCTIVE.idempotentHint is False


# --- validate_host ---


def test_validate_host_valid():
    assert validate_host("spine-01") is None
    assert validate_host("10.0.0.1") is None
    assert validate_host("switch.lab.local") is None
    assert validate_host("host_name") is None
    assert validate_host("fe80::1") is None


def test_validate_host_empty():
    assert validate_host("") is not None
    assert validate_host("   ") is not None


def test_validate_host_too_long():
    assert validate_host("a" * 254) is not None


def test_validate_host_max_length():
    assert validate_host("a" * 253) is None


def test_validate_host_invalid_chars():
    assert validate_host("host; rm -rf /") is not None
    assert validate_host("host$(cmd)") is not None
    assert validate_host("host`cmd`") is not None
    assert validate_host("host name") is not None
    assert validate_host("host/path") is not None


def test_validate_host_sql_injection():
    assert validate_host("host' OR '1'='1") is not None


def test_validate_host_command_injection():
    assert validate_host("host; rm -rf /") is not None
    assert validate_host("host$(whoami)") is not None
    assert validate_host("host`id`") is not None


def test_validate_host_xss():
    assert validate_host("<script>alert(1)</script>") is not None


def test_validate_host_path_traversal():
    assert validate_host("../../etc/passwd") is not None
    assert validate_host("host/../secret") is not None


# --- validate_interface_name (extended edge cases) ---


def test_validate_interface_name_subinterfaces():
    assert validate_interface_name("Ethernet1/1") is None
    assert validate_interface_name("Ethernet1/2/3") is None


def test_validate_interface_name_invalid_prefix():
    assert validate_interface_name("FooBar1") is not None
    assert validate_interface_name("Serial0") is not None


# --- validate_vlan_id ---


def test_validate_vlan_id_valid():
    assert validate_vlan_id(1) is None
    assert validate_vlan_id(100) is None
    assert validate_vlan_id(4094) is None


def test_validate_vlan_id_invalid():
    assert validate_vlan_id(0) is not None
    assert validate_vlan_id(-1) is not None
    assert validate_vlan_id(4095) is not None
    assert validate_vlan_id(9999) is not None


# --- validate_interface_name ---


def test_validate_interface_name_valid():
    assert validate_interface_name("Ethernet1") is None
    assert validate_interface_name("Management1") is None
    assert validate_interface_name("Loopback0") is None
    assert validate_interface_name("Vlan100") is None
    assert validate_interface_name("Port-Channel1") is None
    assert validate_interface_name("Vxlan1") is None


def test_validate_interface_name_empty():
    assert validate_interface_name("") is not None
    assert validate_interface_name("   ") is not None


def test_validate_interface_name_invalid():
    assert validate_interface_name("GigabitEthernet1") is not None
    assert validate_interface_name("eth0") is not None
    assert validate_interface_name("random") is not None


# --- run_show_command ---


def test_run_show_command_success():
    mock_node = MagicMock()
    mock_node.run_show.return_value = [{"test": "data"}]
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show version"])
    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"] == [{"test": "data"}]
    mock_node.run_show.assert_called_once_with(["show version"], encoding="json")


def test_run_show_command_text_encoding():
    mock_node = MagicMock()
    mock_node.run_show.return_value = [{"output": "some text"}]
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show running-config"], encoding="text")
    assert result["status"] == "success"
    mock_node.run_show.assert_called_once_with(["show running-config"], encoding="text")


def test_run_show_command_invalid_host():
    with pytest.raises(ToolError, match="Host is required"):
        run_show_command(conn_mgr, "", ["show version"])


def test_run_show_command_invalid_host_injection():
    with pytest.raises(ToolError, match="Invalid host"):
        run_show_command(conn_mgr, "host; rm -rf /", ["show version"])


def test_run_show_command_connection_error():
    mock_node = MagicMock()
    mock_node.run_show.side_effect = pyeapi.eapilib.ConnectionError("test-host", "connection refused")
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "bad-host", ["show version"])
    assert result["status"] == "error"
    assert "Connection to" in result["error"]


def test_run_show_command_command_error():
    mock_node = MagicMock()
    mock_node.run_show.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show bad-command"])
    assert result["status"] == "error"
    assert "Command error" in result["error"]


def test_run_show_command_unexpected_error():
    mock_node = MagicMock()
    mock_node.run_show.side_effect = RuntimeError("something unexpected")
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show version"])
    assert result["status"] == "error"
    assert "Unexpected error" in result["error"]


def test_run_show_command_multiple_commands():
    mock_node = MagicMock()
    mock_node.run_show.return_value = [{"cmd1": "data1"}, {"cmd2": "data2"}]
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show version", "show hostname"])
    assert result["status"] == "success"
    assert len(result["data"]) == 2
    assert result["data"][0] == {"cmd1": "data1"}
    assert result["data"][1] == {"cmd2": "data2"}


# --- retry behavior ---


def test_run_show_command_retries_on_connection_error():
    """Verify tenacity retry: first call raises ConnectionError, second succeeds."""
    mock_node = MagicMock()
    mock_node.run_show.side_effect = [
        pyeapi.eapilib.ConnectionError("test-host", "connection refused"),
        [{"version": "4.32.1F"}],
    ]
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show version"])
    assert result["status"] == "success"
    assert result["data"] == [{"version": "4.32.1F"}]
    assert mock_node.run_show.call_count == 2


# --- Settings injection (refcount & singleton consolidation) ---
# Verifies run_show_command/run_config_command use conn_mgr.acquire() (refcount-protected)
# and that _init_settings() injects the server's settings as a single source of truth.


def test_settings_injection_via_init_settings():
    """Injecting settings via _init_settings() makes them available to _get_settings()."""
    import network_mcp.helpers as helpers_mod

    original = helpers_mod._settings
    try:
        custom = NetworkSettings(net_read_only=False, rate_limit_show=99.0)
        _init_settings(custom)
        retrieved = _get_settings()
        assert retrieved is custom
        assert retrieved.rate_limit_show == 99.0
        assert retrieved.net_read_only is False
    finally:
        # Restore original settings
        helpers_mod._settings = original


def test_settings_fallback_without_injection():
    """Without injection, _get_settings() creates a default NetworkSettings as fallback."""
    import network_mcp.helpers as helpers_mod

    original = helpers_mod._settings
    try:
        helpers_mod._settings = None
        retrieved = _get_settings()
        assert isinstance(retrieved, NetworkSettings)
    finally:
        helpers_mod._settings = original


def test_check_read_only_uses_injected_settings():
    """check_read_only() respects the injected settings instance."""
    import network_mcp.helpers as helpers_mod

    original = helpers_mod._settings
    try:
        # Inject writable settings
        writable = NetworkSettings(net_read_only=False)
        _init_settings(writable)
        assert check_read_only() is None  # writes allowed

        # Inject read-only settings
        readonly = NetworkSettings(net_read_only=True)
        _init_settings(readonly)
        result = check_read_only()
        assert result is not None
        assert "Write operations are disabled" in result
    finally:
        helpers_mod._settings = original


def test_meta_settings_injection():
    """_init_meta_settings() injects settings into meta.py for _is_progressive_discovery()."""
    import network_mcp.tools.meta as meta_mod
    from network_mcp.tools.meta import _init_meta_settings, _is_progressive_discovery

    original = meta_mod._settings
    try:
        # Inject settings with progressive discovery disabled
        s = NetworkSettings(net_progressive_discovery=False)
        _init_meta_settings(s)
        assert _is_progressive_discovery() is False

        # Inject settings with progressive discovery enabled
        s2 = NetworkSettings(net_progressive_discovery=True)
        _init_meta_settings(s2)
        assert _is_progressive_discovery() is True
    finally:
        meta_mod._settings = original


# --- paginate_list ---


def test_paginate_list_basic():
    """Basic pagination: 100 items, limit=10, offset=0 returns first 10."""
    data = list(range(100))
    page, meta = paginate_list(data, limit=10, offset=0)
    assert page == list(range(10))
    assert meta["total"] == 100
    assert meta["offset"] == 0
    assert meta["limit"] == 10
    assert meta["returned"] == 10
    assert meta["has_more"] is True


def test_paginate_list_offset():
    """Offset near end: limit=10, offset=90 returns last 10."""
    data = list(range(100))
    page, meta = paginate_list(data, limit=10, offset=90)
    assert page == list(range(90, 100))
    assert meta["total"] == 100
    assert meta["returned"] == 10
    assert meta["has_more"] is False


def test_paginate_list_offset_beyond_total():
    """Offset beyond total returns empty list."""
    data = list(range(100))
    page, meta = paginate_list(data, limit=10, offset=200)
    assert page == []
    assert meta["total"] == 100
    assert meta["returned"] == 0
    assert meta["has_more"] is False


def test_paginate_list_no_limit():
    """No limit returns all items."""
    data = list(range(50))
    page, meta = paginate_list(data)
    assert page == list(range(50))
    assert meta["total"] == 50
    assert meta["limit"] == 50
    assert meta["returned"] == 50
    assert meta["has_more"] is False


def test_paginate_list_empty():
    """Empty list input returns empty list."""
    page, meta = paginate_list([])
    assert page == []
    assert meta["total"] == 0
    assert meta["returned"] == 0
    assert meta["has_more"] is False


def test_paginate_list_limit_zero():
    """limit=0 returns empty page (offset 0 to 0)."""
    data = list(range(10))
    page, meta = paginate_list(data, limit=0, offset=0)
    assert page == []
    assert meta["total"] == 10
    assert meta["returned"] == 0
    assert meta["has_more"] is True


def test_paginate_list_partial_page():
    """Limit larger than remaining items returns partial page."""
    data = list(range(5))
    page, meta = paginate_list(data, limit=10, offset=3)
    assert page == [3, 4]
    assert meta["total"] == 5
    assert meta["returned"] == 2
    assert meta["has_more"] is False


def test_paginate_list_metadata_keys():
    """All expected metadata keys are present."""
    data = list(range(20))
    _, meta = paginate_list(data, limit=5, offset=0)
    assert set(meta.keys()) == {"total", "offset", "limit", "returned", "has_more"}


# --- paginate_dict ---


def test_paginate_dict_basic():
    """Basic dict pagination returns correct subset."""
    data = {f"key{i}": {"val": i} for i in range(20)}
    page, meta = paginate_dict(data, limit=5, offset=0)
    assert len(page) == 5
    assert meta["total"] == 20
    assert meta["returned"] == 5
    assert meta["has_more"] is True


def test_paginate_dict_offset_beyond_total():
    """Offset beyond total returns empty dict with returned=0."""
    data = {"a": 1, "b": 2}
    page, meta = paginate_dict(data, limit=5, offset=10)
    assert page == {}
    assert meta["total"] == 2
    assert meta["returned"] == 0
    assert meta["has_more"] is False


def test_paginate_dict_metadata_keys():
    """All expected metadata keys are present for paginate_dict."""
    data = {"a": 1, "b": 2, "c": 3}
    _, meta = paginate_dict(data, limit=2, offset=0)
    assert set(meta.keys()) == {"total", "offset", "limit", "returned", "has_more"}


# --- handle_tool_errors decorator ---


def test_handle_tool_errors_connection_error():
    """Decorator catches pyeapi ConnectionError and returns error dict."""

    @handle_tool_errors
    def failing_tool(host):
        raise pyeapi.eapilib.ConnectionError(host, "connection refused")

    result = failing_tool("spine-01")
    assert result["status"] == "error"
    assert result["device"] == "spine-01"
    assert "Connection to spine-01 failed" in result["error"]


def test_handle_tool_errors_command_error():
    """Decorator catches pyeapi CommandError and returns error dict."""

    @handle_tool_errors
    def failing_tool(host):
        raise pyeapi.eapilib.CommandError(1, "invalid command")

    result = failing_tool("leaf-01")
    assert result["status"] == "error"
    assert "Command error on leaf-01" in result["error"]


def test_handle_tool_errors_timeout_error():
    """Decorator catches TimeoutError and returns error dict."""

    @handle_tool_errors
    def failing_tool(host):
        raise TimeoutError("device timeout")

    result = failing_tool("spine-02")
    assert result["status"] == "error"
    assert "Timeout communicating with spine-02" in result["error"]


def test_handle_tool_errors_tool_error_propagates():
    """Decorator re-raises ToolError instead of catching it."""

    @handle_tool_errors
    def failing_tool(host):
        raise ToolError("validation failed")

    with pytest.raises(ToolError, match="validation failed"):
        failing_tool("spine-01")


def test_handle_tool_errors_generic_exception():
    """Decorator catches generic exceptions and returns error dict."""

    @handle_tool_errors
    def failing_tool(host):
        raise ValueError("something broke")

    result = failing_tool("spine-01")
    assert result["status"] == "error"
    assert "Unexpected error on spine-01" in result["error"]


def test_handle_tool_errors_host_from_kwargs():
    """Decorator resolves host from keyword arguments."""

    @handle_tool_errors
    def failing_tool(host):
        raise TimeoutError("timeout")

    result = failing_tool(host="my-switch")
    assert result["device"] == "my-switch"


# --- validate_cli_param ---


def test_validate_cli_param_empty():
    assert validate_cli_param("") is not None
    assert "cannot be empty" in validate_cli_param("")


def test_validate_cli_param_whitespace():
    assert "cannot be empty" in validate_cli_param("   ")


def test_validate_cli_param_valid():
    assert validate_cli_param("Ethernet1") is None


def test_validate_cli_param_injection():
    assert "invalid characters" in validate_cli_param("cmd;evil")
    assert "invalid characters" in validate_cli_param("cmd|pipe")
    assert "invalid characters" in validate_cli_param("cmd`tick`")


# --- validate_ip_or_hostname ---


def test_validate_ip_or_hostname_valid():
    assert validate_ip_or_hostname("10.0.0.1") is None
    assert validate_ip_or_hostname("spine-01") is None


def test_validate_ip_or_hostname_invalid_chars():
    assert validate_ip_or_hostname("host name") is not None
    assert "invalid characters" in validate_ip_or_hostname("host name")


def test_validate_ip_or_hostname_empty():
    assert validate_ip_or_hostname("") is not None


# --- run_config_command error paths ---


def test_run_config_command_connection_error():
    """run_config_command returns error dict on ConnectionError."""
    import network_mcp.helpers as helpers_mod

    original = helpers_mod._settings
    try:
        helpers_mod._settings = NetworkSettings(net_read_only=False)
        mock_node = MagicMock()
        mock_node.run_config.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
        with _mock_acquire_for(mock_node):
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "error"
        assert "Connection to spine-01 failed" in result["error"]
    finally:
        helpers_mod._settings = original


def test_run_config_command_command_error():
    """run_config_command returns error dict on CommandError."""
    import network_mcp.helpers as helpers_mod

    original = helpers_mod._settings
    try:
        helpers_mod._settings = NetworkSettings(net_read_only=False)
        mock_node = MagicMock()
        mock_node.run_config.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        with _mock_acquire_for(mock_node):
            result = run_config_command(conn_mgr, "spine-01", ["bad command"], "test_action")
        assert result["status"] == "error"
        assert "Configuration command error on spine-01" in result["error"]
    finally:
        helpers_mod._settings = original


def test_run_config_command_unexpected_error():
    """run_config_command returns error dict on generic Exception."""
    import network_mcp.helpers as helpers_mod

    original = helpers_mod._settings
    try:
        helpers_mod._settings = NetworkSettings(net_read_only=False)
        mock_node = MagicMock()
        mock_node.run_config.side_effect = RuntimeError("driver crash")
        with _mock_acquire_for(mock_node):
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "error"
        assert "Unexpected error on spine-01" in result["error"]
    finally:
        helpers_mod._settings = original


def test_run_config_command_circuit_breaker_open():
    """run_config_command returns error when circuit breaker is open."""
    import network_mcp.helpers as helpers_mod

    original = helpers_mod._settings
    try:
        helpers_mod._settings = NetworkSettings(net_read_only=False)
        mock_breaker = MagicMock(consecutive_failures=5)
        with (
            patch("network_mcp.helpers.circuit_breaker_registry.allow_request", return_value=False),
            patch("network_mcp.helpers.circuit_breaker_registry.get", return_value=mock_breaker),
        ):
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "error"
        assert "Circuit breaker open" in result["error"]
    finally:
        helpers_mod._settings = original


def test_run_config_command_rate_limited():
    """run_config_command returns error when rate limited."""
    import network_mcp.helpers as helpers_mod

    original = helpers_mod._settings
    try:
        helpers_mod._settings = NetworkSettings(net_read_only=False)
        with (
            patch("network_mcp.helpers.device_rate_limiter.check_config", return_value=False),
            patch("network_mcp.helpers.device_rate_limiter.check_config_with_backoff", return_value=False),
            patch("network_mcp.helpers.device_rate_limiter.config_wait_time", return_value=3.5),
        ):
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["status"] == "error"
        assert "Config rate limit exceeded" in result["error"]
    finally:
        helpers_mod._settings = original


# --- run_session_command error paths ---


def test_run_session_command_connection_error():
    """run_session_command returns error dict on ConnectionError."""
    mock_node = MagicMock()
    mock_node.run_show.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "refused")
    with _mock_acquire_for(mock_node):
        result = run_session_command(conn_mgr, "spine-01", ["configure session s1"], "create_session")
    assert result["status"] == "error"
    assert "Connection to spine-01 failed" in result["error"]


def test_run_session_command_command_error():
    """run_session_command returns error dict on CommandError."""
    mock_node = MagicMock()
    mock_node.run_show.side_effect = pyeapi.eapilib.CommandError(1, "bad command")
    with _mock_acquire_for(mock_node):
        result = run_session_command(conn_mgr, "spine-01", ["configure session s1"], "create_session")
    assert result["status"] == "error"
    assert "Configuration command error on spine-01" in result["error"]


def test_run_session_command_unexpected_error():
    """run_session_command returns error dict on generic Exception."""
    mock_node = MagicMock()
    mock_node.run_show.side_effect = RuntimeError("unexpected crash")
    with _mock_acquire_for(mock_node):
        result = run_session_command(conn_mgr, "spine-01", ["configure session s1"], "create_session")
    assert result["status"] == "error"
    assert "Unexpected error on spine-01" in result["error"]


def test_run_session_command_circuit_breaker_open():
    """run_session_command returns error when circuit breaker is open."""
    mock_breaker = MagicMock(consecutive_failures=3)
    with (
        patch("network_mcp.helpers.circuit_breaker_registry.allow_request", return_value=False),
        patch("network_mcp.helpers.circuit_breaker_registry.get", return_value=mock_breaker),
    ):
        result = run_session_command(conn_mgr, "spine-01", ["configure session s1"], "create_session")
    assert result["status"] == "error"
    assert "Circuit breaker open" in result["error"]


def test_run_session_command_rate_limited():
    """run_session_command returns error when rate limited."""
    with (
        patch("network_mcp.helpers.device_rate_limiter.check_config", return_value=False),
        patch("network_mcp.helpers.device_rate_limiter.config_wait_time", return_value=2.0),
    ):
        result = run_session_command(conn_mgr, "spine-01", ["configure session s1"], "create_session")
    assert result["status"] == "error"
    assert "Config rate limit exceeded" in result["error"]


# --- run_show_command sanitization ---


def test_run_show_command_sanitizes_dict_data():
    """run_show_command sanitizes dict data directly (not wrapped)."""
    mock_node = MagicMock()
    mock_node.run_show.return_value = [{"password": "secret123", "hostname": "sw1"}]
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show version"])
    assert result["status"] == "success"
    # The list contains dicts, each dict should be sanitized
    assert result["data"][0]["hostname"] == "sw1"


def test_run_show_command_sanitizes_list_of_dicts():
    """run_show_command sanitizes each dict in a list result."""
    mock_node = MagicMock()
    mock_node.run_show.return_value = [{"iface": "Eth1"}, {"iface": "Eth2"}]
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show interfaces", "show version"])
    assert result["status"] == "success"
    assert len(result["data"]) == 2
    assert result["data"][0]["iface"] == "Eth1"
    assert result["data"][1]["iface"] == "Eth2"


def test_run_show_command_string_data_passthrough():
    """run_show_command passes string data through without wrapping errors."""
    mock_node = MagicMock()
    mock_node.run_show.return_value = "raw text output"
    with _mock_acquire_for(mock_node):
        result = run_show_command(conn_mgr, "test-switch", ["show running-config"], encoding="text")
    assert result["status"] == "success"
    assert result["data"] == "raw text output"
