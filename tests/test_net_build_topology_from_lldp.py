"""Tests for net_build_topology_from_lldp tool.

Validates graph construction, edge deduplication, partial failure handling,
placeholder node creation, and interface speed extraction across a 3-device
mock topology: spine-01 <-> leaf-01 <-> leaf-02, spine-01 <-> leaf-02.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Mock topology data
#
#   spine-01 ──Eth1── leaf-01:Eth49
#   spine-01 ──Eth2── leaf-02:Eth49
#   leaf-01  ──Eth1── leaf-02:Eth1   (inter-leaf link)
# ---------------------------------------------------------------------------

SPINE01_FACTS = {"hostname": "spine-01", "platform": "eos", "vendor": "arista"}
LEAF01_FACTS = {"hostname": "leaf-01", "platform": "eos", "vendor": "arista"}
LEAF02_FACTS = {"hostname": "leaf-02", "platform": "eos", "vendor": "arista"}

SPINE01_LLDP = {
    "Ethernet1": [{"hostname": "leaf-01", "port": "Ethernet49", "system_description": "Arista EOS"}],
    "Ethernet2": [{"hostname": "leaf-02", "port": "Ethernet49", "system_description": "Arista EOS"}],
}
LEAF01_LLDP = {
    "Ethernet49": [{"hostname": "spine-01", "port": "Ethernet1", "system_description": "Arista EOS"}],
    "Ethernet1": [{"hostname": "leaf-02", "port": "Ethernet1", "system_description": "Arista EOS"}],
}
LEAF02_LLDP = {
    "Ethernet49": [{"hostname": "spine-01", "port": "Ethernet2", "system_description": "Arista EOS"}],
    "Ethernet1": [{"hostname": "leaf-01", "port": "Ethernet1", "system_description": "Arista EOS"}],
}

SPINE01_INTERFACES = {
    "Ethernet1": {"is_up": True, "is_enabled": True, "speed": 100000, "description": "to leaf-01"},
    "Ethernet2": {"is_up": True, "is_enabled": True, "speed": 100000, "description": "to leaf-02"},
}
LEAF01_INTERFACES = {
    "Ethernet49": {"is_up": True, "is_enabled": True, "speed": 100000, "description": "to spine-01"},
    "Ethernet1": {"is_up": True, "is_enabled": True, "speed": 25000, "description": "to leaf-02"},
}
LEAF02_INTERFACES = {
    "Ethernet49": {"is_up": True, "is_enabled": True, "speed": 100000, "description": "to spine-01"},
    "Ethernet1": {"is_up": True, "is_enabled": True, "speed": 25000, "description": "to leaf-01"},
}


def _make_driver(facts: dict, lldp: dict, interfaces: dict | None = None) -> MagicMock:
    """Create a mock NetworkDriver with preset return values."""
    driver = MagicMock()
    driver.vendor = facts.get("vendor", "arista")
    driver.platform = facts.get("platform", "eos")
    driver.get_facts.return_value = facts
    driver.get_lldp_neighbors.return_value = lldp
    driver.get_interfaces.return_value = interfaces or {}
    return driver


def _make_drivers_map() -> dict[str, MagicMock]:
    """Return host -> driver mapping for the 3-device topology."""
    return {
        "spine-01": _make_driver(SPINE01_FACTS, SPINE01_LLDP, SPINE01_INTERFACES),
        "leaf-01": _make_driver(LEAF01_FACTS, LEAF01_LLDP, LEAF01_INTERFACES),
        "leaf-02": _make_driver(LEAF02_FACTS, LEAF02_LLDP, LEAF02_INTERFACES),
    }


@pytest.fixture()
def three_device_drivers():
    """Patch conn_mgr.get_driver with the 3-device topology drivers."""
    drivers = _make_drivers_map()

    def _get_driver(host):
        if host not in drivers:
            raise ConnectionError(f"Cannot connect to {host}")
        return drivers[host]

    with patch.object(conn_mgr, "get_driver", side_effect=_get_driver):
        yield drivers


# ===========================================================================
# Basic response structure
# ===========================================================================


class TestTopologyResponseStructure:
    def test_status_success(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        assert result["status"] == "success"

    def test_has_topology_key(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        assert "topology" in result
        assert "nodes" in result["topology"]
        assert "edges" in result["topology"]

    def test_nodes_is_list(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        assert isinstance(result["topology"]["nodes"], list)

    def test_edges_is_list(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        assert isinstance(result["topology"]["edges"], list)


# ===========================================================================
# Node construction
# ===========================================================================


class TestTopologyNodes:
    def test_all_queried_hosts_present(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        node_ids = {n["id"] for n in result["topology"]["nodes"]}
        assert "spine-01" in node_ids
        assert "leaf-01" in node_ids
        assert "leaf-02" in node_ids

    def test_node_has_required_fields(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01"])
        node = next(n for n in result["topology"]["nodes"] if n["id"] == "spine-01")
        assert "id" in node
        assert "hostname" in node
        assert "platform" in node
        assert "management_ip" in node

    def test_node_hostname_from_facts(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01"])
        node = next(n for n in result["topology"]["nodes"] if n["id"] == "spine-01")
        assert node["hostname"] == "spine-01"

    def test_node_platform_from_facts(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01"])
        node = next(n for n in result["topology"]["nodes"] if n["id"] == "spine-01")
        assert node["platform"] == "eos"

    def test_node_management_ip_is_host(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01"])
        node = next(n for n in result["topology"]["nodes"] if n["id"] == "spine-01")
        assert node["management_ip"] == "spine-01"

    def test_empty_hosts_list(self):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp([])
        assert result["status"] == "success"
        assert result["topology"]["nodes"] == []
        assert result["topology"]["edges"] == []


# ===========================================================================
# Edge construction
# ===========================================================================


class TestTopologyEdges:
    def test_correct_edge_count_no_duplicates(self, three_device_drivers):
        """3-device ring: spine-leaf1, spine-leaf2, leaf1-leaf2 → 3 unique edges."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        assert len(result["topology"]["edges"]) == 3

    def test_edge_has_required_fields(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        edge = result["topology"]["edges"][0]
        assert "source" in edge
        assert "target" in edge
        assert "source_port" in edge
        assert "target_port" in edge
        assert "speed" in edge

    def test_spine_leaf1_edge_exists(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        edges = result["topology"]["edges"]
        # Link: spine-01:Eth1 <-> leaf-01:Eth49
        link_ports = {frozenset([(e["source"], e["source_port"]), (e["target"], e["target_port"])]) for e in edges}
        assert frozenset([("spine-01", "Ethernet1"), ("leaf-01", "Ethernet49")]) in link_ports

    def test_spine_leaf2_edge_exists(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        edges = result["topology"]["edges"]
        link_ports = {frozenset([(e["source"], e["source_port"]), (e["target"], e["target_port"])]) for e in edges}
        assert frozenset([("spine-01", "Ethernet2"), ("leaf-02", "Ethernet49")]) in link_ports

    def test_inter_leaf_edge_exists(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        edges = result["topology"]["edges"]
        link_ports = {frozenset([(e["source"], e["source_port"]), (e["target"], e["target_port"])]) for e in edges}
        assert frozenset([("leaf-01", "Ethernet1"), ("leaf-02", "Ethernet1")]) in link_ports

    def test_edge_speed_populated(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        edges = result["topology"]["edges"]
        # spine-01:Eth1 -> leaf-01 should carry speed=100000 from SPINE01_INTERFACES
        spine_leaf1 = next(
            e for e in edges if e["source"] == "spine-01" and e["source_port"] == "Ethernet1"
        )
        assert spine_leaf1["speed"] == 100000

    def test_edge_speed_zero_when_interface_missing(self):
        """When get_interfaces fails, speeds default to 0."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        driver = _make_driver(SPINE01_FACTS, SPINE01_LLDP, interfaces=None)
        driver.get_interfaces.side_effect = RuntimeError("not available")
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_build_topology_from_lldp(["spine-01"])

        for edge in result["topology"]["edges"]:
            assert edge["speed"] == 0


# ===========================================================================
# Deduplication
# ===========================================================================


class TestEdgeDeduplication:
    def test_symmetric_lldp_does_not_duplicate_edge(self):
        """Both sides of a link report each other; the edge should appear exactly once."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        # Minimal topology: each device only knows about the other
        spine_lldp = {"Ethernet1": [{"hostname": "leaf-01", "port": "Ethernet49", "system_description": ""}]}
        leaf_lldp = {"Ethernet49": [{"hostname": "spine-01", "port": "Ethernet1", "system_description": ""}]}
        drivers = {
            "spine-01": _make_driver(SPINE01_FACTS, spine_lldp, {}),
            "leaf-01": _make_driver(LEAF01_FACTS, leaf_lldp, {}),
        }

        with patch.object(conn_mgr, "get_driver", side_effect=lambda h: drivers[h]):
            result = net_build_topology_from_lldp(["spine-01", "leaf-01"])

        edges = result["topology"]["edges"]
        assert len(edges) == 1

    def test_single_device_creates_no_duplicate_edges(self, three_device_drivers):
        """When only one side of a link is queried, there's still exactly one edge."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01"])
        edges = result["topology"]["edges"]
        # spine-01 has 2 LLDP entries → 2 edges, no duplicates
        assert len(edges) == 2


# ===========================================================================
# Placeholder nodes for unknown neighbours
# ===========================================================================


class TestPlaceholderNodes:
    def test_neighbor_not_in_hosts_gets_placeholder_node(self):
        """Neighbours that weren't queried appear as placeholder nodes."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        driver = _make_driver(SPINE01_FACTS, SPINE01_LLDP)
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_build_topology_from_lldp(["spine-01"])

        node_ids = {n["id"] for n in result["topology"]["nodes"]}
        assert "leaf-01" in node_ids
        assert "leaf-02" in node_ids

    def test_placeholder_node_fields(self):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        driver = _make_driver(SPINE01_FACTS, SPINE01_LLDP)
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_build_topology_from_lldp(["spine-01"])

        leaf1 = next(n for n in result["topology"]["nodes"] if n["id"] == "leaf-01")
        assert leaf1["hostname"] == "leaf-01"
        assert leaf1["platform"] == ""
        assert leaf1["management_ip"] == ""
        assert "error" not in leaf1

    def test_placeholder_not_created_when_host_already_queried(self, three_device_drivers):
        """Queried hosts should not be overwritten with placeholder data."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01", "leaf-01", "leaf-02"])
        leaf1 = next(n for n in result["topology"]["nodes"] if n["id"] == "leaf-01")
        # Should have real platform data, not empty placeholder
        assert leaf1["platform"] == "eos"


# ===========================================================================
# Partial failure handling
# ===========================================================================


class TestPartialFailures:
    def test_unreachable_host_included_as_node_with_error(self):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        def _get_driver(host):
            if host == "bad-host":
                raise ConnectionError("unreachable")
            return _make_driver(SPINE01_FACTS, SPINE01_LLDP)

        with patch.object(conn_mgr, "get_driver", side_effect=_get_driver):
            result = net_build_topology_from_lldp(["spine-01", "bad-host"])

        assert result["status"] == "success"
        node_ids = {n["id"] for n in result["topology"]["nodes"]}
        assert "bad-host" in node_ids

        bad_node = next(n for n in result["topology"]["nodes"] if n["id"] == "bad-host")
        assert "error" in bad_node
        assert bad_node["platform"] == ""

    def test_unreachable_host_does_not_prevent_other_edges(self):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        def _get_driver(host):
            if host == "bad-host":
                raise ConnectionError("unreachable")
            return _make_driver(SPINE01_FACTS, SPINE01_LLDP)

        with patch.object(conn_mgr, "get_driver", side_effect=_get_driver):
            result = net_build_topology_from_lldp(["spine-01", "bad-host"])

        # spine-01 still contributes its 2 edges
        assert len(result["topology"]["edges"]) == 2

    def test_lldp_failure_adds_error_to_node(self):
        """LLDP query failure after successful connect marks node with error."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        driver = _make_driver(SPINE01_FACTS, {})
        driver.get_lldp_neighbors.side_effect = RuntimeError("lldp unavailable")
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_build_topology_from_lldp(["spine-01"])

        assert result["status"] == "success"
        node = next(n for n in result["topology"]["nodes"] if n["id"] == "spine-01")
        assert "error" in node
        assert result["topology"]["edges"] == []

    def test_facts_failure_uses_host_as_fallback_hostname(self):
        """When get_facts() raises, the host parameter is used as the hostname."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        driver = _make_driver(SPINE01_FACTS, SPINE01_LLDP)
        driver.get_facts.side_effect = RuntimeError("facts unavailable")
        with patch.object(conn_mgr, "get_driver", return_value=driver):
            result = net_build_topology_from_lldp(["spine-01"])

        node = next(n for n in result["topology"]["nodes"] if n["id"] == "spine-01")
        assert node["hostname"] == "spine-01"
        assert node["platform"] == ""
        # LLDP still worked so no error on node
        assert "error" not in node

    def test_all_hosts_fail_returns_success_with_error_nodes(self):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timeout")):
            result = net_build_topology_from_lldp(["spine-01", "leaf-01"])

        assert result["status"] == "success"
        assert len(result["topology"]["nodes"]) == 2
        assert result["topology"]["edges"] == []
        for node in result["topology"]["nodes"]:
            assert "error" in node

    def test_single_host_success(self, three_device_drivers):
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        result = net_build_topology_from_lldp(["spine-01"])
        assert result["status"] == "success"
        assert any(n["id"] == "spine-01" for n in result["topology"]["nodes"])

    def test_mixed_success_and_failure(self):
        """One host succeeds, one fails; both appear in nodes."""
        from network_mcp.tools.common.switching import net_build_topology_from_lldp

        drivers = {
            "spine-01": _make_driver(SPINE01_FACTS, {"Ethernet1": []}, {}),
        }

        def _get_driver(host):
            if host not in drivers:
                raise ConnectionError(f"no route to {host}")
            return drivers[host]

        with patch.object(conn_mgr, "get_driver", side_effect=_get_driver):
            result = net_build_topology_from_lldp(["spine-01", "leaf-01"])

        node_ids = {n["id"] for n in result["topology"]["nodes"]}
        assert "spine-01" in node_ids
        assert "leaf-01" in node_ids

        leaf1 = next(n for n in result["topology"]["nodes"] if n["id"] == "leaf-01")
        assert "error" in leaf1
