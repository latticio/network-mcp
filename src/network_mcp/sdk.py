"""Plugin SDK — public API for plugin authors.

Provides:
- MockDriverFactory: Creates mock NetworkDriver instances pre-loaded with
  configurable response data for testing plugin tools.
- ToolTestHarness: Wraps a tool function with a mock connection manager
  and provides assertion helpers (assert_success, assert_error, assert_field).
- register_tools: Helper that plugins can use instead of importing mcp directly.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from mcp.types import ToolAnnotations

logger = logging.getLogger("network-mcp")


# ---------------------------------------------------------------------------
# MockDriverFactory
# ---------------------------------------------------------------------------


class _MockNetworkDriver:
    """A configurable mock NetworkDriver for plugin testing.

    Satisfies the NetworkDriver protocol with pre-loaded response data.
    Plugin authors configure responses via the ``responses`` dict which maps
    command strings to their return values.
    """

    def __init__(
        self,
        *,
        platform: str = "eos",
        vendor: str = "arista",
        responses: dict[str, Any] | None = None,
        config_responses: dict[str, list[str]] | None = None,
        getter_responses: dict[str, Any] | None = None,
    ) -> None:
        self._platform = platform
        self._vendor = vendor
        self._connected = False
        self._host: str | None = None
        self._responses: dict[str, Any] = responses or {}
        self._config_responses: dict[str, list[str]] = config_responses or {}
        self._getter_responses: dict[str, Any] = getter_responses or {}

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def vendor(self) -> str:
        return self._vendor

    def connect(self, **kwargs: Any) -> None:
        self._host = kwargs.get("host", "mock-device")
        self._connected = True

    def close(self) -> None:
        self._connected = False
        self._host = None

    def is_connected(self) -> bool:
        return self._connected

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        results: list[dict] = []
        for cmd in commands:
            if cmd in self._responses:
                results.append(copy.deepcopy(self._responses[cmd]))
            else:
                results.append({})
        return results

    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show — backward compatibility with pyeapi.Node."""
        return self.run_show(commands, encoding=encoding)

    def run_config(self, commands: list[str]) -> list[str]:
        results: list[str] = []
        for cmd in commands:
            if cmd in self._config_responses:
                results.extend(self._config_responses[cmd])
            else:
                results.append("")
        return results

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config — backward compatibility with pyeapi.Node."""
        return self.run_config(commands)

    def reconnect(self) -> None:
        pass

    def capabilities(self) -> dict[str, bool]:
        return {"config_sessions": True, "gnmi": False}

    def __getattr__(self, name: str) -> Any:
        """Proxy getter methods to getter_responses."""
        if name.startswith("get_"):
            if name in self._getter_responses:
                return lambda *args, **kwargs: copy.deepcopy(self._getter_responses[name])
            return lambda *args, **kwargs: {}
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


class MockDriverFactory:
    """Factory for creating mock NetworkDriver instances for plugin testing.

    Example::

        factory = MockDriverFactory()
        driver = factory.create(
            platform="eos",
            responses={"show version": {"modelName": "DCS-7050TX3-48C8", "version": "4.32.1F"}},
        )
        result = driver.run_show(["show version"])
        assert result[0]["modelName"] == "DCS-7050TX3-48C8"
    """

    @staticmethod
    def create(
        *,
        platform: str = "eos",
        vendor: str = "arista",
        host: str = "mock-device",
        responses: dict[str, Any] | None = None,
        config_responses: dict[str, list[str]] | None = None,
        getter_responses: dict[str, Any] | None = None,
    ) -> _MockNetworkDriver:
        """Create a mock NetworkDriver pre-loaded with response data.

        Args:
            platform: Platform identifier (e.g., 'eos', 'iosxe', 'nxos', 'junos').
            vendor: Vendor name (e.g., 'arista', 'cisco', 'juniper').
            host: Hostname to use when connecting.
            responses: Dict mapping show command strings to their JSON response dicts.
            config_responses: Dict mapping config command strings to their response lists.
            getter_responses: Dict mapping getter method names (e.g., 'get_facts') to return values.

        Returns:
            A mock NetworkDriver instance that is already connected.
        """
        driver = _MockNetworkDriver(
            platform=platform,
            vendor=vendor,
            responses=responses,
            config_responses=config_responses,
            getter_responses=getter_responses,
        )
        driver.connect(host=host)
        return driver


# ---------------------------------------------------------------------------
# ToolTestHarness
# ---------------------------------------------------------------------------


class ToolTestHarness:
    """Test harness for plugin tools.

    Wraps a tool function with a mock connection manager and provides
    assertion helpers for verifying tool output.

    Example::

        from network_mcp.sdk import MockDriverFactory, ToolTestHarness

        def my_tool(host: str) -> dict:
            from network_mcp.helpers import run_show_command
            from network_mcp.server import conn_mgr
            result = run_show_command(conn_mgr, host, ["show version"])
            if result["status"] == "error":
                return result
            return {"status": "success", "device": host, "data": result["data"][0]}

        driver = MockDriverFactory.create(
            responses={"show version": {"version": "4.32.1F"}},
        )
        harness = ToolTestHarness(my_tool, driver)
        result = harness.call("switch-01")
        harness.assert_success(result)
        harness.assert_field(result, "data.version", "4.32.1F")
    """

    def __init__(self, tool_fn: Callable[..., dict], driver: _MockNetworkDriver) -> None:
        """Initialize the test harness.

        Args:
            tool_fn: The tool function to test.
            driver: A mock driver created by MockDriverFactory.
        """
        self._tool_fn = tool_fn
        self._driver = driver
        self._mock_conn_mgr = MagicMock()
        self._mock_conn_mgr.get_node.return_value = self._driver
        self._mock_conn_mgr.get_driver.return_value = self._driver
        self._mock_conn_mgr.get_platform.return_value = self._driver.platform
        self._mock_conn_mgr.list_devices.return_value = [self._driver._host or "mock-device"]

    @property
    def mock_conn_mgr(self) -> MagicMock:
        """The mock ConnectionManager used by this harness."""
        return self._mock_conn_mgr

    def call(self, *args: Any, **kwargs: Any) -> dict:
        """Call the tool function with the mock connection manager injected.

        Patches ``network_mcp.server.conn_mgr`` so that ``run_show_command``
        and ``run_config_command`` use the mock driver.

        Returns:
            The tool function's return dict.
        """
        from unittest.mock import patch

        from network_mcp.server import conn_mgr as real_conn_mgr

        with patch.object(real_conn_mgr, "get_node", self._mock_conn_mgr.get_node):
            with patch.object(real_conn_mgr, "get_driver", self._mock_conn_mgr.get_driver):
                with patch.object(real_conn_mgr, "get_platform", self._mock_conn_mgr.get_platform):
                    with patch.object(real_conn_mgr, "list_devices", self._mock_conn_mgr.list_devices):
                        return self._tool_fn(*args, **kwargs)

    @staticmethod
    def assert_success(result: dict) -> None:
        """Assert that a tool result has status 'success'.

        Raises:
            AssertionError: If the result status is not 'success'.
        """
        assert result.get("status") == "success", (  # noqa: S101
            f"Expected status='success', got status='{result.get('status')}'"
            + (f", error='{result.get('error')}'" if result.get("error") else "")
        )

    @staticmethod
    def assert_error(result: dict, error_contains: str | None = None) -> None:
        """Assert that a tool result has status 'error'.

        Args:
            error_contains: If provided, asserts that the error message contains this substring.

        Raises:
            AssertionError: If the result status is not 'error' or error text doesn't match.
        """
        assert result.get("status") == "error", (  # noqa: S101
            f"Expected status='error', got status='{result.get('status')}'"
        )
        if error_contains is not None:
            error_msg = result.get("error", "")
            assert error_contains in error_msg, (  # noqa: S101
                f"Expected error to contain '{error_contains}', got: '{error_msg}'"
            )

    @staticmethod
    def assert_field(result: dict, path: str, expected: Any) -> None:
        """Assert that a nested field in the result matches the expected value.

        Supports dot-separated paths (e.g., 'data.version', 'data.interfaces.Ethernet1.is_up').

        Args:
            result: The tool result dict.
            path: Dot-separated path to the field.
            expected: Expected value.

        Raises:
            AssertionError: If the field doesn't exist or doesn't match.
        """
        parts = path.split(".")
        current = result
        for i, part in enumerate(parts):
            if not isinstance(current, dict) or part not in current:
                traversed = ".".join(parts[: i + 1])
                available = list(current.keys()) if isinstance(current, dict) else type(current).__name__
                raise AssertionError(f"Field '{traversed}' not found in result. Available keys: {available}")
            current = current[part]
        assert current == expected, f"Field '{path}': expected {expected!r}, got {current!r}"  # noqa: S101


# ---------------------------------------------------------------------------
# register_tools helper
# ---------------------------------------------------------------------------


def register_tools(
    setup_fn: Callable,
    *,
    annotations: ToolAnnotations | None = None,
) -> Callable:
    """Register plugin tools without importing mcp directly.

    This is a convenience wrapper that plugins can use instead of doing
    ``from network_mcp.server import mcp``. It calls the setup function
    with the live MCP server instance.

    Args:
        setup_fn: A callable that receives the FastMCP instance and registers
                  tools using ``@mcp.tool()`` decorators.
        annotations: Default tool annotations to apply (currently informational;
                     individual tools should set their own annotations).

    Returns:
        The setup function (unchanged), for decorator-style usage.

    Example::

        from network_mcp.sdk import register_tools

        def setup(mcp):
            @mcp.tool()
            def my_custom_tool(host: str) -> dict:
                return {"status": "success", "device": host, "data": {}}

        register_tools(setup)
    """
    from network_mcp.server import mcp

    setup_fn(mcp)
    return setup_fn
