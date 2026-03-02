"""Tests for vendor-agnostic VLAN tools (read and write).

Validates pagination, VLAN ID validation, NotSupportedError handling,
read-only mode enforcement, CLI injection prevention, and standard response structure.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr, settings

# --- Mock data ---

MOCK_VLANS = [
    {"vlan_id": 1, "name": "default", "status": "active", "interfaces": ["Ethernet1", "Ethernet2"]},
    {"vlan_id": 10, "name": "MGMT", "status": "active", "interfaces": ["Management1"]},
    {"vlan_id": 20, "name": "SERVERS", "status": "active", "interfaces": ["Ethernet3", "Ethernet4"]},
    {"vlan_id": 30, "name": "USERS", "status": "active", "interfaces": ["Ethernet5"]},
    {"vlan_id": 100, "name": "DMZ", "status": "active", "interfaces": []},
    {"vlan_id": 200, "name": "BACKUP", "status": "suspend", "interfaces": []},
    {"vlan_id": 300, "name": "IOT", "status": "active", "interfaces": ["Ethernet6"]},
    {"vlan_id": 400, "name": "VOICE", "status": "active", "interfaces": ["Ethernet7", "Ethernet8"]},
    {"vlan_id": 500, "name": "GUEST", "status": "active", "interfaces": []},
    {"vlan_id": 999, "name": "NATIVE", "status": "active", "interfaces": []},
]

MOCK_VLAN_DETAIL = {
    "vlan_id": 10,
    "name": "MGMT",
    "status": "active",
    "interfaces": ["Management1"],
    "type": "static",
}


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with VLAN getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_vlans.return_value = MOCK_VLANS
    driver.get_vlan_detail.return_value = MOCK_VLAN_DETAIL
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# --- net_get_vlans ---


class TestNetGetVlans:
    """Test the net_get_vlans common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 10

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data", "pagination"}

    def test_calls_get_vlans(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlans

        net_get_vlans("spine-01")
        mock_driver.get_vlans.assert_called_once()

    def test_default_pagination(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01")
        assert result["pagination"]["total"] == 10
        assert result["pagination"]["returned"] == 10
        assert result["pagination"]["has_more"] is False

    def test_pagination_page_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=1, page_size=3)
        assert result["status"] == "success"
        assert len(result["data"]) == 3
        assert result["data"][0]["vlan_id"] == 1
        assert result["data"][2]["vlan_id"] == 20
        assert result["pagination"]["total"] == 10
        assert result["pagination"]["returned"] == 3
        assert result["pagination"]["has_more"] is True

    def test_pagination_page_2(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=2, page_size=3)
        assert result["status"] == "success"
        assert len(result["data"]) == 3
        assert result["data"][0]["vlan_id"] == 30
        assert result["data"][2]["vlan_id"] == 200

    def test_pagination_last_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=4, page_size=3)
        assert result["status"] == "success"
        assert len(result["data"]) == 1
        assert result["data"][0]["vlan_id"] == 999
        assert result["pagination"]["has_more"] is False

    def test_pagination_empty_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=100, page_size=3)
        assert result["status"] == "success"
        assert len(result["data"]) == 0
        assert result["pagination"]["returned"] == 0
        assert result["pagination"]["has_more"] is False

    def test_pagination_exact_fit(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=1, page_size=10)
        assert result["status"] == "success"
        assert len(result["data"]) == 10
        assert result["pagination"]["has_more"] is False

    def test_page_less_than_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=0)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_negative(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=-1)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_size_zero(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page_size=0)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_page_size_too_large(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page_size=501)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_empty_vlan_list(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlans

        mock_driver.get_vlans.return_value = []
        result = net_get_vlans("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []
        assert result["pagination"]["total"] == 0
        assert result["pagination"]["returned"] == 0
        assert result["pagination"]["has_more"] is False

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlans

        mock_driver.get_vlans.side_effect = NotSupportedError("not available")
        result = net_get_vlans("spine-01")
        assert result["status"] == "not_supported"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.vlans import net_get_vlans

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_vlans("bad-host")
        assert result["status"] == "error"
        assert result["device"] == "bad-host"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.vlans import net_get_vlans

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_vlans("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.vlans import net_get_vlans

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = net_get_vlans("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_pagination_page_size_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=1, page_size=1)
        assert len(result["data"]) == 1
        assert result["data"][0]["vlan_id"] == 1
        assert result["pagination"]["has_more"] is True

    def test_large_page_size(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlans

        result = net_get_vlans("spine-01", page=1, page_size=500)
        assert len(result["data"]) == 10
        assert result["pagination"]["has_more"] is False


# --- net_get_vlan_detail ---


class TestNetGetVlanDetail:
    """Test the net_get_vlan_detail common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        result = net_get_vlan_detail("spine-01", 10)
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert result["data"]["vlan_id"] == 10
        assert result["data"]["name"] == "MGMT"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        result = net_get_vlan_detail("spine-01", 10)
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_vlan_detail(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        net_get_vlan_detail("spine-01", 10)
        mock_driver.get_vlan_detail.assert_called_once_with(10)

    def test_vlan_not_found(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        mock_driver.get_vlan_detail.return_value = {}
        result = net_get_vlan_detail("spine-01", 999)
        assert result["status"] == "error"
        assert "VLAN 999 not found" in result["error"]

    def test_vlan_not_found_none(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        mock_driver.get_vlan_detail.return_value = None
        result = net_get_vlan_detail("spine-01", 42)
        assert result["status"] == "error"
        assert "VLAN 42 not found" in result["error"]

    def test_invalid_vlan_id_zero(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        result = net_get_vlan_detail("spine-01", 0)
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_invalid_vlan_id_negative(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        result = net_get_vlan_detail("spine-01", -1)
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_invalid_vlan_id_too_high(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        result = net_get_vlan_detail("spine-01", 4095)
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_valid_vlan_id_1(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        mock_driver.get_vlan_detail.return_value = {
            "vlan_id": 1,
            "name": "default",
            "status": "active",
            "interfaces": [],
            "type": "static",
        }
        result = net_get_vlan_detail("spine-01", 1)
        assert result["status"] == "success"
        assert result["data"]["vlan_id"] == 1

    def test_valid_vlan_id_4094(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        mock_driver.get_vlan_detail.return_value = {
            "vlan_id": 4094,
            "name": "LAST",
            "status": "active",
            "interfaces": [],
            "type": "static",
        }
        result = net_get_vlan_detail("spine-01", 4094)
        assert result["status"] == "success"
        assert result["data"]["vlan_id"] == 4094

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        mock_driver.get_vlan_detail.side_effect = NotSupportedError("not available")
        result = net_get_vlan_detail("spine-01", 10)
        assert result["status"] == "not_supported"
        assert result["device"] == "spine-01"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_vlan_detail("bad-host", 10)
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_vlan_detail("slow-host", 10)
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("driver crash")):
            result = net_get_vlan_detail("crash-host", 10)
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_vlan_detail_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        result = net_get_vlan_detail("spine-01", 10)
        data = result["data"]
        assert "vlan_id" in data
        assert "name" in data
        assert "status" in data
        assert "interfaces" in data
        assert "type" in data

    def test_vlan_validation_skips_driver_call(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vlans import net_get_vlan_detail

        net_get_vlan_detail("spine-01", 0)
        mock_driver.get_vlan_detail.assert_not_called()


# --- Write Tool Fixtures ---


@pytest.fixture
def mock_write_driver():
    """Create a mock NetworkDriver for write operations."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.run_config.return_value = []
    return driver


@pytest.fixture
def mock_write_conn_mgr(mock_write_driver):
    """Patch conn_mgr.get_driver for write tests."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver) as patched:
        yield patched


@pytest.fixture(autouse=True)
def _allow_writes():
    """Ensure write operations are allowed for write tests."""
    with patch.object(settings, "net_read_only", False):
        yield


# --- net_create_vlan ---


class TestNetCreateVlan:
    """Test the net_create_vlan common write tool."""

    def test_success_eos(self, mock_write_conn_mgr, mock_write_driver):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 100, "SERVERS")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["action"] == "create_vlan"
        assert result["data"]["vlan_id"] == 100
        assert result["data"]["name"] == "SERVERS"
        mock_write_driver.run_config.assert_called_once_with(["vlan 100", "name SERVERS"])

    def test_success_iosxe(self, mock_write_driver):
        from network_mcp.tools.common.vlans import net_create_vlan

        mock_write_driver.platform = "iosxe"
        mock_write_driver.vendor = "cisco"
        with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver):
            result = net_create_vlan("iosxe-rtr", 200, "MGMT")
        assert result["status"] == "success"
        assert result["vendor"] == "cisco"
        mock_write_driver.run_config.assert_called_once_with(["vlan 200", "name MGMT"])

    def test_success_nxos(self, mock_write_driver):
        from network_mcp.tools.common.vlans import net_create_vlan

        mock_write_driver.platform = "nxos"
        mock_write_driver.vendor = "cisco"
        with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver):
            result = net_create_vlan("nxos-sw", 300, "USERS")
        assert result["status"] == "success"
        mock_write_driver.run_config.assert_called_once_with(["vlan 300", "name USERS"])

    def test_success_junos(self, mock_write_driver):
        from network_mcp.tools.common.vlans import net_create_vlan

        mock_write_driver.platform = "junos"
        mock_write_driver.vendor = "juniper"
        with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver):
            result = net_create_vlan("junos-sw", 400, "DMZ")
        assert result["status"] == "success"
        assert result["vendor"] == "juniper"
        mock_write_driver.run_config.assert_called_once_with(["set vlans DMZ vlan-id 400"])

    def test_read_only_blocked(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_create_vlan

        with patch.object(settings, "net_read_only", True):
            result = net_create_vlan("spine-01", 100, "TEST")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    def test_invalid_vlan_id_zero(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 0, "TEST")
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_invalid_vlan_id_too_high(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 4095, "TEST")
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_invalid_name_special_chars(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 100, "bad name!")
        assert result["status"] == "error"
        assert "VLAN name" in result["error"]

    def test_invalid_name_semicolon(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 100, "test;inject")
        assert result["status"] == "error"
        assert "VLAN name" in result["error"]

    def test_invalid_name_too_long(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 100, "a" * 33)
        assert result["status"] == "error"
        assert "VLAN name" in result["error"]

    def test_name_max_length(self, mock_write_conn_mgr, mock_write_driver):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 100, "a" * 32)
        assert result["status"] == "success"

    def test_name_with_hyphens_underscores(self, mock_write_conn_mgr, mock_write_driver):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 100, "my_vlan-name")
        assert result["status"] == "success"

    def test_vlan_id_boundary_1(self, mock_write_conn_mgr, mock_write_driver):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 1, "DEFAULT")
        assert result["status"] == "success"
        assert result["data"]["vlan_id"] == 1

    def test_vlan_id_boundary_4094(self, mock_write_conn_mgr, mock_write_driver):
        from network_mcp.tools.common.vlans import net_create_vlan

        result = net_create_vlan("spine-01", 4094, "LAST")
        assert result["status"] == "success"
        assert result["data"]["vlan_id"] == 4094

    def test_connection_error(self):
        from network_mcp.tools.common.vlans import net_create_vlan

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_create_vlan("bad-host", 100, "TEST")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]


# --- net_delete_vlan ---


class TestNetDeleteVlan:
    """Test the net_delete_vlan common write tool."""

    def test_success_eos(self, mock_write_conn_mgr, mock_write_driver):
        from network_mcp.tools.common.vlans import net_delete_vlan

        result = net_delete_vlan("spine-01", 100)
        assert result["status"] == "success"
        assert result["action"] == "delete_vlan"
        assert result["data"]["vlan_id"] == 100
        mock_write_driver.run_config.assert_called_once_with(["no vlan 100"])

    def test_success_iosxe(self, mock_write_driver):
        from network_mcp.tools.common.vlans import net_delete_vlan

        mock_write_driver.platform = "iosxe"
        mock_write_driver.vendor = "cisco"
        with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver):
            result = net_delete_vlan("iosxe-rtr", 200)
        assert result["status"] == "success"
        mock_write_driver.run_config.assert_called_once_with(["no vlan 200"])

    def test_success_junos(self, mock_write_driver):
        from network_mcp.tools.common.vlans import net_delete_vlan

        mock_write_driver.platform = "junos"
        mock_write_driver.vendor = "juniper"
        with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver):
            result = net_delete_vlan("junos-sw", 300)
        assert result["status"] == "success"
        mock_write_driver.run_config.assert_called_once_with(["delete vlans vlan-id 300"])

    def test_read_only_blocked(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_delete_vlan

        with patch.object(settings, "net_read_only", True):
            result = net_delete_vlan("spine-01", 100)
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    def test_invalid_vlan_id(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_delete_vlan

        result = net_delete_vlan("spine-01", 0)
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_invalid_vlan_id_negative(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_delete_vlan

        result = net_delete_vlan("spine-01", -1)
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.vlans import net_delete_vlan

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_delete_vlan("bad-host", 100)
        assert result["status"] == "error"
        assert "unreachable" in result["error"]


# --- net_rename_vlan ---


class TestNetRenameVlan:
    """Test the net_rename_vlan common write tool."""

    def test_success_eos(self, mock_write_conn_mgr, mock_write_driver):
        from network_mcp.tools.common.vlans import net_rename_vlan

        result = net_rename_vlan("spine-01", 100, "NEW-NAME")
        assert result["status"] == "success"
        assert result["action"] == "rename_vlan"
        assert result["data"]["vlan_id"] == 100
        assert result["data"]["name"] == "NEW-NAME"
        mock_write_driver.run_config.assert_called_once_with(["vlan 100", "name NEW-NAME"])

    def test_success_junos(self, mock_write_driver):
        from network_mcp.tools.common.vlans import net_rename_vlan

        mock_write_driver.platform = "junos"
        mock_write_driver.vendor = "juniper"
        with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver):
            result = net_rename_vlan("junos-sw", 100, "NEW-NAME")
        assert result["status"] == "success"
        mock_write_driver.run_config.assert_called_once_with(["set vlans NEW-NAME vlan-id 100"])

    def test_read_only_blocked(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_rename_vlan

        with patch.object(settings, "net_read_only", True):
            result = net_rename_vlan("spine-01", 100, "TEST")
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    def test_invalid_vlan_id(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_rename_vlan

        result = net_rename_vlan("spine-01", 4095, "TEST")
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_invalid_name_special_chars(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_rename_vlan

        result = net_rename_vlan("spine-01", 100, "bad@name!")
        assert result["status"] == "error"
        assert "VLAN name" in result["error"]

    def test_invalid_name_pipe(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_rename_vlan

        result = net_rename_vlan("spine-01", 100, "test|cmd")
        assert result["status"] == "error"
        assert "VLAN name" in result["error"]

    def test_invalid_name_empty(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_rename_vlan

        result = net_rename_vlan("spine-01", 100, "")
        assert result["status"] == "error"
        assert "VLAN name" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.vlans import net_rename_vlan

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_rename_vlan("bad-host", 100, "TEST")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]


# --- net_assign_interface_vlan ---


class TestNetAssignInterfaceVlan:
    """Test the net_assign_interface_vlan common write tool."""

    def test_success_eos(self, mock_write_conn_mgr, mock_write_driver):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        result = net_assign_interface_vlan("spine-01", "Ethernet1", 100)
        assert result["status"] == "success"
        assert result["action"] == "assign_interface_vlan"
        assert result["data"]["interface"] == "Ethernet1"
        assert result["data"]["vlan_id"] == 100
        mock_write_driver.run_config.assert_called_once_with(
            [
                "interface Ethernet1",
                "switchport mode access",
                "switchport access vlan 100",
            ]
        )

    def test_success_iosxe(self, mock_write_driver):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        mock_write_driver.platform = "iosxe"
        mock_write_driver.vendor = "cisco"
        with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver):
            result = net_assign_interface_vlan("iosxe-rtr", "Ethernet1", 200)
        assert result["status"] == "success"
        mock_write_driver.run_config.assert_called_once_with(
            [
                "interface Ethernet1",
                "switchport mode access",
                "switchport access vlan 200",
            ]
        )

    def test_success_junos(self, mock_write_driver):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        mock_write_driver.platform = "junos"
        mock_write_driver.vendor = "juniper"
        with patch.object(conn_mgr, "get_driver", return_value=mock_write_driver):
            result = net_assign_interface_vlan("junos-sw", "Ethernet1", 300)
        assert result["status"] == "success"
        mock_write_driver.run_config.assert_called_once_with(
            [
                "set interfaces Ethernet1 unit 0 family ethernet-switching vlan members vlan300",
            ]
        )

    def test_read_only_blocked(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        with patch.object(settings, "net_read_only", True):
            result = net_assign_interface_vlan("spine-01", "Ethernet1", 100)
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]

    def test_invalid_vlan_id(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        result = net_assign_interface_vlan("spine-01", "Ethernet1", 4095)
        assert result["status"] == "error"
        assert "VLAN ID must be between 1 and 4094" in result["error"]

    def test_cli_injection_interface(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        result = net_assign_interface_vlan("spine-01", "Ethernet1;shutdown", 100)
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]

    def test_cli_injection_pipe(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        result = net_assign_interface_vlan("spine-01", "Ethernet1|more", 100)
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]

    def test_cli_injection_backtick(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        result = net_assign_interface_vlan("spine-01", "Ethernet1`cmd`", 100)
        assert result["status"] == "error"
        assert "invalid characters" in result["error"]

    def test_empty_interface(self, mock_write_conn_mgr):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        result = net_assign_interface_vlan("spine-01", "", 100)
        assert result["status"] == "error"
        assert "cannot be empty" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_assign_interface_vlan("bad-host", "Ethernet1", 100)
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_validation_order_read_only_first(self, mock_write_conn_mgr, mock_write_driver):
        """Read-only check should happen before VLAN or interface validation."""
        from network_mcp.tools.common.vlans import net_assign_interface_vlan

        with patch.object(settings, "net_read_only", True):
            result = net_assign_interface_vlan("spine-01", "Ethernet1", 100)
        assert result["status"] == "error"
        assert "Write operations are disabled" in result["error"]
        mock_write_driver.run_config.assert_not_called()
