"""Tests for vendor-agnostic security tools.

Tests net_get_acls, net_get_aaa_status, net_get_snmp_config, net_get_users,
and net_get_copp_policy. Validates response structure, credential redaction,
multi-vendor support, NotSupportedError handling, and error handling.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr

# --- Mock data ---

MOCK_ACLS = [
    {
        "name": "MGMT-ACCESS",
        "type": "extended",
        "rules": [
            {"sequence": 10, "action": "permit", "source": "10.0.0.0/24"},
            {"sequence": 20, "action": "deny", "source": "any"},
        ],
    },
    {
        "name": "DENY-ALL",
        "type": "standard",
        "rules": [
            {"sequence": 10, "action": "deny", "source": "any"},
        ],
    },
]

MOCK_AAA_STATUS = {
    "methods": [
        {"type": "local", "servers": [], "status": "active"},
        {
            "type": "tacacs+",
            "servers": ["10.1.1.100", "10.1.1.101"],
            "status": "active",
            "shared_secret": "supersecretkey",
        },
        {
            "type": "radius",
            "servers": ["10.2.2.100"],
            "status": "inactive",
            "shared_secret": "radiuskey123",
        },
    ],
    "authorization": {"exec": "local", "commands": "tacacs+"},
    "accounting": {"exec": "tacacs+"},
}

MOCK_SNMP_CONFIG = {
    "communities": [
        {"name": "SNMP-RO", "access": "ro", "community": "public_string"},
        {"name": "SNMP-RW", "access": "rw", "community": "private_rw"},
    ],
    "users": [
        {
            "username": "snmpv3user",
            "auth_key": "authpassword123",
            "priv_key": "privpassword456",
            "security_level": "authPriv",
        },
    ],
    "traps": [
        {"host": "10.3.3.100", "community": "trap_community_str"},
    ],
}

MOCK_USER_SESSIONS = [
    {
        "username": "admin",
        "source_ip": "10.0.0.50",
        "session_type": "ssh",
        "idle_time": "00:05:30",
    },
    {
        "username": "operator",
        "source_ip": "",
        "session_type": "console",
        "idle_time": "01:23:45",
    },
    {
        "username": "automation",
        "source_ip": "10.0.0.100",
        "session_type": "api",
        "idle_time": "00:00:02",
    },
]

MOCK_COPP_POLICY = {
    "classes": [
        {
            "name": "copp-system-acllog",
            "rate_limit": "100 pps",
            "matched_packets": 5432,
            "dropped_packets": 12,
        },
        {
            "name": "copp-system-bfd",
            "rate_limit": "15000 pps",
            "matched_packets": 987654,
            "dropped_packets": 0,
        },
    ],
}


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with security getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_acls.return_value = MOCK_ACLS
    driver.get_aaa_status.return_value = MOCK_AAA_STATUS
    driver.get_snmp_config.return_value = MOCK_SNMP_CONFIG
    driver.get_user_sessions.return_value = MOCK_USER_SESSIONS
    driver.get_copp_policy.return_value = MOCK_COPP_POLICY
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# ===========================================================================
# net_get_acls
# ===========================================================================


class TestNetGetAcls:
    """Test the net_get_acls common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_acls

        result = net_get_acls("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_acls

        result = net_get_acls("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_data_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_acls

        result = net_get_acls("spine-01")
        assert set(result["data"].keys()) == {"acls", "acl_count"}

    def test_acl_count(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_acls

        result = net_get_acls("spine-01")
        assert result["data"]["acl_count"] == 2

    def test_acl_content(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_acls

        result = net_get_acls("spine-01")
        acls = result["data"]["acls"]
        assert acls[0]["name"] == "MGMT-ACCESS"
        assert acls[0]["type"] == "extended"
        assert len(acls[0]["rules"]) == 2

    def test_calls_get_acls(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_acls

        net_get_acls("spine-01")
        mock_driver.get_acls.assert_called_once()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_acls

        mock_driver.get_acls.side_effect = NotSupportedError("not available")
        result = net_get_acls("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.security import net_get_acls

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_acls("bad-host")
        assert result["status"] == "error"

    def test_timeout_error(self):
        from network_mcp.tools.common.security import net_get_acls

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_acls("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_empty_acls(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_acls

        mock_driver.get_acls.return_value = []
        result = net_get_acls("spine-01")
        assert result["status"] == "success"
        assert result["data"]["acls"] == []
        assert result["data"]["acl_count"] == 0

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_acls

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_acls("iosxe-router")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_acls

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_acls("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_acls

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_acls("junos-router")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"


# ===========================================================================
# net_get_aaa_status
# ===========================================================================


class TestNetGetAaaStatus:
    """Test the net_get_aaa_status common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_methods_present(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status("spine-01")
        assert "methods" in result["data"]
        assert len(result["data"]["methods"]) == 3

    def test_tacacs_secret_redacted(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status("spine-01")
        tacacs = result["data"]["methods"][1]
        assert tacacs["type"] == "tacacs+"
        assert tacacs["shared_secret"] == "***"
        assert "supersecretkey" not in str(result)

    def test_radius_secret_redacted(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status("spine-01")
        radius = result["data"]["methods"][2]
        assert radius["type"] == "radius"
        assert radius["shared_secret"] == "***"
        assert "radiuskey123" not in str(result)

    def test_non_secret_fields_preserved(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status("spine-01")
        local = result["data"]["methods"][0]
        assert local["type"] == "local"
        assert local["status"] == "active"

    def test_authorization_present(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status("spine-01")
        assert result["data"]["authorization"] == {"exec": "local", "commands": "tacacs+"}

    def test_accounting_present(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result = net_get_aaa_status("spine-01")
        assert result["data"]["accounting"] == {"exec": "tacacs+"}

    def test_calls_get_aaa_status(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_aaa_status

        net_get_aaa_status("spine-01")
        mock_driver.get_aaa_status.assert_called_once()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_aaa_status

        mock_driver.get_aaa_status.side_effect = NotSupportedError("not available")
        result = net_get_aaa_status("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.security import net_get_aaa_status

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_aaa_status("bad-host")
        assert result["status"] == "error"

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_aaa_status

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_aaa_status("iosxe-router")
        assert result["vendor"] == "cisco"

    def test_empty_aaa(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_aaa_status

        mock_driver.get_aaa_status.return_value = {"methods": [], "authorization": {}, "accounting": {}}
        result = net_get_aaa_status("spine-01")
        assert result["status"] == "success"
        assert result["data"]["methods"] == []


# ===========================================================================
# net_get_snmp_config
# ===========================================================================


class TestNetGetSnmpConfig:
    """Test the net_get_snmp_config common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_communities_present(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        assert "communities" in result["data"]
        assert len(result["data"]["communities"]) == 2

    def test_community_strings_redacted(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        for comm in result["data"]["communities"]:
            assert comm["community"] == "***"
        # Community values must be redacted; non-secret name labels preserved
        result_str = str(result["data"]["communities"])
        assert "public_string" not in result_str
        assert "private_rw" not in result_str
        assert "SNMP-RO" in result_str
        assert "SNMP-RW" in result_str

    def test_snmpv3_auth_key_redacted(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        user = result["data"]["users"][0]
        assert user["auth_key"] == "***"
        assert "authpassword123" not in str(result)

    def test_snmpv3_priv_key_redacted(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        user = result["data"]["users"][0]
        assert user["priv_key"] == "***"
        assert "privpassword456" not in str(result)

    def test_snmpv3_non_secret_preserved(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        user = result["data"]["users"][0]
        assert user["username"] == "snmpv3user"
        assert user["security_level"] == "authPriv"

    def test_trap_community_redacted(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        trap = result["data"]["traps"][0]
        assert trap["community"] == "***"
        assert trap["host"] == "10.3.3.100"
        assert "trap_community_str" not in str(result)

    def test_calls_get_snmp_config(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_snmp_config

        net_get_snmp_config("spine-01")
        mock_driver.get_snmp_config.assert_called_once()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_snmp_config

        mock_driver.get_snmp_config.side_effect = NotSupportedError("not available")
        result = net_get_snmp_config("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.security import net_get_snmp_config

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_snmp_config("bad-host")
        assert result["status"] == "error"

    def test_timeout_error(self):
        from network_mcp.tools.common.security import net_get_snmp_config

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_snmp_config("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_empty_snmp_config(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_snmp_config

        mock_driver.get_snmp_config.return_value = {"communities": [], "users": [], "traps": []}
        result = net_get_snmp_config("spine-01")
        assert result["status"] == "success"
        assert result["data"]["communities"] == []
        assert result["data"]["users"] == []
        assert result["data"]["traps"] == []

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_snmp_config

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_snmp_config("iosxe-router")
        assert result["vendor"] == "cisco"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_snmp_config

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_snmp_config("junos-router")
        assert result["vendor"] == "juniper"

    def test_no_secrets_leak_full_output(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result = net_get_snmp_config("spine-01")
        result_str = str(result)
        assert "public_string" not in result_str
        assert "private_rw" not in result_str
        assert "authpassword123" not in result_str
        assert "privpassword456" not in result_str
        assert "trap_community_str" not in result_str


# ===========================================================================
# net_get_users
# ===========================================================================


class TestNetGetUsers:
    """Test the net_get_users common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_users

        result = net_get_users("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_users

        result = net_get_users("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_data_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_users

        result = net_get_users("spine-01")
        assert set(result["data"].keys()) == {"sessions", "session_count"}

    def test_session_count(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_users

        result = net_get_users("spine-01")
        assert result["data"]["session_count"] == 3

    def test_ssh_session(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_users

        result = net_get_users("spine-01")
        ssh = result["data"]["sessions"][0]
        assert ssh["username"] == "admin"
        assert ssh["source_ip"] == "10.0.0.50"
        assert ssh["session_type"] == "ssh"

    def test_console_session(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_users

        result = net_get_users("spine-01")
        console = result["data"]["sessions"][1]
        assert console["username"] == "operator"
        assert console["source_ip"] == ""
        assert console["session_type"] == "console"

    def test_api_session(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_users

        result = net_get_users("spine-01")
        api = result["data"]["sessions"][2]
        assert api["username"] == "automation"
        assert api["session_type"] == "api"

    def test_calls_get_user_sessions(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_users

        net_get_users("spine-01")
        mock_driver.get_user_sessions.assert_called_once()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_users

        mock_driver.get_user_sessions.side_effect = NotSupportedError("not available")
        result = net_get_users("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.security import net_get_users

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_users("bad-host")
        assert result["status"] == "error"

    def test_empty_sessions(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_users

        mock_driver.get_user_sessions.return_value = []
        result = net_get_users("spine-01")
        assert result["status"] == "success"
        assert result["data"]["sessions"] == []
        assert result["data"]["session_count"] == 0

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_users

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_users("iosxe-router")
        assert result["vendor"] == "cisco"

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_users

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_users("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_users

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_users("junos-router")
        assert result["vendor"] == "juniper"


# ===========================================================================
# net_get_copp_policy
# ===========================================================================


class TestNetGetCoppPolicy:
    """Test the net_get_copp_policy common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_copp_policy

        result = net_get_copp_policy("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_copp_policy

        result = net_get_copp_policy("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_classes_present(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_copp_policy

        result = net_get_copp_policy("spine-01")
        assert "classes" in result["data"]
        assert len(result["data"]["classes"]) == 2

    def test_class_content(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_copp_policy

        result = net_get_copp_policy("spine-01")
        cls = result["data"]["classes"][0]
        assert cls["name"] == "copp-system-acllog"
        assert cls["rate_limit"] == "100 pps"
        assert cls["matched_packets"] == 5432
        assert cls["dropped_packets"] == 12

    def test_calls_get_copp_policy(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_copp_policy

        net_get_copp_policy("spine-01")
        mock_driver.get_copp_policy.assert_called_once()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_copp_policy

        mock_driver.get_copp_policy.side_effect = NotSupportedError("not available")
        result = net_get_copp_policy("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.security import net_get_copp_policy

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_copp_policy("bad-host")
        assert result["status"] == "error"

    def test_timeout_error(self):
        from network_mcp.tools.common.security import net_get_copp_policy

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_copp_policy("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_empty_copp(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.security import net_get_copp_policy

        mock_driver.get_copp_policy.return_value = {"classes": []}
        result = net_get_copp_policy("spine-01")
        assert result["status"] == "success"
        assert result["data"]["classes"] == []

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.security import net_get_copp_policy

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_copp_policy("iosxe-router")
        assert result["vendor"] == "cisco"

    def test_juniper_not_supported(self, mock_driver):
        from network_mcp.tools.common.security import net_get_copp_policy

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        mock_driver.get_copp_policy.side_effect = NotSupportedError("JunOS does not support CoPP")
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_copp_policy("junos-router")
        assert result["status"] == "not_supported"
        assert result["vendor"] == "juniper"


# ===========================================================================
# Secret redaction helper tests
# ===========================================================================


class TestRedactSecrets:
    """Test the _redact_secrets helper function."""

    def test_redacts_community(self):
        from network_mcp.tools.common.security import _redact_secrets

        data = {"community": "secret123"}
        result = _redact_secrets(data)
        assert result["community"] == "***"

    def test_redacts_shared_secret(self):
        from network_mcp.tools.common.security import _redact_secrets

        data = {"shared_secret": "mykey"}
        result = _redact_secrets(data)
        assert result["shared_secret"] == "***"

    def test_redacts_password(self):
        from network_mcp.tools.common.security import _redact_secrets

        data = {"password": "admin123"}
        result = _redact_secrets(data)
        assert result["password"] == "***"

    def test_redacts_auth_key(self):
        from network_mcp.tools.common.security import _redact_secrets

        data = {"auth_key": "authpass"}
        result = _redact_secrets(data)
        assert result["auth_key"] == "***"

    def test_redacts_priv_key(self):
        from network_mcp.tools.common.security import _redact_secrets

        data = {"priv_key": "privpass"}
        result = _redact_secrets(data)
        assert result["priv_key"] == "***"

    def test_preserves_non_secret(self):
        from network_mcp.tools.common.security import _redact_secrets

        data = {"hostname": "switch-01", "status": "active"}
        result = _redact_secrets(data)
        assert result == data

    def test_nested_dict(self):
        from network_mcp.tools.common.security import _redact_secrets

        data = {"server": {"host": "10.0.0.1", "key": "secret"}}
        result = _redact_secrets(data)
        assert result["server"]["host"] == "10.0.0.1"
        assert result["server"]["key"] == "***"

    def test_list_of_dicts(self):
        from network_mcp.tools.common.security import _redact_secrets

        data = {"items": [{"name": "a", "secret": "s1"}, {"name": "b", "secret": "s2"}]}
        result = _redact_secrets(data)
        assert result["items"][0]["secret"] == "***"
        assert result["items"][1]["secret"] == "***"
        assert result["items"][0]["name"] == "a"

    def test_depth_limit(self):
        from network_mcp.tools.common.security import _redact_secrets

        # Build deeply nested structure (25 levels)
        data: dict = {"community": "secret"}
        for _ in range(25):
            data = {"nested": data}
        result = _redact_secrets(data)
        # Should not crash; innermost value preserved due to depth limit
        assert isinstance(result, dict)

    def test_non_dict_input(self):
        from network_mcp.tools.common.security import _redact_secrets

        assert _redact_secrets("hello") == "hello"
        assert _redact_secrets(42) == 42
        assert _redact_secrets(None) is None

    def test_empty_dict(self):
        from network_mcp.tools.common.security import _redact_secrets

        assert _redact_secrets({}) == {}

    def test_empty_list(self):
        from network_mcp.tools.common.security import _redact_secrets

        assert _redact_secrets([]) == []


# ===========================================================================
# Cross-tool secret leak verification
# ===========================================================================


class TestNoSecretsLeak:
    """Verify no secrets leak across all security tool outputs."""

    ALL_SECRETS = [
        "supersecretkey",
        "radiuskey123",
        "public_string",
        "private_rw",
        "authpassword123",
        "privpassword456",
        "trap_community_str",
    ]

    def test_aaa_no_secrets(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_aaa_status

        result_str = str(net_get_aaa_status("spine-01"))
        for secret in self.ALL_SECRETS:
            assert secret not in result_str, f"Secret '{secret}' leaked in AAA output"

    def test_snmp_no_secrets(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_snmp_config

        result_str = str(net_get_snmp_config("spine-01"))
        for secret in self.ALL_SECRETS:
            assert secret not in result_str, f"Secret '{secret}' leaked in SNMP output"

    def test_acls_no_secrets(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_acls

        result_str = str(net_get_acls("spine-01"))
        for secret in self.ALL_SECRETS:
            assert secret not in result_str, f"Secret '{secret}' leaked in ACL output"

    def test_users_no_secrets(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_users

        result_str = str(net_get_users("spine-01"))
        for secret in self.ALL_SECRETS:
            assert secret not in result_str, f"Secret '{secret}' leaked in users output"

    def test_copp_no_secrets(self, mock_common_conn_mgr):
        from network_mcp.tools.common.security import net_get_copp_policy

        result_str = str(net_get_copp_policy("spine-01"))
        for secret in self.ALL_SECRETS:
            assert secret not in result_str, f"Secret '{secret}' leaked in CoPP output"
