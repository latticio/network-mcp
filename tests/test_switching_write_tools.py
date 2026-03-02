"""Tests for switching configuration write tools (LAG, STP, BPDU guard, storm control)."""

from unittest.mock import patch

import pytest

from network_mcp.server import settings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


# --- eos_create_port_channel ---


class TestCreatePortChannel:
    def test_success_lacp_active(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        mock_node.config.return_value = [{}]
        result = eos_create_port_channel("test-switch", 10, "active", ["Ethernet1", "Ethernet2"])

        assert result["status"] == "success"
        assert result["action"] == "create_port_channel"
        mock_node.config.assert_called_once_with(
            [
                "interface Port-Channel10",
                "interface Ethernet1",
                "channel-group 10 mode active",
                "interface Ethernet2",
                "channel-group 10 mode active",
            ]
        )

    def test_success_lacp_passive(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        mock_node.config.return_value = [{}]
        result = eos_create_port_channel("test-switch", 5, "passive", ["Ethernet3"])

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "interface Port-Channel5",
                "interface Ethernet3",
                "channel-group 5 mode passive",
            ]
        )

    def test_success_static_on(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        mock_node.config.return_value = [{}]
        result = eos_create_port_channel("test-switch", 1, "on", ["Ethernet1"])

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "interface Port-Channel1",
                "interface Ethernet1",
                "channel-group 1 mode on",
            ]
        )

    def test_multiple_members_commands(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        mock_node.config.return_value = [{}]
        members = ["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"]
        result = eos_create_port_channel("test-switch", 100, "active", members)

        assert result["status"] == "success"
        expected = [
            "interface Port-Channel100",
            "interface Ethernet1",
            "channel-group 100 mode active",
            "interface Ethernet2",
            "channel-group 100 mode active",
            "interface Ethernet3",
            "channel-group 100 mode active",
            "interface Ethernet4",
            "channel-group 100 mode active",
        ]
        mock_node.config.assert_called_once_with(expected)

    def test_invalid_channel_id_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        result = eos_create_port_channel("test-switch", 0, "active", ["Ethernet1"])

        assert result["status"] == "error"
        assert "Port-channel ID" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_channel_id_too_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        result = eos_create_port_channel("test-switch", 2001, "active", ["Ethernet1"])

        assert result["status"] == "error"
        assert "Port-channel ID" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        result = eos_create_port_channel("test-switch", 10, "desirable", ["Ethernet1"])

        assert result["status"] == "error"
        assert "Invalid mode" in result["error"]
        mock_node.config.assert_not_called()

    def test_empty_members(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        result = eos_create_port_channel("test-switch", 10, "active", [])

        assert result["status"] == "error"
        assert "member" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_member_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        result = eos_create_port_channel("test-switch", 10, "active", ["Ethernet1", "bad-intf"])

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_create_port_channel

        with patch.object(settings, "net_read_only", True):
            result = eos_create_port_channel("test-switch", 10, "active", ["Ethernet1"])
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_add_port_channel_member ---


class TestAddPortChannelMember:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_add_port_channel_member

        mock_node.config.return_value = [{}]
        result = eos_add_port_channel_member("test-switch", "Ethernet3", 10)

        assert result["status"] == "success"
        assert result["action"] == "add_port_channel_member"
        mock_node.config.assert_called_once_with(["interface Ethernet3", "channel-group 10 mode active"])

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_add_port_channel_member

        result = eos_add_port_channel_member("test-switch", "bad-intf", 10)

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_channel_id(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_add_port_channel_member

        result = eos_add_port_channel_member("test-switch", "Ethernet1", 2001)

        assert result["status"] == "error"
        assert "Port-channel ID" in result["error"]
        mock_node.config.assert_not_called()

    def test_channel_id_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_add_port_channel_member

        result = eos_add_port_channel_member("test-switch", "Ethernet1", 0)

        assert result["status"] == "error"
        assert "Port-channel ID" in result["error"]
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_add_port_channel_member

        with patch.object(settings, "net_read_only", True):
            result = eos_add_port_channel_member("test-switch", "Ethernet1", 10)
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_stp ---


class TestConfigureStp:
    def test_success_mstp(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        mock_node.config.return_value = [{}]
        result = eos_configure_stp("test-switch", "mstp", 4096)

        assert result["status"] == "success"
        assert result["action"] == "configure_stp"
        mock_node.config.assert_called_once_with(["spanning-tree mode mstp", "spanning-tree priority 4096"])

    def test_success_rstp(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        mock_node.config.return_value = [{}]
        result = eos_configure_stp("test-switch", "rstp", 32768)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["spanning-tree mode rstp", "spanning-tree priority 32768"])

    def test_success_rapid_pvst(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        mock_node.config.return_value = [{}]
        result = eos_configure_stp("test-switch", "rapid-pvst", 8192)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["spanning-tree mode rapid-pvst", "spanning-tree priority 8192"])

    def test_priority_zero_uses_root_primary(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        mock_node.config.return_value = [{}]
        result = eos_configure_stp("test-switch", "mstp", 0)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["spanning-tree mode mstp", "spanning-tree root primary"])

    def test_success_with_instances(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        mock_node.config.return_value = [{}]
        result = eos_configure_stp(
            "test-switch", "mstp", 4096, instances=["instance 1 vlan 100-200", "instance 2 vlan 300-400"]
        )

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "spanning-tree mode mstp",
                "spanning-tree priority 4096",
                "spanning-tree instance 1 vlan 100-200",
                "spanning-tree instance 2 vlan 300-400",
            ]
        )

    def test_default_priority(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        mock_node.config.return_value = [{}]
        result = eos_configure_stp("test-switch", "rstp")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["spanning-tree mode rstp", "spanning-tree priority 32768"])

    def test_invalid_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        result = eos_configure_stp("test-switch", "pvst", 4096)

        assert result["status"] == "error"
        assert "Invalid STP mode" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_priority_not_multiple_of_4096(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        result = eos_configure_stp("test-switch", "mstp", 1000)

        assert result["status"] == "error"
        assert "multiple of 4096" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_priority_too_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        result = eos_configure_stp("test-switch", "mstp", 65536)

        assert result["status"] == "error"
        assert "STP priority" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_priority_negative(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        result = eos_configure_stp("test-switch", "mstp", -1)

        assert result["status"] == "error"
        assert "STP priority" in result["error"]
        mock_node.config.assert_not_called()

    def test_priority_max_valid(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        mock_node.config.return_value = [{}]
        result = eos_configure_stp("test-switch", "rstp", 61440)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["spanning-tree mode rstp", "spanning-tree priority 61440"])

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_stp

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_stp("test-switch", "mstp", 4096)
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_bpdu_guard ---


class TestConfigureBpduGuard:
    def test_enable(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_bpdu_guard

        mock_node.config.return_value = [{}]
        result = eos_configure_bpdu_guard("test-switch", "Ethernet1", True)

        assert result["status"] == "success"
        assert result["action"] == "configure_bpdu_guard"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "spanning-tree bpduguard enable"])

    def test_disable(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_bpdu_guard

        mock_node.config.return_value = [{}]
        result = eos_configure_bpdu_guard("test-switch", "Ethernet1", False)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "no spanning-tree bpduguard"])

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_bpdu_guard

        result = eos_configure_bpdu_guard("test-switch", "bad-intf", True)

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_bpdu_guard

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_bpdu_guard("test-switch", "Ethernet1", True)
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_storm_control ---


class TestConfigureStormControl:
    def test_success_broadcast(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        mock_node.config.return_value = [{}]
        result = eos_configure_storm_control("test-switch", "Ethernet1", 10.0, "broadcast")

        assert result["status"] == "success"
        assert result["action"] == "configure_storm_control"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "storm-control broadcast level 10.0"])

    def test_success_multicast(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        mock_node.config.return_value = [{}]
        result = eos_configure_storm_control("test-switch", "Ethernet1", 50.5, "multicast")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "storm-control multicast level 50.5"])

    def test_success_unicast(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        mock_node.config.return_value = [{}]
        result = eos_configure_storm_control("test-switch", "Ethernet1", 0.01, "unicast")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "storm-control unicast level 0.01"])

    def test_success_100_percent(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        mock_node.config.return_value = [{}]
        result = eos_configure_storm_control("test-switch", "Ethernet1", 100.0, "broadcast")

        assert result["status"] == "success"

    def test_invalid_level_too_low(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        result = eos_configure_storm_control("test-switch", "Ethernet1", 0.0, "broadcast")

        assert result["status"] == "error"
        assert "level" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_level_too_high(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        result = eos_configure_storm_control("test-switch", "Ethernet1", 100.1, "broadcast")

        assert result["status"] == "error"
        assert "level" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_traffic_type(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        result = eos_configure_storm_control("test-switch", "Ethernet1", 10.0, "unknown")

        assert result["status"] == "error"
        assert "traffic type" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        result = eos_configure_storm_control("test-switch", "bad-intf", 10.0, "broadcast")

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.switching import eos_configure_storm_control

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_storm_control("test-switch", "Ethernet1", 10.0, "broadcast")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- Validator unit tests ---


class TestValidatePortChannelId:
    def test_valid_ids(self):
        from network_mcp.helpers import validate_port_channel_id

        assert validate_port_channel_id(1) is None
        assert validate_port_channel_id(1000) is None
        assert validate_port_channel_id(2000) is None

    def test_invalid_zero(self):
        from network_mcp.helpers import validate_port_channel_id

        err = validate_port_channel_id(0)
        assert err is not None
        assert "1-2000" in err

    def test_invalid_too_high(self):
        from network_mcp.helpers import validate_port_channel_id

        err = validate_port_channel_id(2001)
        assert err is not None
        assert "1-2000" in err

    def test_invalid_negative(self):
        from network_mcp.helpers import validate_port_channel_id

        err = validate_port_channel_id(-1)
        assert err is not None
        assert "1-2000" in err


class TestValidateStpPriority:
    def test_valid_priorities(self):
        from network_mcp.helpers import validate_stp_priority

        for priority in [0, 4096, 8192, 16384, 32768, 61440]:
            assert validate_stp_priority(priority) is None, f"Priority {priority} should be valid"

    def test_invalid_not_multiple_of_4096(self):
        from network_mcp.helpers import validate_stp_priority

        err = validate_stp_priority(1000)
        assert err is not None
        assert "multiple of 4096" in err

    def test_invalid_too_high(self):
        from network_mcp.helpers import validate_stp_priority

        err = validate_stp_priority(65536)
        assert err is not None
        assert "0-61440" in err

    def test_invalid_negative(self):
        from network_mcp.helpers import validate_stp_priority

        err = validate_stp_priority(-1)
        assert err is not None

    def test_invalid_4097(self):
        from network_mcp.helpers import validate_stp_priority

        err = validate_stp_priority(4097)
        assert err is not None
        assert "multiple of 4096" in err
