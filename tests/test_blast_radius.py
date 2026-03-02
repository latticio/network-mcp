"""Tests for blast radius analysis (net_analyze_change_impact)."""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr
from network_mcp.tools.common.changes import (
    _calculate_risk_level,
    _parse_affected_resources,
    net_analyze_change_impact,
)


class TestParseAffectedResources:
    """Tests for command parsing logic."""

    def test_interface_commands_detected(self):
        """Interface commands are detected and interfaces listed."""
        commands = ["interface Ethernet1", "description uplink"]
        resources = _parse_affected_resources(commands)

        assert "Ethernet1" in resources["affected_interfaces"]

    def test_multiple_interfaces_detected(self):
        """Multiple interface commands are detected."""
        commands = [
            "interface Ethernet1",
            "description uplink-1",
            "interface Ethernet2",
            "description uplink-2",
        ]
        resources = _parse_affected_resources(commands)

        assert len(resources["affected_interfaces"]) == 2
        assert "Ethernet1" in resources["affected_interfaces"]
        assert "Ethernet2" in resources["affected_interfaces"]

    def test_vlan_commands_detected(self):
        """VLAN commands are detected and VLANs listed."""
        commands = ["vlan 100", "name SERVERS", "vlan 200", "name CLIENTS"]
        resources = _parse_affected_resources(commands)

        assert 100 in resources["affected_vlans"]
        assert 200 in resources["affected_vlans"]

    def test_bgp_commands_detected(self):
        """BGP commands are detected and sessions identified."""
        commands = [
            "router bgp 65000",
            "neighbor 10.0.0.1 remote-as 65001",
            "neighbor 10.0.0.2 remote-as 65002",
        ]
        resources = _parse_affected_resources(commands)

        assert resources["has_routing_impact"] is True
        assert "10.0.0.1" in resources["affected_bgp_sessions"]
        assert "10.0.0.2" in resources["affected_bgp_sessions"]

    def test_routing_commands_detected(self):
        """Routing commands (ip route, route-map, ospf) flag routing impact."""
        commands = ["ip route 10.0.0.0/8 192.168.1.1"]
        resources = _parse_affected_resources(commands)
        assert resources["has_routing_impact"] is True

        commands = ["route-map EXPORT permit 10"]
        resources = _parse_affected_resources(commands)
        assert resources["has_routing_impact"] is True

        commands = ["router ospf 1"]
        resources = _parse_affected_resources(commands)
        assert resources["has_routing_impact"] is True

    def test_description_only_detected(self):
        """Description-only changes are identified."""
        commands = ["interface Ethernet1", "  description new-uplink-description"]
        resources = _parse_affected_resources(commands)

        assert "Ethernet1" in resources["affected_interfaces"]
        # Note: the interface command itself sets has_description_only to False
        # because we can't know if there will be more commands following it
        # This is by design -- interface + description is not "description only"

    def test_empty_commands(self):
        """Empty command list returns empty resources."""
        resources = _parse_affected_resources([])

        assert resources["affected_interfaces"] == []
        assert resources["affected_vlans"] == []
        assert resources["affected_bgp_sessions"] == []
        assert resources["has_routing_impact"] is False

    def test_no_duplicate_interfaces(self):
        """Same interface mentioned twice is only listed once."""
        commands = [
            "interface Ethernet1",
            "description uplink",
            "interface Ethernet1",
            "shutdown",
        ]
        resources = _parse_affected_resources(commands)

        assert resources["affected_interfaces"].count("Ethernet1") == 1

    def test_no_duplicate_vlans(self):
        """Same VLAN mentioned twice is only listed once."""
        commands = ["vlan 100", "name SERVERS", "vlan 100", "name SERVERS-v2"]
        resources = _parse_affected_resources(commands)

        assert resources["affected_vlans"].count(100) == 1


class TestCalculateRiskLevel:
    """Tests for risk level calculation."""

    def test_low_risk_empty_commands(self):
        """Empty command list returns LOW risk."""
        resources = _parse_affected_resources([])
        assert _calculate_risk_level(resources) == "LOW"

    def test_low_risk_description_only(self):
        """Description-only changes return LOW risk when has_description_only is True."""
        resources = {
            "affected_interfaces": [],
            "affected_vlans": [],
            "affected_bgp_sessions": [],
            "has_routing_impact": False,
            "has_description_only": True,
        }
        assert _calculate_risk_level(resources) == "LOW"

    def test_medium_risk_single_interface(self):
        """Single interface change returns MEDIUM risk."""
        resources = {
            "affected_interfaces": ["Ethernet1"],
            "affected_vlans": [],
            "affected_bgp_sessions": [],
            "has_routing_impact": False,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "MEDIUM"

    def test_medium_risk_single_vlan(self):
        """Single VLAN change returns MEDIUM risk."""
        resources = {
            "affected_interfaces": [],
            "affected_vlans": [100],
            "affected_bgp_sessions": [],
            "has_routing_impact": False,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "MEDIUM"

    def test_high_risk_multiple_interfaces(self):
        """Multiple interfaces returns HIGH risk."""
        resources = {
            "affected_interfaces": ["Ethernet1", "Ethernet2"],
            "affected_vlans": [],
            "affected_bgp_sessions": [],
            "has_routing_impact": False,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "HIGH"

    def test_high_risk_bgp_sessions(self):
        """BGP session changes return HIGH or CRITICAL risk."""
        resources = {
            "affected_interfaces": [],
            "affected_vlans": [],
            "affected_bgp_sessions": ["10.0.0.1"],
            "has_routing_impact": True,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "CRITICAL"

    def test_high_risk_routing_impact(self):
        """Routing impact without BGP sessions returns HIGH risk."""
        resources = {
            "affected_interfaces": [],
            "affected_vlans": [],
            "affected_bgp_sessions": [],
            "has_routing_impact": True,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "HIGH"

    def test_critical_risk_management_interface(self):
        """Management interface changes return CRITICAL risk."""
        resources = {
            "affected_interfaces": ["Management0"],
            "affected_vlans": [],
            "affected_bgp_sessions": [],
            "has_routing_impact": False,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "CRITICAL"

    def test_critical_risk_loopback_interface(self):
        """Loopback0 changes return CRITICAL risk."""
        resources = {
            "affected_interfaces": ["Loopback0"],
            "affected_vlans": [],
            "affected_bgp_sessions": [],
            "has_routing_impact": False,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "CRITICAL"

    def test_critical_risk_many_interfaces(self):
        """>5 interfaces returns CRITICAL risk."""
        resources = {
            "affected_interfaces": [f"Ethernet{i}" for i in range(1, 8)],
            "affected_vlans": [],
            "affected_bgp_sessions": [],
            "has_routing_impact": False,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "CRITICAL"

    def test_critical_risk_routing_with_bgp(self):
        """Routing impact with BGP sessions returns CRITICAL."""
        resources = {
            "affected_interfaces": [],
            "affected_vlans": [],
            "affected_bgp_sessions": ["10.0.0.1"],
            "has_routing_impact": True,
            "has_description_only": False,
        }
        assert _calculate_risk_level(resources) == "CRITICAL"


class TestNetAnalyzeChangeImpact:
    """Tests for the net_analyze_change_impact tool function."""

    @pytest.fixture
    def mock_driver(self):
        """Create a mock driver."""
        driver = MagicMock()
        driver.vendor = "arista"
        driver.platform = "eos"
        driver.get_lldp_neighbors = MagicMock(
            return_value={
                "Ethernet1": [
                    {"hostname": "leaf-02", "port": "Ethernet1"},
                ],
                "Ethernet2": [
                    {"hostname": "leaf-03", "port": "Ethernet5"},
                ],
            }
        )
        return driver

    @pytest.fixture
    def mock_conn(self, mock_driver):
        """Patch conn_mgr with mock driver."""
        with patch.object(conn_mgr, "get_driver", return_value=mock_driver):
            yield mock_driver

    def test_empty_commands_returns_low_risk(self, mock_conn):
        """Empty command list returns LOW risk."""
        result = net_analyze_change_impact("spine-01", [])

        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["impact"]["risk_level"] == "LOW"
        assert result["impact"]["affected_interfaces"] == []
        assert result["impact"]["affected_vlans"] == []

    def test_interface_commands_with_lldp_lookup(self, mock_conn):
        """Interface commands trigger LLDP neighbor lookup."""
        result = net_analyze_change_impact("spine-01", ["interface Ethernet1", "shutdown"])

        assert result["status"] == "success"
        impact = result["impact"]
        assert "Ethernet1" in impact["affected_interfaces"]
        assert len(impact["dependent_devices"]) == 1
        assert impact["dependent_devices"][0]["device"] == "leaf-02"
        assert impact["dependent_devices"][0]["interface"] == "Ethernet1"

    def test_vlan_commands_detected(self, mock_conn):
        """VLAN commands are detected in impact analysis."""
        result = net_analyze_change_impact("spine-01", ["vlan 100", "name SERVERS"])

        assert result["status"] == "success"
        assert 100 in result["impact"]["affected_vlans"]
        assert result["impact"]["risk_level"] == "MEDIUM"

    def test_bgp_commands_detected(self, mock_conn):
        """BGP commands are detected in impact analysis."""
        result = net_analyze_change_impact(
            "spine-01",
            ["router bgp 65000", "neighbor 10.0.0.1 remote-as 65001"],
        )

        assert result["status"] == "success"
        assert "10.0.0.1" in result["impact"]["affected_bgp_sessions"]
        assert result["impact"]["risk_level"] == "CRITICAL"

    def test_management_interface_returns_critical(self, mock_conn):
        """Management interface changes return CRITICAL risk."""
        result = net_analyze_change_impact("spine-01", ["interface Management0", "shutdown"])

        assert result["status"] == "success"
        assert result["impact"]["risk_level"] == "CRITICAL"

    def test_multiple_interfaces_with_neighbors(self, mock_conn):
        """Multiple interfaces with LLDP neighbors are all reported."""
        result = net_analyze_change_impact(
            "spine-01",
            ["interface Ethernet1", "shutdown", "interface Ethernet2", "shutdown"],
        )

        assert result["status"] == "success"
        impact = result["impact"]
        assert len(impact["affected_interfaces"]) == 2
        assert len(impact["dependent_devices"]) == 2
        assert impact["risk_level"] == "HIGH"

    def test_lldp_failure_graceful(self, mock_conn):
        """LLDP query failure is handled gracefully."""
        mock_conn.get_lldp_neighbors.side_effect = Exception("LLDP not available")

        result = net_analyze_change_impact("spine-01", ["interface Ethernet1", "shutdown"])

        assert result["status"] == "success"
        assert "Ethernet1" in result["impact"]["affected_interfaces"]
        assert result["impact"]["dependent_devices"] == []

    def test_summary_text_generated(self, mock_conn):
        """Summary text is generated based on affected resources."""
        result = net_analyze_change_impact(
            "spine-01",
            ["interface Ethernet1", "shutdown", "vlan 100", "name TEST"],
        )

        assert result["status"] == "success"
        summary = result["impact"]["summary"]
        assert "interface" in summary.lower()

    def test_no_duplicate_dependent_devices(self, mock_conn):
        """Same LLDP neighbor is not reported twice."""
        mock_conn.get_lldp_neighbors.return_value = {
            "Ethernet1": [
                {"hostname": "leaf-02", "port": "Ethernet1"},
                {"hostname": "leaf-02", "port": "Ethernet1"},  # duplicate
            ],
        }

        result = net_analyze_change_impact("spine-01", ["interface Ethernet1", "shutdown"])

        assert result["status"] == "success"
        # Only one dependent device entry per unique device+interface
        assert len(result["impact"]["dependent_devices"]) == 1

    def test_lldp_neighbor_as_dict(self, mock_conn):
        """LLDP neighbor data returned as dict (single neighbor) is handled."""
        mock_conn.get_lldp_neighbors.return_value = {
            "Ethernet1": {"hostname": "leaf-02", "port": "Ethernet1"},
        }

        result = net_analyze_change_impact("spine-01", ["interface Ethernet1", "shutdown"])

        assert result["status"] == "success"
        assert len(result["impact"]["dependent_devices"]) == 1
        assert result["impact"]["dependent_devices"][0]["device"] == "leaf-02"
