"""Tests for the Plugin SDK (network_mcp.sdk)."""

import pytest  # noqa: I001

from network_mcp.sdk import MockDriverFactory, ToolTestHarness, register_tools


# ---------------------------------------------------------------------------
# MockDriverFactory
# ---------------------------------------------------------------------------


class TestMockDriverFactory:
    """Test MockDriverFactory.create() and the resulting mock driver."""

    def test_create_defaults(self):
        """Default factory creates an EOS driver that is connected."""
        driver = MockDriverFactory.create()
        assert driver.platform == "eos"
        assert driver.vendor == "arista"
        assert driver.is_connected()

    def test_create_custom_platform(self):
        """Factory respects custom platform and vendor."""
        driver = MockDriverFactory.create(platform="nxos", vendor="cisco")
        assert driver.platform == "nxos"
        assert driver.vendor == "cisco"

    def test_create_with_host(self):
        """Factory sets the host on the driver."""
        driver = MockDriverFactory.create(host="spine-01")
        assert driver._host == "spine-01"

    def test_run_show_with_responses(self):
        """run_show returns configured responses for known commands."""
        driver = MockDriverFactory.create(
            responses={
                "show version": {"modelName": "DCS-7050TX3-48C8", "version": "4.32.1F"},
                "show vlan": {"vlans": {"100": {"name": "PROD"}}},
            },
        )
        result = driver.run_show(["show version"])
        assert len(result) == 1
        assert result[0]["version"] == "4.32.1F"

    def test_run_show_unknown_command(self):
        """run_show returns empty dict for unknown commands."""
        driver = MockDriverFactory.create(responses={"show version": {"version": "1.0"}})
        result = driver.run_show(["show unknown-command"])
        assert result == [{}]

    def test_run_show_multiple_commands(self):
        """run_show handles multiple commands in a single call."""
        driver = MockDriverFactory.create(
            responses={
                "show version": {"version": "4.32.1F"},
                "show hostname": {"hostname": "spine-01"},
            },
        )
        result = driver.run_show(["show version", "show hostname"])
        assert len(result) == 2
        assert result[0]["version"] == "4.32.1F"
        assert result[1]["hostname"] == "spine-01"

    def test_run_show_deep_copies(self):
        """run_show returns deep copies to prevent cross-call mutation."""
        driver = MockDriverFactory.create(
            responses={"show version": {"data": {"nested": [1, 2, 3]}}},
        )
        result1 = driver.run_show(["show version"])
        result1[0]["data"]["nested"].append(4)
        result2 = driver.run_show(["show version"])
        assert result2[0]["data"]["nested"] == [1, 2, 3]

    def test_run_commands_alias(self):
        """run_commands is an alias for run_show."""
        driver = MockDriverFactory.create(
            responses={"show version": {"version": "4.32.1F"}},
        )
        result = driver.run_commands(["show version"])
        assert result[0]["version"] == "4.32.1F"

    def test_run_config_with_responses(self):
        """run_config returns configured responses."""
        driver = MockDriverFactory.create(
            config_responses={"interface Ethernet1": ["ok"]},
        )
        result = driver.run_config(["interface Ethernet1"])
        assert result == ["ok"]

    def test_run_config_unknown_command(self):
        """run_config returns empty string for unknown commands."""
        driver = MockDriverFactory.create()
        result = driver.run_config(["vlan 999"])
        assert result == [""]

    def test_config_alias(self):
        """config() is an alias for run_config()."""
        driver = MockDriverFactory.create(
            config_responses={"vlan 100": [""]},
        )
        result = driver.config(["vlan 100"])
        assert result == [""]

    def test_getter_responses(self):
        """Getter methods return configured responses."""
        driver = MockDriverFactory.create(
            getter_responses={
                "get_facts": {"hostname": "spine-01", "model": "DCS-7050TX3"},
                "get_interfaces": {"Ethernet1": {"is_up": True}},
            },
        )
        facts = driver.get_facts()
        assert facts["hostname"] == "spine-01"
        interfaces = driver.get_interfaces()
        assert interfaces["Ethernet1"]["is_up"] is True

    def test_getter_unknown_returns_empty(self):
        """Unknown getter methods return empty dict."""
        driver = MockDriverFactory.create()
        result = driver.get_vlans()
        assert result == {}

    def test_getter_deep_copies(self):
        """Getter responses are deep-copied to prevent mutation."""
        driver = MockDriverFactory.create(
            getter_responses={"get_facts": {"hostname": "spine-01"}},
        )
        result1 = driver.get_facts()
        result1["hostname"] = "mutated"
        result2 = driver.get_facts()
        assert result2["hostname"] == "spine-01"

    def test_connect_close_cycle(self):
        """Driver supports connect/close lifecycle."""
        driver = MockDriverFactory.create()
        assert driver.is_connected()
        driver.close()
        assert not driver.is_connected()
        driver.connect(host="new-device")
        assert driver.is_connected()
        assert driver._host == "new-device"

    def test_capabilities(self):
        """capabilities() returns a dict of feature flags."""
        driver = MockDriverFactory.create()
        caps = driver.capabilities()
        assert isinstance(caps, dict)
        assert "config_sessions" in caps

    def test_reconnect(self):
        """reconnect() is a no-op and doesn't raise."""
        driver = MockDriverFactory.create()
        driver.reconnect()  # should not raise

    def test_non_getter_attribute_raises(self):
        """Accessing non-getter, non-existent attributes raises AttributeError."""
        driver = MockDriverFactory.create()
        with pytest.raises(AttributeError):
            driver.nonexistent_method  # noqa: B018


# ---------------------------------------------------------------------------
# ToolTestHarness
# ---------------------------------------------------------------------------


class TestToolTestHarness:
    """Test ToolTestHarness with a simple tool function."""

    @staticmethod
    def _sample_tool(host: str) -> dict:
        """A simple tool for testing."""
        return {"status": "success", "device": host, "data": {"version": "4.32.1F"}}

    @staticmethod
    def _error_tool(host: str) -> dict:
        """A tool that always returns an error."""
        return {"status": "error", "device": host, "error": "connection timeout"}

    def test_assert_success(self):
        driver = MockDriverFactory.create()
        harness = ToolTestHarness(self._sample_tool, driver)
        result = self._sample_tool("switch-01")
        harness.assert_success(result)

    def test_assert_success_fails_on_error(self):
        driver = MockDriverFactory.create()
        harness = ToolTestHarness(self._error_tool, driver)
        result = self._error_tool("switch-01")
        with pytest.raises(AssertionError, match="Expected status='success'"):
            harness.assert_success(result)

    def test_assert_error(self):
        driver = MockDriverFactory.create()
        harness = ToolTestHarness(self._error_tool, driver)
        result = self._error_tool("switch-01")
        harness.assert_error(result)

    def test_assert_error_with_contains(self):
        driver = MockDriverFactory.create()
        harness = ToolTestHarness(self._error_tool, driver)
        result = self._error_tool("switch-01")
        harness.assert_error(result, error_contains="timeout")

    def test_assert_error_contains_mismatch(self):
        driver = MockDriverFactory.create()
        harness = ToolTestHarness(self._error_tool, driver)
        result = self._error_tool("switch-01")
        with pytest.raises(AssertionError, match="Expected error to contain"):
            harness.assert_error(result, error_contains="auth failure")

    def test_assert_error_fails_on_success(self):
        driver = MockDriverFactory.create()
        harness = ToolTestHarness(self._sample_tool, driver)
        result = self._sample_tool("switch-01")
        with pytest.raises(AssertionError, match="Expected status='error'"):
            harness.assert_error(result)

    def test_assert_field_simple(self):
        result = {"status": "success", "device": "sw1", "data": {"version": "4.32.1F"}}
        ToolTestHarness.assert_field(result, "status", "success")
        ToolTestHarness.assert_field(result, "device", "sw1")

    def test_assert_field_nested(self):
        result = {"status": "success", "data": {"version": "4.32.1F"}}
        ToolTestHarness.assert_field(result, "data.version", "4.32.1F")

    def test_assert_field_deep_nested(self):
        result = {"data": {"interfaces": {"Ethernet1": {"is_up": True}}}}
        ToolTestHarness.assert_field(result, "data.interfaces.Ethernet1.is_up", True)

    def test_assert_field_missing_key(self):
        result = {"status": "success"}
        with pytest.raises(AssertionError, match="not found"):
            ToolTestHarness.assert_field(result, "data.version", "1.0")

    def test_assert_field_value_mismatch(self):
        result = {"data": {"version": "4.32.1F"}}
        with pytest.raises(AssertionError, match="expected"):
            ToolTestHarness.assert_field(result, "data.version", "5.0.0")

    def test_mock_conn_mgr_property(self):
        """mock_conn_mgr exposes the underlying MagicMock."""
        driver = MockDriverFactory.create()
        harness = ToolTestHarness(self._sample_tool, driver)
        assert harness.mock_conn_mgr is not None
        assert harness.mock_conn_mgr.get_node.return_value is driver

    def test_call_patches_conn_mgr(self):
        """call() patches conn_mgr methods on the real server instance."""
        driver = MockDriverFactory.create(
            responses={"show version": {"version": "4.32.1F"}},
        )

        def tool_using_conn_mgr(host: str) -> dict:
            from network_mcp.server import conn_mgr

            node = conn_mgr.get_node(host)
            result = node.run_show(["show version"])
            return {"status": "success", "device": host, "data": result[0]}

        harness = ToolTestHarness(tool_using_conn_mgr, driver)
        result = harness.call("spine-01")
        harness.assert_success(result)
        harness.assert_field(result, "data.version", "4.32.1F")


# ---------------------------------------------------------------------------
# register_tools
# ---------------------------------------------------------------------------


class TestRegisterTools:
    """Test the register_tools helper."""

    def test_register_tools_calls_setup(self):
        """register_tools calls the setup function with the mcp instance."""
        setup_called = []

        def my_setup(mcp_instance):
            setup_called.append(mcp_instance)

        register_tools(my_setup)
        assert len(setup_called) == 1

    def test_register_tools_returns_setup_fn(self):
        """register_tools returns the setup function for decorator usage."""

        def my_setup(mcp_instance):
            pass

        result = register_tools(my_setup)
        assert result is my_setup
