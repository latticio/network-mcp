"""Tests for security configuration write tools (ACLs, RADIUS, CoPP)."""

from unittest.mock import patch

import pytest

from network_mcp.server import settings


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for all tests in this module."""
    with patch.object(settings, "net_read_only", False):
        yield


# --- eos_create_acl ---


class TestCreateAcl:
    def test_standard_acl_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        mock_node.config.return_value = [{}]
        result = eos_create_acl(
            "test-switch",
            "MGMT-ACCESS",
            "standard",
            [
                {"seq": 10, "action": "permit", "source": "10.0.0.0/8"},
                {"seq": 20, "action": "deny", "source": "any"},
            ],
        )

        assert result["status"] == "success"
        assert result["action"] == "create_acl"
        mock_node.config.assert_called_once_with(
            [
                "ip access-list MGMT-ACCESS",
                "10 permit 10.0.0.0/8",
                "20 deny any",
            ]
        )

    def test_extended_acl_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        mock_node.config.return_value = [{}]
        result = eos_create_acl(
            "test-switch",
            "WEB-TRAFFIC",
            "extended",
            [
                {
                    "seq": 10,
                    "action": "permit",
                    "protocol": "tcp",
                    "source": "any",
                    "destination": "10.0.0.0/8",
                    "dst_port": "443",
                },
                {
                    "seq": 20,
                    "action": "deny",
                    "protocol": "ip",
                    "source": "any",
                    "destination": "any",
                },
            ],
        )

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "ip access-list WEB-TRAFFIC",
                "10 permit tcp any 10.0.0.0/8 eq 443",
                "20 deny ip any any",
            ]
        )

    def test_extended_acl_without_dst_port(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        mock_node.config.return_value = [{}]
        result = eos_create_acl(
            "test-switch",
            "BLOCK-ALL",
            "extended",
            [{"seq": 10, "action": "deny", "protocol": "ip", "source": "any", "destination": "any"}],
        )

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["ip access-list BLOCK-ALL", "10 deny ip any any"])

    def test_invalid_acl_type(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        result = eos_create_acl(
            "test-switch",
            "MY-ACL",
            "reflexive",
            [{"seq": 10, "action": "permit", "source": "any"}],
        )

        assert result["status"] == "error"
        assert "acl_type" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_action(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        result = eos_create_acl(
            "test-switch",
            "MY-ACL",
            "standard",
            [{"seq": 10, "action": "allow", "source": "any"}],
        )

        assert result["status"] == "error"
        assert "action" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_empty_entries(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        result = eos_create_acl("test-switch", "MY-ACL", "standard", [])

        assert result["status"] == "error"
        assert "entry" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        result = eos_create_acl(
            "test-switch",
            "",
            "standard",
            [{"seq": 10, "action": "permit", "source": "any"}],
        )

        assert result["status"] == "error"
        assert "name" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_seq_number(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        result = eos_create_acl(
            "test-switch",
            "MY-ACL",
            "standard",
            [{"seq": -1, "action": "permit", "source": "any"}],
        )

        assert result["status"] == "error"
        assert "sequence" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_standard_empty_source(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        result = eos_create_acl(
            "test-switch",
            "MY-ACL",
            "standard",
            [{"seq": 10, "action": "permit", "source": ""}],
        )

        assert result["status"] == "error"
        assert "source" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_extended_empty_protocol(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        result = eos_create_acl(
            "test-switch",
            "MY-ACL",
            "extended",
            [{"seq": 10, "action": "permit", "protocol": "", "source": "any", "destination": "any"}],
        )

        assert result["status"] == "error"
        assert "protocol" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_create_acl

        with patch.object(settings, "net_read_only", True):
            result = eos_create_acl(
                "test-switch",
                "MY-ACL",
                "standard",
                [{"seq": 10, "action": "permit", "source": "any"}],
            )
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_modify_acl_rule ---


class TestModifyAclRule:
    def test_add_rule_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_modify_acl_rule

        mock_node.config.return_value = [{}]
        result = eos_modify_acl_rule("test-switch", "MGMT-ACCESS", 30, "add", "permit 192.168.1.0/24")

        assert result["status"] == "success"
        assert result["action"] == "modify_acl_rule"
        mock_node.config.assert_called_once_with(["ip access-list MGMT-ACCESS", "30 permit 192.168.1.0/24"])

    def test_remove_rule_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_modify_acl_rule

        mock_node.config.return_value = [{}]
        result = eos_modify_acl_rule("test-switch", "MGMT-ACCESS", 20, "remove")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["ip access-list MGMT-ACCESS", "no 20"])

    def test_invalid_action(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_modify_acl_rule

        result = eos_modify_acl_rule("test-switch", "MGMT-ACCESS", 10, "delete")

        assert result["status"] == "error"
        assert "action" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_seq(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_modify_acl_rule

        result = eos_modify_acl_rule("test-switch", "MGMT-ACCESS", 0, "add", "permit any")

        assert result["status"] == "error"
        assert "sequence" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_add_with_empty_entry(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_modify_acl_rule

        result = eos_modify_acl_rule("test-switch", "MGMT-ACCESS", 10, "add", "")

        assert result["status"] == "error"
        assert "entry" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_modify_acl_rule

        result = eos_modify_acl_rule("test-switch", "", 10, "add", "permit any")

        assert result["status"] == "error"
        assert "name" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_modify_acl_rule

        with patch.object(settings, "net_read_only", True):
            result = eos_modify_acl_rule("test-switch", "MGMT-ACCESS", 10, "add", "permit any")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_apply_acl ---


class TestApplyAcl:
    def test_success_inbound(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl

        mock_node.config.return_value = [{}]
        result = eos_apply_acl("test-switch", "Ethernet1", "MGMT-ACCESS", "in")

        assert result["status"] == "success"
        assert result["action"] == "apply_acl"
        mock_node.config.assert_called_once_with(["interface Ethernet1", "ip access-group MGMT-ACCESS in"])

    def test_success_outbound(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl

        mock_node.config.return_value = [{}]
        result = eos_apply_acl("test-switch", "Vlan100", "BLOCK-ALL", "out")

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(["interface Vlan100", "ip access-group BLOCK-ALL out"])

    def test_invalid_direction(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl

        result = eos_apply_acl("test-switch", "Ethernet1", "MY-ACL", "both")

        assert result["status"] == "error"
        assert "direction" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_interface(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl

        result = eos_apply_acl("test-switch", "bad-intf", "MY-ACL", "in")

        assert result["status"] == "error"
        assert "interface" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_acl_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl

        result = eos_apply_acl("test-switch", "Ethernet1", "", "in")

        assert result["status"] == "error"
        assert "acl_name" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_apply_acl

        with patch.object(settings, "net_read_only", True):
            result = eos_apply_acl("test-switch", "Ethernet1", "MY-ACL", "in")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_radius_server ---


class TestConfigureRadiusServer:
    def test_success_default_ports(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_radius_server

        mock_node.config.return_value = [{}]
        result = eos_configure_radius_server("test-switch", "10.1.1.100", "s3cret")

        assert result["status"] == "success"
        assert result["action"] == "configure_radius_server"
        mock_node.config.assert_called_once_with(
            ["radius-server host 10.1.1.100 auth-port 1812 acct-port 1813 key 7 s3cret"]
        )

    def test_success_custom_ports(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_radius_server

        mock_node.config.return_value = [{}]
        result = eos_configure_radius_server("test-switch", "10.1.1.100", "mykey", auth_port=1645, acct_port=1646)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            ["radius-server host 10.1.1.100 auth-port 1645 acct-port 1646 key 7 mykey"]
        )

    def test_invalid_ip(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_radius_server

        result = eos_configure_radius_server("test-switch", "not-an-ip", "mykey")

        assert result["status"] == "error"
        assert "ip" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_ip_octet(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_radius_server

        result = eos_configure_radius_server("test-switch", "10.1.1.999", "mykey")

        assert result["status"] == "error"
        assert "ip" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_empty_key(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_radius_server

        result = eos_configure_radius_server("test-switch", "10.1.1.100", "")

        assert result["status"] == "error"
        assert "key" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_auth_port(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_radius_server

        result = eos_configure_radius_server("test-switch", "10.1.1.100", "mykey", auth_port=0)

        assert result["status"] == "error"
        assert "auth_port" in result["error"]
        mock_node.config.assert_not_called()

    def test_invalid_acct_port(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_radius_server

        result = eos_configure_radius_server("test-switch", "10.1.1.100", "mykey", acct_port=70000)

        assert result["status"] == "error"
        assert "acct_port" in result["error"]
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_radius_server

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_radius_server("test-switch", "10.1.1.100", "mykey")
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()


# --- eos_configure_copp ---


class TestConfigureCopp:
    def test_success(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_copp

        mock_node.config.return_value = [{}]
        result = eos_configure_copp("test-switch", "copp-system-acllog", 1000, 500)

        assert result["status"] == "success"
        assert result["action"] == "configure_copp"
        mock_node.config.assert_called_once_with(
            [
                "policy-map copp-system-policy",
                "class copp-system-acllog",
                "shape rate 1000 pps burst-size 500 packets",
            ]
        )

    def test_different_class(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_copp

        mock_node.config.return_value = [{}]
        result = eos_configure_copp("test-switch", "copp-system-bfd", 5000, 2000)

        assert result["status"] == "success"
        mock_node.config.assert_called_once_with(
            [
                "policy-map copp-system-policy",
                "class copp-system-bfd",
                "shape rate 5000 pps burst-size 2000 packets",
            ]
        )

    def test_invalid_class_name(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_copp

        result = eos_configure_copp("test-switch", "", 1000, 500)

        assert result["status"] == "error"
        assert "class_name" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_rate_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_copp

        result = eos_configure_copp("test-switch", "copp-system-acllog", 0, 500)

        assert result["status"] == "error"
        assert "rate" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_rate_negative(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_copp

        result = eos_configure_copp("test-switch", "copp-system-acllog", -10, 500)

        assert result["status"] == "error"
        assert "rate" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_burst_zero(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_copp

        result = eos_configure_copp("test-switch", "copp-system-acllog", 1000, 0)

        assert result["status"] == "error"
        assert "burst" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_invalid_burst_negative(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_copp

        result = eos_configure_copp("test-switch", "copp-system-acllog", 1000, -5)

        assert result["status"] == "error"
        assert "burst" in result["error"].lower()
        mock_node.config.assert_not_called()

    def test_write_blocked_in_read_only_mode(self, mock_conn_mgr, mock_node):
        from network_mcp.tools.security import eos_configure_copp

        with patch.object(settings, "net_read_only", True):
            result = eos_configure_copp("test-switch", "copp-system-acllog", 1000, 500)
            assert result["status"] == "error"
            assert "Write operations are disabled" in result["error"]
            mock_node.config.assert_not_called()
