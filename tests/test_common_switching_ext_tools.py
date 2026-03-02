"""Tests for vendor-agnostic switching tools (extended).

Tests net_get_mac_table, net_get_stp_status, net_get_port_channels, and
net_get_lldp_neighbor_detail. Validates pagination, filtering, NotSupportedError
handling, empty results, vendor terminology normalization, and standard
response structure.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr

# --- Mock data ---

MOCK_MAC_TABLE = [
    {"mac_address": "00:1a:2b:3c:4d:5e", "vlan": 10, "interface": "Ethernet1", "type": "dynamic"},
    {"mac_address": "00:1a:2b:3c:4d:5f", "vlan": 10, "interface": "Ethernet2", "type": "dynamic"},
    {"mac_address": "00:1a:2b:3c:4d:60", "vlan": 20, "interface": "Ethernet1", "type": "static"},
    {"mac_address": "aa:bb:cc:dd:ee:ff", "vlan": 20, "interface": "Ethernet3", "type": "dynamic"},
    {"mac_address": "aa:bb:cc:dd:ee:01", "vlan": 30, "interface": "Port-Channel1", "type": "dynamic"},
]

MOCK_STP_STATUS_RSTP = {
    "mode": "rstp",
    "root_bridge": "00:1a:2b:3c:4d:00",
    "instances": [
        {
            "id": "0",
            "root_bridge": "00:1a:2b:3c:4d:00",
            "root_port": "Ethernet1",
            "priority": 32768,
        },
    ],
}

MOCK_STP_STATUS_MSTP = {
    "mode": "mstp",
    "root_bridge": "aa:bb:cc:dd:ee:00",
    "instances": [
        {
            "id": "0",
            "root_bridge": "aa:bb:cc:dd:ee:00",
            "root_port": "Ethernet1",
            "priority": 4096,
        },
        {
            "id": "1",
            "root_bridge": "aa:bb:cc:dd:ee:01",
            "root_port": "Ethernet2",
            "priority": 32768,
        },
    ],
}

MOCK_STP_STATUS_PVST = {
    "mode": "rapid-pvst",
    "root_bridge": "00:aa:bb:cc:dd:00",
    "instances": [
        {
            "id": "10",
            "root_bridge": "00:aa:bb:cc:dd:00",
            "root_port": "Ethernet1",
            "priority": 24576,
        },
        {
            "id": "20",
            "root_bridge": "00:aa:bb:cc:dd:01",
            "root_port": "",
            "priority": 24576,
        },
    ],
}

MOCK_PORT_CHANNELS_LACP = [
    {
        "name": "Port-Channel1",
        "status": "up",
        "protocol": "lacp",
        "members": [
            {"interface": "Ethernet1", "status": "bundled"},
            {"interface": "Ethernet2", "status": "bundled"},
        ],
    },
    {
        "name": "Port-Channel2",
        "status": "down",
        "protocol": "lacp",
        "members": [
            {"interface": "Ethernet3", "status": "down"},
        ],
    },
]

MOCK_PORT_CHANNELS_STATIC = [
    {
        "name": "Port-Channel10",
        "status": "up",
        "protocol": "static",
        "members": [
            {"interface": "Ethernet5", "status": "bundled"},
            {"interface": "Ethernet6", "status": "bundled"},
        ],
    },
]

MOCK_LLDP_DETAIL = {
    "Ethernet1": [
        {
            "hostname": "spine-01",
            "port": "Ethernet49",
            "system_description": "Arista EOS 4.28.0F",
            "chassis_id": "00:1a:2b:3c:4d:5e",
            "management_address": "10.0.0.1",
            "capabilities": ["bridge", "router"],
        },
    ],
    "Ethernet2": [
        {
            "hostname": "spine-02",
            "port": "Ethernet49",
            "system_description": "Arista EOS 4.28.0F",
            "chassis_id": "00:1a:2b:3c:4d:5f",
            "management_address": "10.0.0.2",
            "capabilities": ["bridge", "router"],
        },
    ],
    "Ethernet3": [
        {
            "hostname": "server-01",
            "port": "eth0",
            "system_description": "Linux 5.15",
            "chassis_id": "aa:bb:cc:dd:ee:ff",
            "management_address": "192.168.1.10",
            "capabilities": ["station"],
        },
    ],
}


def _generate_large_mac_table(count: int) -> list[dict]:
    """Generate a large MAC table for pagination tests."""
    return [
        {
            "mac_address": f"00:11:22:33:{i // 256:02x}:{i % 256:02x}",
            "vlan": (i % 10) + 10,
            "interface": f"Ethernet{(i % 48) + 1}",
            "type": "dynamic" if i % 3 != 0 else "static",
        }
        for i in range(count)
    ]


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with switching getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_mac_table.return_value = MOCK_MAC_TABLE
    driver.get_stp_status.return_value = MOCK_STP_STATUS_RSTP
    driver.get_port_channels.return_value = MOCK_PORT_CHANNELS_LACP
    driver.get_lldp_neighbor_detail.return_value = MOCK_LLDP_DETAIL
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# ===========================================================================
# net_get_mac_table
# ===========================================================================


class TestNetGetMacTable:
    """Test the net_get_mac_table common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 5

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data", "pagination"}

    def test_calls_get_mac_table(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        net_get_mac_table("spine-01")
        mock_driver.get_mac_table.assert_called_once_with(vlan=None, limit=10000)

    def test_vlan_filter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        net_get_mac_table("spine-01", vlan=10)
        mock_driver.get_mac_table.assert_called_once_with(vlan=10, limit=10000)

    def test_vlan_zero_means_all(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        net_get_mac_table("spine-01", vlan=0)
        mock_driver.get_mac_table.assert_called_once_with(vlan=None, limit=10000)

    def test_interface_filter(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", interface="Ethernet1")
        assert result["status"] == "success"
        assert len(result["data"]) == 2
        for entry in result["data"]:
            assert entry["interface"] == "Ethernet1"

    def test_interface_filter_no_match(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", interface="Ethernet99")
        assert result["status"] == "success"
        assert len(result["data"]) == 0

    def test_mac_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01")
        entry = result["data"][0]
        assert entry["mac_address"] == "00:1a:2b:3c:4d:5e"
        assert entry["vlan"] == 10
        assert entry["interface"] == "Ethernet1"
        assert entry["type"] == "dynamic"

    def test_static_mac_entry(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01")
        static_entry = result["data"][2]
        assert static_entry["type"] == "static"

    def test_default_pagination(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01")
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["returned"] == 5
        assert result["pagination"]["has_more"] is False

    def test_pagination_page_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", page=1, page_size=2)
        assert len(result["data"]) == 2
        assert result["data"][0]["mac_address"] == "00:1a:2b:3c:4d:5e"
        assert result["data"][1]["mac_address"] == "00:1a:2b:3c:4d:5f"
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["has_more"] is True

    def test_pagination_page_2(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", page=2, page_size=2)
        assert len(result["data"]) == 2
        assert result["data"][0]["mac_address"] == "00:1a:2b:3c:4d:60"
        assert result["data"][1]["mac_address"] == "aa:bb:cc:dd:ee:ff"

    def test_pagination_last_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", page=3, page_size=2)
        assert len(result["data"]) == 1
        assert result["data"][0]["mac_address"] == "aa:bb:cc:dd:ee:01"
        assert result["pagination"]["has_more"] is False

    def test_pagination_empty_page(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", page=100, page_size=2)
        assert len(result["data"]) == 0
        assert result["pagination"]["returned"] == 0
        assert result["pagination"]["has_more"] is False

    def test_page_less_than_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", page=0)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_negative(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", page=-1)
        assert result["status"] == "error"
        assert "page must be >= 1" in result["error"]

    def test_page_size_zero(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", page_size=0)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_page_size_too_large(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", page_size=501)
        assert result["status"] == "error"
        assert "page_size must be between 1 and 500" in result["error"]

    def test_large_table_pagination(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        large_macs = _generate_large_mac_table(1200)
        mock_driver.get_mac_table.return_value = large_macs
        result = net_get_mac_table("spine-01", page=1, page_size=100)
        assert len(result["data"]) == 100
        assert result["pagination"]["total"] == 1200
        assert result["pagination"]["has_more"] is True

    def test_large_table_last_page(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        large_macs = _generate_large_mac_table(1200)
        mock_driver.get_mac_table.return_value = large_macs
        result = net_get_mac_table("spine-01", page=12, page_size=100)
        assert len(result["data"]) == 100
        assert result["pagination"]["has_more"] is False

    def test_empty_mac_table(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        mock_driver.get_mac_table.return_value = []
        result = net_get_mac_table("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []
        assert result["pagination"]["total"] == 0

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        mock_driver.get_mac_table.side_effect = NotSupportedError("not available")
        result = net_get_mac_table("spine-01")
        assert result["status"] == "not_supported"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.switching import net_get_mac_table

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_mac_table("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.switching import net_get_mac_table

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_mac_table("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.switching import net_get_mac_table

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_mac_table("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_page_validation_skips_driver_call(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        net_get_mac_table("spine-01", page=0)
        mock_driver.get_mac_table.assert_not_called()

    def test_page_size_validation_skips_driver_call(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        net_get_mac_table("spine-01", page_size=501)
        mock_driver.get_mac_table.assert_not_called()

    def test_combined_vlan_and_interface_filter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_mac_table

        net_get_mac_table("spine-01", vlan=10, interface="Ethernet1")
        mock_driver.get_mac_table.assert_called_once_with(vlan=10, limit=10000)

    def test_port_channel_interface(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_mac_table

        result = net_get_mac_table("spine-01", interface="Port-Channel1")
        assert len(result["data"]) == 1
        assert result["data"][0]["mac_address"] == "aa:bb:cc:dd:ee:01"


# ===========================================================================
# net_get_stp_status
# ===========================================================================


class TestNetGetStpStatus:
    """Test the net_get_stp_status common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_stp_status

        result = net_get_stp_status("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_stp_status

        result = net_get_stp_status("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_stp_status(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_stp_status

        net_get_stp_status("spine-01")
        mock_driver.get_stp_status.assert_called_once()

    def test_rstp_mode(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_stp_status

        result = net_get_stp_status("spine-01")
        data = result["data"]
        assert data["mode"] == "rstp"
        assert data["root_bridge"] == "00:1a:2b:3c:4d:00"
        assert len(data["instances"]) == 1
        assert data["instances"][0]["priority"] == 32768

    def test_mstp_mode(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_stp_status

        mock_driver.get_stp_status.return_value = MOCK_STP_STATUS_MSTP
        result = net_get_stp_status("spine-01")
        data = result["data"]
        assert data["mode"] == "mstp"
        assert len(data["instances"]) == 2
        assert data["instances"][0]["priority"] == 4096
        assert data["instances"][1]["id"] == "1"

    def test_pvst_mode(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_stp_status

        mock_driver.get_stp_status.return_value = MOCK_STP_STATUS_PVST
        result = net_get_stp_status("spine-01")
        data = result["data"]
        assert data["mode"] == "rapid-pvst"
        assert len(data["instances"]) == 2
        assert data["instances"][0]["id"] == "10"
        assert data["instances"][1]["id"] == "20"

    def test_instance_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_stp_status

        result = net_get_stp_status("spine-01")
        inst = result["data"]["instances"][0]
        assert inst["id"] == "0"
        assert inst["root_bridge"] == "00:1a:2b:3c:4d:00"
        assert inst["root_port"] == "Ethernet1"
        assert inst["priority"] == 32768

    def test_empty_instances(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_stp_status

        mock_driver.get_stp_status.return_value = {"mode": "rstp", "root_bridge": "", "instances": []}
        result = net_get_stp_status("spine-01")
        assert result["status"] == "success"
        assert result["data"]["instances"] == []

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_stp_status

        mock_driver.get_stp_status.side_effect = NotSupportedError("not available")
        result = net_get_stp_status("spine-01")
        assert result["status"] == "not_supported"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.switching import net_get_stp_status

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_stp_status("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.switching import net_get_stp_status

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_stp_status("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.switching import net_get_stp_status

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_stp_status("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.switching import net_get_stp_status

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_stp_status("iosxe-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.switching import net_get_stp_status

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_stp_status("junos-switch")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"


# ===========================================================================
# net_get_port_channels
# ===========================================================================


class TestNetGetPortChannels:
    """Test the net_get_port_channels common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_port_channels

        result = net_get_port_channels("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 2

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_port_channels

        result = net_get_port_channels("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_port_channels(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_port_channels

        net_get_port_channels("spine-01")
        mock_driver.get_port_channels.assert_called_once()

    def test_lacp_port_channel(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_port_channels

        result = net_get_port_channels("spine-01")
        pc1 = result["data"][0]
        assert pc1["name"] == "Port-Channel1"
        assert pc1["status"] == "up"
        assert pc1["protocol"] == "lacp"
        assert len(pc1["members"]) == 2
        assert pc1["members"][0]["interface"] == "Ethernet1"
        assert pc1["members"][0]["status"] == "bundled"

    def test_down_port_channel(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_port_channels

        result = net_get_port_channels("spine-01")
        pc2 = result["data"][1]
        assert pc2["name"] == "Port-Channel2"
        assert pc2["status"] == "down"
        assert len(pc2["members"]) == 1
        assert pc2["members"][0]["status"] == "down"

    def test_static_port_channel(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_port_channels

        mock_driver.get_port_channels.return_value = MOCK_PORT_CHANNELS_STATIC
        result = net_get_port_channels("spine-01")
        pc = result["data"][0]
        assert pc["protocol"] == "static"
        assert pc["name"] == "Port-Channel10"

    def test_empty_port_channels(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_port_channels

        mock_driver.get_port_channels.return_value = []
        result = net_get_port_channels("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []

    def test_member_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_port_channels

        result = net_get_port_channels("spine-01")
        member = result["data"][0]["members"][1]
        assert member["interface"] == "Ethernet2"
        assert member["status"] == "bundled"

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_port_channels

        mock_driver.get_port_channels.side_effect = NotSupportedError("not available")
        result = net_get_port_channels("spine-01")
        assert result["status"] == "not_supported"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.switching import net_get_port_channels

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_port_channels("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.switching import net_get_port_channels

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_port_channels("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.switching import net_get_port_channels

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_port_channels("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.switching import net_get_port_channels

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_port_channels("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_ae_normalization(self, mock_driver):
        """Verify JunOS ae interfaces are returned through the same data structure."""
        from network_mcp.tools.common.switching import net_get_port_channels

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        mock_driver.get_port_channels.return_value = [
            {
                "name": "ae0",
                "status": "up",
                "protocol": "lacp",
                "members": [
                    {"interface": "xe-0/0/0", "status": "bundled"},
                    {"interface": "xe-0/0/1", "status": "bundled"},
                ],
            },
        ]
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_port_channels("junos-switch")
        assert result["vendor"] == "juniper"
        assert result["data"][0]["name"] == "ae0"
        assert result["data"][0]["members"][0]["interface"] == "xe-0/0/0"

    def test_hot_standby_member(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_port_channels

        mock_driver.get_port_channels.return_value = [
            {
                "name": "Port-Channel1",
                "status": "up",
                "protocol": "lacp",
                "members": [
                    {"interface": "Ethernet1", "status": "bundled"},
                    {"interface": "Ethernet2", "status": "hot-standby"},
                ],
            },
        ]
        result = net_get_port_channels("spine-01")
        members = result["data"][0]["members"]
        assert members[1]["status"] == "hot-standby"


# ===========================================================================
# net_get_lldp_neighbor_detail
# ===========================================================================


class TestNetGetLldpNeighborDetail:
    """Test the net_get_lldp_neighbor_detail common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        result = net_get_lldp_neighbor_detail("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"
        assert isinstance(result["data"], dict)
        assert len(result["data"]) == 3

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        result = net_get_lldp_neighbor_detail("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_lldp_neighbor_detail(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        net_get_lldp_neighbor_detail("spine-01")
        mock_driver.get_lldp_neighbor_detail.assert_called_once_with(interface="")

    def test_interface_filter(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        net_get_lldp_neighbor_detail("spine-01", interface="Ethernet1")
        mock_driver.get_lldp_neighbor_detail.assert_called_once_with(interface="Ethernet1")

    def test_detail_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        result = net_get_lldp_neighbor_detail("spine-01")
        eth1 = result["data"]["Ethernet1"][0]
        assert eth1["hostname"] == "spine-01"
        assert eth1["port"] == "Ethernet49"
        assert eth1["system_description"] == "Arista EOS 4.28.0F"
        assert eth1["chassis_id"] == "00:1a:2b:3c:4d:5e"
        assert eth1["management_address"] == "10.0.0.1"
        assert eth1["capabilities"] == ["bridge", "router"]

    def test_server_capabilities(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        result = net_get_lldp_neighbor_detail("spine-01")
        eth3 = result["data"]["Ethernet3"][0]
        assert eth3["hostname"] == "server-01"
        assert eth3["capabilities"] == ["station"]

    def test_multiple_interfaces(self, mock_common_conn_mgr):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        result = net_get_lldp_neighbor_detail("spine-01")
        assert "Ethernet1" in result["data"]
        assert "Ethernet2" in result["data"]
        assert "Ethernet3" in result["data"]

    def test_empty_detail(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        mock_driver.get_lldp_neighbor_detail.return_value = {}
        result = net_get_lldp_neighbor_detail("spine-01")
        assert result["status"] == "success"
        assert result["data"] == {}

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        mock_driver.get_lldp_neighbor_detail.side_effect = NotSupportedError("not available")
        result = net_get_lldp_neighbor_detail("spine-01")
        assert result["status"] == "not_supported"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_lldp_neighbor_detail("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_lldp_neighbor_detail("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_lldp_neighbor_detail("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_lldp_neighbor_detail("iosxe-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_lldp_neighbor_detail("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_lldp_neighbor_detail("junos-switch")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"

    def test_interface_filtered_results(self, mock_common_conn_mgr, mock_driver):
        """When interface is passed, only matching data is returned."""
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        mock_driver.get_lldp_neighbor_detail.return_value = {
            "Ethernet1": MOCK_LLDP_DETAIL["Ethernet1"],
        }
        result = net_get_lldp_neighbor_detail("spine-01", interface="Ethernet1")
        assert len(result["data"]) == 1
        assert "Ethernet1" in result["data"]

    def test_empty_capabilities(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.switching import net_get_lldp_neighbor_detail

        mock_driver.get_lldp_neighbor_detail.return_value = {
            "Ethernet1": [
                {
                    "hostname": "device-01",
                    "port": "eth0",
                    "system_description": "Unknown",
                    "chassis_id": "00:11:22:33:44:55",
                    "management_address": "",
                    "capabilities": [],
                },
            ],
        }
        result = net_get_lldp_neighbor_detail("spine-01")
        entry = result["data"]["Ethernet1"][0]
        assert entry["capabilities"] == []
        assert entry["management_address"] == ""
