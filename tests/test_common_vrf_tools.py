"""Tests for vendor-agnostic VRF tools.

Tests net_get_vrf_list and net_get_vrf_detail. Validates response structure,
empty results, NotSupportedError handling, parameter validation, and
multi-vendor support.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr

# --- Mock data ---

MOCK_VRF_LIST = [
    {"name": "default", "rd": "", "interfaces": ["Ethernet1", "Ethernet2", "Loopback0"], "route_count": 42},
    {"name": "MGMT", "rd": "65001:100", "interfaces": ["Management1"], "route_count": 3},
    {"name": "PROD", "rd": "65001:200", "interfaces": ["Vlan100", "Vlan200"], "route_count": 150},
]

MOCK_VRF_LIST_SINGLE = [
    {"name": "default", "rd": "", "interfaces": [], "route_count": 0},
]

MOCK_VRF_DETAIL = {
    "name": "PROD",
    "rd": "65001:200",
    "interfaces": ["Vlan100", "Vlan200", "Loopback1"],
    "route_count": 150,
    "import_targets": ["65001:200"],
    "export_targets": ["65001:200"],
}

MOCK_VRF_DETAIL_NO_TARGETS = {
    "name": "MGMT",
    "rd": "65001:100",
    "interfaces": ["Management1"],
    "route_count": 3,
    "import_targets": [],
    "export_targets": [],
}

# JunOS routing instances
MOCK_VRF_LIST_JUNOS = [
    {"name": "master", "rd": "", "interfaces": ["ge-0/0/0", "ge-0/0/1", "lo0"], "route_count": 100},
    {"name": "__juniper_private1__", "rd": "", "interfaces": [], "route_count": 0},
    {"name": "VRF-A", "rd": "65001:1", "interfaces": ["ge-0/0/2.100"], "route_count": 25},
]


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with VRF getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_vrfs.return_value = MOCK_VRF_LIST
    driver.get_vrf_detail.return_value = MOCK_VRF_DETAIL
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# ===========================================================================
# net_get_vrf_list
# ===========================================================================


class TestNetGetVrfList:
    """Test the net_get_vrf_list common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        result = net_get_vrf_list("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        result = net_get_vrf_list("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_vrfs(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        net_get_vrf_list("spine-01")
        mock_driver.get_vrfs.assert_called_once()

    def test_vrf_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        result = net_get_vrf_list("spine-01")
        data = result["data"]
        assert isinstance(data, list)
        assert len(data) == 3

    def test_vrf_entry_fields(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        result = net_get_vrf_list("spine-01")
        vrf = result["data"][1]
        assert vrf["name"] == "MGMT"
        assert vrf["rd"] == "65001:100"
        assert vrf["interfaces"] == ["Management1"]
        assert vrf["route_count"] == 3

    def test_default_vrf(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        result = net_get_vrf_list("spine-01")
        default = result["data"][0]
        assert default["name"] == "default"
        assert default["rd"] == ""
        assert default["route_count"] == 42

    def test_empty_vrf_list(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        mock_driver.get_vrfs.return_value = []
        result = net_get_vrf_list("spine-01")
        assert result["status"] == "success"
        assert result["data"] == []

    def test_single_vrf(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        mock_driver.get_vrfs.return_value = MOCK_VRF_LIST_SINGLE
        result = net_get_vrf_list("spine-01")
        assert len(result["data"]) == 1
        assert result["data"][0]["name"] == "default"

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        mock_driver.get_vrfs.side_effect = NotSupportedError("not available")
        result = net_get_vrf_list("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_vrf_list("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_vrf_list("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_vrf_list("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_vrf_list("iosxe-router")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_vrf_list("nxos-switch")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        mock_driver.get_vrfs.return_value = MOCK_VRF_LIST_JUNOS
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_vrf_list("junos-router")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"
        assert len(result["data"]) == 3

    def test_junos_routing_instances(self, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        mock_driver.get_vrfs.return_value = MOCK_VRF_LIST_JUNOS
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_vrf_list("junos-router")
        names = [v["name"] for v in result["data"]]
        assert "master" in names
        assert "VRF-A" in names

    def test_vrf_with_many_interfaces(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_list

        mock_driver.get_vrfs.return_value = [
            {"name": "PROD", "rd": "65001:200", "interfaces": [f"Vlan{i}" for i in range(100, 200)], "route_count": 500}
        ]
        result = net_get_vrf_list("spine-01")
        assert len(result["data"][0]["interfaces"]) == 100


# ===========================================================================
# net_get_vrf_detail
# ===========================================================================


class TestNetGetVrfDetail:
    """Test the net_get_vrf_detail common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        result = net_get_vrf_detail("spine-01", "PROD")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        result = net_get_vrf_detail("spine-01", "PROD")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_calls_get_vrf_detail(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        net_get_vrf_detail("spine-01", "PROD")
        mock_driver.get_vrf_detail.assert_called_once_with(vrf="PROD")

    def test_detail_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        result = net_get_vrf_detail("spine-01", "PROD")
        data = result["data"]
        assert data["name"] == "PROD"
        assert data["rd"] == "65001:200"
        assert "Vlan100" in data["interfaces"]
        assert "Vlan200" in data["interfaces"]
        assert data["route_count"] == 150
        assert data["import_targets"] == ["65001:200"]
        assert data["export_targets"] == ["65001:200"]

    def test_vrf_not_found(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        mock_driver.get_vrf_detail.return_value = {}
        result = net_get_vrf_detail("spine-01", "NONEXISTENT")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_vrf_with_no_route_targets(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        mock_driver.get_vrf_detail.return_value = MOCK_VRF_DETAIL_NO_TARGETS
        result = net_get_vrf_detail("spine-01", "MGMT")
        data = result["data"]
        assert data["import_targets"] == []
        assert data["export_targets"] == []

    def test_invalid_vrf_name_empty(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        result = net_get_vrf_detail("spine-01", "")
        assert result["status"] == "error"
        assert "cannot be empty" in result["error"]
        mock_driver.get_vrf_detail.assert_not_called()

    def test_invalid_vrf_name_injection(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        result = net_get_vrf_detail("spine-01", "PROD; reload")
        assert result["status"] == "error"
        assert "VRF name" in result["error"]
        mock_driver.get_vrf_detail.assert_not_called()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        mock_driver.get_vrf_detail.side_effect = NotSupportedError("not available")
        result = net_get_vrf_detail("spine-01", "PROD")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_vrf_detail("bad-host", "PROD")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_vrf_detail("slow-host", "PROD")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_vrf_detail("crash-host", "PROD")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_cisco_iosxe_vendor(self, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        mock_driver.vendor = "cisco"
        mock_driver.platform = "iosxe"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_vrf_detail("iosxe-router", "PROD")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "iosxe"

    def test_cisco_nxos_vendor(self, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        mock_driver.vendor = "cisco"
        mock_driver.platform = "nxos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_vrf_detail("nxos-switch", "PROD")
        assert result["vendor"] == "cisco"
        assert result["platform"] == "nxos"

    def test_juniper_vendor(self, mock_driver):
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_vrf_detail("junos-router", "VRF-A")
        assert result["vendor"] == "juniper"
        assert result["platform"] == "junos"

    def test_junos_routing_instance(self, mock_driver):
        """JunOS routing instances should work via the same tool."""
        from network_mcp.tools.common.vrf import net_get_vrf_detail

        mock_driver.vendor = "juniper"
        mock_driver.platform = "junos"
        mock_driver.get_vrf_detail.return_value = {
            "name": "master",
            "rd": "",
            "interfaces": ["ge-0/0/0", "lo0"],
            "route_count": 100,
            "import_targets": [],
            "export_targets": [],
        }
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            result = net_get_vrf_detail("junos-router", "master")
        assert result["status"] == "success"
        assert result["data"]["name"] == "master"
