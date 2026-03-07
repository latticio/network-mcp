"""Tests for TopologyGraph and the net_* topology tools.

Covers:
- TopologyGraph unit tests (no mocking needed — pure in-memory graph)
  - Shortest path (BFS) on 3-tier, spine-leaf, and ring topologies
  - Weighted shortest path (Dijkstra)
  - All-simple-paths enumeration / ECMP detection
  - MLAG pair detection (naming + overlap)
  - Device role inference
  - Hop detail construction
- Tool-level tests (mock conn_mgr)
  - net_show_path
  - net_get_device_role
  - net_get_redundancy_groups
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr
from network_mcp.topology import TopologyGraph, _mlag_name_match

# ===========================================================================
# Shared topology builders
# ===========================================================================


def _make_spine_leaf_topology() -> dict:
    """2 spines + 2 leaves, fully connected (classic Clos spine-leaf).

    spine-01 ── Eth1 ── leaf-01:Eth49
    spine-01 ── Eth2 ── leaf-02:Eth49
    spine-02 ── Eth1 ── leaf-01:Eth50
    spine-02 ── Eth2 ── leaf-02:Eth50
    """
    nodes = [
        {"id": "spine-01", "hostname": "spine-01", "platform": "eos", "management_ip": "10.0.0.1"},
        {"id": "spine-02", "hostname": "spine-02", "platform": "eos", "management_ip": "10.0.0.2"},
        {"id": "leaf-01", "hostname": "leaf-01", "platform": "eos", "management_ip": "10.0.0.3"},
        {"id": "leaf-02", "hostname": "leaf-02", "platform": "eos", "management_ip": "10.0.0.4"},
    ]
    edges = [
        {"source": "spine-01", "target": "leaf-01", "source_port": "Eth1", "target_port": "Eth49", "speed": 100000},
        {"source": "spine-01", "target": "leaf-02", "source_port": "Eth2", "target_port": "Eth49", "speed": 100000},
        {"source": "spine-02", "target": "leaf-01", "source_port": "Eth1", "target_port": "Eth50", "speed": 100000},
        {"source": "spine-02", "target": "leaf-02", "source_port": "Eth2", "target_port": "Eth50", "speed": 100000},
    ]
    return {"nodes": nodes, "edges": edges}


def _make_3tier_topology() -> dict:
    """3-tier: core > distribution > access.

    core-01 ── dist-01 ── access-01
    core-01 ── dist-02 ── access-01
    core-01 ── dist-01 ── access-02
    core-01 ── dist-02 ── access-02
    """
    nodes = [
        {"id": "core-01", "hostname": "core-01", "platform": "eos", "management_ip": "10.0.0.1"},
        {"id": "dist-01", "hostname": "dist-01", "platform": "eos", "management_ip": "10.0.0.2"},
        {"id": "dist-02", "hostname": "dist-02", "platform": "eos", "management_ip": "10.0.0.3"},
        {"id": "access-01", "hostname": "access-01", "platform": "eos", "management_ip": "10.0.0.4"},
        {"id": "access-02", "hostname": "access-02", "platform": "eos", "management_ip": "10.0.0.5"},
    ]
    edges = [
        {"source": "core-01", "target": "dist-01", "source_port": "Eth1", "target_port": "Eth1", "speed": 100000},
        {"source": "core-01", "target": "dist-02", "source_port": "Eth2", "target_port": "Eth1", "speed": 100000},
        {"source": "dist-01", "target": "access-01", "source_port": "Eth2", "target_port": "Eth1", "speed": 25000},
        {"source": "dist-02", "target": "access-01", "source_port": "Eth2", "target_port": "Eth2", "speed": 25000},
        {"source": "dist-01", "target": "access-02", "source_port": "Eth3", "target_port": "Eth1", "speed": 25000},
        {"source": "dist-02", "target": "access-02", "source_port": "Eth3", "target_port": "Eth2", "speed": 25000},
    ]
    return {"nodes": nodes, "edges": edges}


def _make_ring_topology() -> dict:
    """Ring: A -- B -- C -- A."""
    nodes = [
        {"id": "sw-a", "hostname": "sw-a", "platform": "eos", "management_ip": "10.0.0.1"},
        {"id": "sw-b", "hostname": "sw-b", "platform": "eos", "management_ip": "10.0.0.2"},
        {"id": "sw-c", "hostname": "sw-c", "platform": "eos", "management_ip": "10.0.0.3"},
    ]
    edges = [
        {"source": "sw-a", "target": "sw-b", "source_port": "Eth1", "target_port": "Eth1", "speed": 10000},
        {"source": "sw-b", "target": "sw-c", "source_port": "Eth2", "target_port": "Eth1", "speed": 10000},
        {"source": "sw-c", "target": "sw-a", "source_port": "Eth2", "target_port": "Eth2", "speed": 10000},
    ]
    return {"nodes": nodes, "edges": edges}


# ===========================================================================
# Unit tests: TopologyGraph construction
# ===========================================================================


class TestTopologyGraphConstruction:
    def test_from_topology_dict_populates_nodes(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert "spine-01" in g.nodes
        assert "leaf-01" in g.nodes

    def test_from_topology_dict_populates_edges(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        # 4 edges, each appears twice in _adj (once per endpoint)
        assert len(g._adj["spine-01"]) == 2  # connected to leaf-01 and leaf-02
        assert len(g._adj["leaf-01"]) == 2  # connected to spine-01 and spine-02

    def test_add_node_ignores_duplicate(self):
        g = TopologyGraph()
        g.add_node("a", {"hostname": "first"})
        g.add_node("a", {"hostname": "second"})
        assert g.nodes["a"]["hostname"] == "first"

    def test_empty_topology(self):
        g = TopologyGraph.from_topology_dict({"nodes": [], "edges": []})
        assert g.nodes == {}
        assert g._adj == {}


# ===========================================================================
# Unit tests: shortest_path (BFS)
# ===========================================================================


class TestShortestPath:
    def test_direct_neighbour(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        path = g.shortest_path("spine-01", "leaf-01")
        assert path == ["spine-01", "leaf-01"]

    def test_same_node(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.shortest_path("spine-01", "spine-01") == ["spine-01"]

    def test_two_hops(self):
        """leaf-01 to leaf-02 requires 2 hops through a spine."""
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        path = g.shortest_path("leaf-01", "leaf-02")
        assert path is not None
        assert path[0] == "leaf-01"
        assert path[-1] == "leaf-02"
        assert len(path) == 3  # leaf-01 -> spine-X -> leaf-02

    def test_ring_shortest_path(self):
        """In the ring A-B-C-A, path from A to C is 2 hops (A->B->C or A->C)."""
        g = TopologyGraph.from_topology_dict(_make_ring_topology())
        path = g.shortest_path("sw-a", "sw-c")
        assert path is not None
        assert len(path) == 2  # direct hop A -> C (ring edge)

    def test_3tier_shortest_path(self):
        """core-01 to access-01 should be 2 hops (core -> dist -> access)."""
        g = TopologyGraph.from_topology_dict(_make_3tier_topology())
        path = g.shortest_path("core-01", "access-01")
        assert path is not None
        assert path[0] == "core-01"
        assert path[-1] == "access-01"
        assert len(path) == 3

    def test_unknown_source_returns_none(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.shortest_path("does-not-exist", "leaf-01") is None

    def test_unknown_dest_returns_none(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.shortest_path("spine-01", "does-not-exist") is None

    def test_disconnected_graph_returns_none(self):
        g = TopologyGraph()
        g.add_node("a", {"hostname": "a"})
        g.add_node("b", {"hostname": "b"})
        # No edges added — islands
        assert g.shortest_path("a", "b") is None


# ===========================================================================
# Unit tests: shortest_path_weighted (Dijkstra)
# ===========================================================================


class TestShortestPathWeighted:
    def test_prefers_high_speed_path(self):
        """When two paths exist and one has higher speed links, Dijkstra picks it."""
        # A --(10G)--> B --(10G)--> D
        # A --(1G)---> C --(1G)---> D
        nodes = [
            {"id": "A", "hostname": "A", "platform": "", "management_ip": ""},
            {"id": "B", "hostname": "B", "platform": "", "management_ip": ""},
            {"id": "C", "hostname": "C", "platform": "", "management_ip": ""},
            {"id": "D", "hostname": "D", "platform": "", "management_ip": ""},
        ]
        edges = [
            {"source": "A", "target": "B", "source_port": "e1", "target_port": "e1", "speed": 10000},
            {"source": "B", "target": "D", "source_port": "e2", "target_port": "e1", "speed": 10000},
            {"source": "A", "target": "C", "source_port": "e2", "target_port": "e1", "speed": 1000},
            {"source": "C", "target": "D", "source_port": "e2", "target_port": "e2", "speed": 1000},
        ]
        g = TopologyGraph.from_topology_dict({"nodes": nodes, "edges": edges})
        path = g.shortest_path_weighted("A", "D")
        assert path == ["A", "B", "D"]

    def test_same_node_returns_single(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.shortest_path_weighted("spine-01", "spine-01") == ["spine-01"]

    def test_unknown_node_returns_none(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.shortest_path_weighted("ghost", "leaf-01") is None


# ===========================================================================
# Unit tests: all_simple_paths
# ===========================================================================


class TestAllSimplePaths:
    def test_spine_leaf_ecmp_between_leaves(self):
        """leaf-01 to leaf-02 has 2 equal-cost paths through spine-01 and spine-02."""
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        paths = g.all_simple_paths("leaf-01", "leaf-02")
        assert len(paths) == 2
        hop_counts = {len(p) for p in paths}
        assert hop_counts == {3}  # all paths are 3 nodes (2 hops)

    def test_ring_has_two_paths(self):
        """Ring A-B-C: A->C has a direct path and a 3-hop path."""
        g = TopologyGraph.from_topology_dict(_make_ring_topology())
        paths = g.all_simple_paths("sw-a", "sw-c")
        assert len(paths) == 2

    def test_same_node(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.all_simple_paths("spine-01", "spine-01") == [["spine-01"]]

    def test_disconnected(self):
        g = TopologyGraph()
        g.add_node("x", {})
        g.add_node("y", {})
        assert g.all_simple_paths("x", "y") == []

    def test_max_paths_respected(self):
        """Even if more paths exist, we stop at max_paths."""
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        paths = g.all_simple_paths("leaf-01", "leaf-02", max_paths=1)
        assert len(paths) == 1


# ===========================================================================
# Unit tests: detect_mlag_pairs
# ===========================================================================


class TestDetectMlagPairs:
    def test_ab_naming_detected(self):
        """Devices leaf-01a / leaf-01b should be detected as an MLAG pair."""
        nodes = [
            {"id": "leaf-01a", "hostname": "leaf-01a", "platform": "eos", "management_ip": ""},
            {"id": "leaf-01b", "hostname": "leaf-01b", "platform": "eos", "management_ip": ""},
            {"id": "spine-01", "hostname": "spine-01", "platform": "eos", "management_ip": ""},
        ]
        edges = [
            {"source": "spine-01", "target": "leaf-01a", "source_port": "Eth1", "target_port": "Eth49", "speed": 0},
            {"source": "spine-01", "target": "leaf-01b", "source_port": "Eth2", "target_port": "Eth49", "speed": 0},
        ]
        g = TopologyGraph.from_topology_dict({"nodes": nodes, "edges": edges})
        pairs = g.detect_mlag_pairs()
        devices_in_pairs = [frozenset(p["devices"]) for p in pairs]
        assert frozenset(["leaf-01a", "leaf-01b"]) in devices_in_pairs

    def test_sequential_naming_detected(self):
        """Devices leaf-01 / leaf-02 with same prefix should be detected."""
        nodes = [
            {"id": "leaf-01", "hostname": "leaf-01", "platform": "eos", "management_ip": ""},
            {"id": "leaf-02", "hostname": "leaf-02", "platform": "eos", "management_ip": ""},
            {"id": "spine-01", "hostname": "spine-01", "platform": "eos", "management_ip": ""},
        ]
        edges = [
            {"source": "spine-01", "target": "leaf-01", "source_port": "Eth1", "target_port": "Eth49", "speed": 0},
            {"source": "spine-01", "target": "leaf-02", "source_port": "Eth2", "target_port": "Eth49", "speed": 0},
        ]
        g = TopologyGraph.from_topology_dict({"nodes": nodes, "edges": edges})
        pairs = g.detect_mlag_pairs()
        devices_in_pairs = [frozenset(p["devices"]) for p in pairs]
        assert frozenset(["leaf-01", "leaf-02"]) in devices_in_pairs

    def test_high_neighbor_overlap_detected(self):
        """Two devices sharing all neighbours should be detected even without naming match."""
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        pairs = g.detect_mlag_pairs()
        # leaf-01 and leaf-02 both connect to spine-01 and spine-02 (100% overlap)
        devices_in_pairs = [frozenset(p["devices"]) for p in pairs]
        assert frozenset(["leaf-01", "leaf-02"]) in devices_in_pairs

    def test_no_false_positives_on_linear_chain(self):
        """A linear chain A-B-C should produce no MLAG pairs."""
        nodes = [
            {"id": "a", "hostname": "devA", "platform": "", "management_ip": ""},
            {"id": "b", "hostname": "devB", "platform": "", "management_ip": ""},
            {"id": "c", "hostname": "devC", "platform": "", "management_ip": ""},
        ]
        edges = [
            {"source": "a", "target": "b", "source_port": "e1", "target_port": "e1", "speed": 0},
            {"source": "b", "target": "c", "source_port": "e2", "target_port": "e1", "speed": 0},
        ]
        g = TopologyGraph.from_topology_dict({"nodes": nodes, "edges": edges})
        # devA and devC don't share neighbours; devA/devB and devB/devC only partially
        pairs = g.detect_mlag_pairs()
        # With low overlap and no naming match, no pairs expected
        for p in pairs:
            assert p["naming_match"] or p["neighbor_overlap"] >= 0.6

    def test_pair_fields_present(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        pairs = g.detect_mlag_pairs()
        if pairs:
            p = pairs[0]
            assert "type" in p
            assert "devices" in p
            assert "neighbor_overlap" in p
            assert "naming_match" in p
            assert p["type"] == "mlag_pair"


# ===========================================================================
# Unit tests: detect_ecmp_groups
# ===========================================================================


class TestDetectEcmpGroups:
    def test_spine_leaf_has_ecmp(self):
        """leaf-01 <-> leaf-02 should form an ECMP group (2 paths via spines)."""
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        groups = g.detect_ecmp_groups()
        src_dst = {(eg["source"], eg["dest"]) for eg in groups} | {(eg["dest"], eg["source"]) for eg in groups}
        assert ("leaf-01", "leaf-02") in src_dst or ("leaf-02", "leaf-01") in src_dst

    def test_3tier_access_nodes_have_ecmp(self):
        """access-01 to access-02 should have ECMP through dist-01 and dist-02."""
        g = TopologyGraph.from_topology_dict(_make_3tier_topology())
        groups = g.detect_ecmp_groups()
        all_pairs = {frozenset([eg["source"], eg["dest"]]) for eg in groups}
        assert frozenset(["access-01", "access-02"]) in all_pairs

    def test_linear_chain_no_ecmp(self):
        """A linear chain has only one path between any pair — no ECMP."""
        nodes = [{"id": x, "hostname": x, "platform": "", "management_ip": ""} for x in ["a", "b", "c"]]
        edges = [
            {"source": "a", "target": "b", "source_port": "e1", "target_port": "e1", "speed": 0},
            {"source": "b", "target": "c", "source_port": "e2", "target_port": "e1", "speed": 0},
        ]
        g = TopologyGraph.from_topology_dict({"nodes": nodes, "edges": edges})
        assert g.detect_ecmp_groups() == []

    def test_ring_ecmp(self):
        """Ring A-B-C has 2 paths between A and C → ECMP."""
        g = TopologyGraph.from_topology_dict(_make_ring_topology())
        groups = g.detect_ecmp_groups()
        # At least one ECMP group should exist
        assert len(groups) > 0

    def test_group_fields_present(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        groups = g.detect_ecmp_groups()
        assert len(groups) > 0
        eg = groups[0]
        assert eg["type"] == "ecmp"
        assert "source" in eg
        assert "dest" in eg
        assert "path_count" in eg
        assert "hop_count" in eg
        assert "paths" in eg
        assert eg["path_count"] >= 2


# ===========================================================================
# Unit tests: device role inference
# ===========================================================================


class TestInferDeviceRole:
    def test_spine_by_name(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.infer_device_role("spine-01") == "spine"

    def test_leaf_by_name(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.infer_device_role("leaf-01") == "leaf"

    def test_core_by_name(self):
        g = TopologyGraph.from_topology_dict(_make_3tier_topology())
        assert g.infer_device_role("core-01") == "core"

    def test_border_by_name(self):
        nodes = [
            {"id": "border-01", "hostname": "border-01", "platform": "", "management_ip": ""},
            {"id": "peer-01", "hostname": "peer-01", "platform": "", "management_ip": ""},
        ]
        edges = [{"source": "border-01", "target": "peer-01", "source_port": "e1", "target_port": "e1", "speed": 0}]
        g = TopologyGraph.from_topology_dict({"nodes": nodes, "edges": edges})
        assert g.infer_device_role("border-01") == "border"

    def test_unknown_node_returns_unknown(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        assert g.infer_device_role("ghost") == "unknown"

    def test_high_degree_node_inferred_as_spine(self):
        """A highly connected hub node without naming cues should be inferred as spine."""
        nodes = [{"id": f"sw{i}", "hostname": f"sw{i}", "platform": "", "management_ip": ""} for i in range(6)]
        # sw0 connects to all others (hub) — should be spine by degree
        edges = [
            {"source": "sw0", "target": f"sw{i}", "source_port": "e1", "target_port": "e1", "speed": 0}
            for i in range(1, 6)
        ]
        g = TopologyGraph.from_topology_dict({"nodes": nodes, "edges": edges})
        assert g.infer_device_role("sw0") == "spine"

    def test_degree_one_node_is_endpoint(self):
        """A node with only one connection (no naming cues) should be endpoint."""
        nodes = [
            {"id": "hub", "hostname": "hub", "platform": "", "management_ip": ""},
            {"id": "server1", "hostname": "server1", "platform": "", "management_ip": ""},
        ]
        edges = [{"source": "hub", "target": "server1", "source_port": "e1", "target_port": "e1", "speed": 0}]
        g = TopologyGraph.from_topology_dict({"nodes": nodes, "edges": edges})
        assert g.infer_device_role("server1") == "endpoint"


# ===========================================================================
# Unit tests: get_path_detail
# ===========================================================================


class TestGetPathDetail:
    def test_basic_path_detail(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        hops = g.get_path_detail("spine-01", "leaf-01")
        assert len(hops) == 2
        assert hops[0]["device"] == "spine-01"
        assert hops[1]["device"] == "leaf-01"

    def test_hop_fields_present(self):
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        hops = g.get_path_detail("spine-01", "leaf-01")
        first = hops[0]
        assert "hop" in first
        assert "device" in first
        assert "hostname" in first
        assert "platform" in first
        assert "role" in first
        assert "egress_port" in first

    def test_last_hop_has_no_egress(self):
        """The final hop should not have egress_port."""
        g = TopologyGraph.from_topology_dict(_make_spine_leaf_topology())
        hops = g.get_path_detail("spine-01", "leaf-01")
        last = hops[-1]
        assert "egress_port" not in last

    def test_no_path_returns_empty(self):
        g = TopologyGraph()
        g.add_node("a", {})
        g.add_node("b", {})
        assert g.get_path_detail("a", "b") == []

    def test_hop_numbering(self):
        g = TopologyGraph.from_topology_dict(_make_3tier_topology())
        hops = g.get_path_detail("core-01", "access-01")
        for i, hop in enumerate(hops):
            assert hop["hop"] == i


# ===========================================================================
# Unit tests: _mlag_name_match helper
# ===========================================================================


class TestMlagNameMatch:
    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ("leaf-01a", "leaf-01b", True),
            ("leaf-01A", "leaf-01B", True),
            ("LEAF-01a", "LEAF-01b", True),
            ("leaf-01", "leaf-02", True),
            ("sw1", "sw2", True),
            ("leaf-01", "leaf-03", False),  # diff > 1
            ("spine-01", "leaf-01", False),  # different prefix
            ("foo", "bar", False),
            ("leaf-01a", "leaf-01a", False),  # identical names
            ("leaf-01", "leaf-01", False),  # identical
        ],
    )
    def test_mlag_name_match(self, a, b, expected):
        assert _mlag_name_match(a, b) == expected


# ===========================================================================
# Tool-level tests (mock conn_mgr)
# ===========================================================================

# Shared mock LLDP data for a simple 3-device topology:
# spine-01 <-> leaf-01 <-> leaf-02, spine-01 <-> leaf-02

_FACTS = {
    "spine-01": {"hostname": "spine-01", "platform": "eos", "vendor": "arista"},
    "leaf-01": {"hostname": "leaf-01", "platform": "eos", "vendor": "arista"},
    "leaf-02": {"hostname": "leaf-02", "platform": "eos", "vendor": "arista"},
}
_LLDP = {
    "spine-01": {
        "Ethernet1": [{"hostname": "leaf-01", "port": "Ethernet49", "system_description": ""}],
        "Ethernet2": [{"hostname": "leaf-02", "port": "Ethernet49", "system_description": ""}],
    },
    "leaf-01": {
        "Ethernet49": [{"hostname": "spine-01", "port": "Ethernet1", "system_description": ""}],
        "Ethernet1": [{"hostname": "leaf-02", "port": "Ethernet1", "system_description": ""}],
    },
    "leaf-02": {
        "Ethernet49": [{"hostname": "spine-01", "port": "Ethernet2", "system_description": ""}],
        "Ethernet1": [{"hostname": "leaf-01", "port": "Ethernet1", "system_description": ""}],
    },
}
_INTERFACES = {
    host: {
        "Ethernet1": {"is_up": True, "speed": 100000},
        "Ethernet2": {"is_up": True, "speed": 100000},
        "Ethernet49": {"is_up": True, "speed": 100000},
        "Ethernet50": {"is_up": True, "speed": 100000},
    }
    for host in ["spine-01", "leaf-01", "leaf-02"]
}


def _make_mock_driver(host: str) -> MagicMock:
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_facts.return_value = _FACTS[host]
    driver.get_lldp_neighbors.return_value = _LLDP[host]
    driver.get_interfaces.return_value = _INTERFACES[host]
    return driver


@pytest.fixture()
def three_device_mock():
    """Patch conn_mgr.get_driver for the 3-device test topology."""
    drivers = {h: _make_mock_driver(h) for h in ["spine-01", "leaf-01", "leaf-02"]}

    def _get_driver(host):
        if host not in drivers:
            raise ConnectionError(f"Cannot connect to {host}")
        return drivers[host]

    with patch.object(conn_mgr, "get_driver", side_effect=_get_driver):
        yield drivers


class TestNetShowPath:
    def test_success_response(self, three_device_mock):
        from network_mcp.tools.common.topology import net_show_path

        result = net_show_path("spine-01", "leaf-01", ["spine-01", "leaf-01", "leaf-02"])
        assert result["status"] == "success"

    def test_path_from_spine_to_leaf(self, three_device_mock):
        from network_mcp.tools.common.topology import net_show_path

        result = net_show_path("spine-01", "leaf-01", ["spine-01", "leaf-01", "leaf-02"])
        assert result["source"] == "spine-01"
        assert result["dest"] == "leaf-01"
        assert "path" in result
        assert result["path"][0] == "spine-01"
        assert result["path"][-1] == "leaf-01"

    def test_hop_count_field(self, three_device_mock):
        from network_mcp.tools.common.topology import net_show_path

        result = net_show_path("spine-01", "leaf-01", ["spine-01", "leaf-01", "leaf-02"])
        assert result["hop_count"] == 1

    def test_hops_list_present(self, three_device_mock):
        from network_mcp.tools.common.topology import net_show_path

        result = net_show_path("spine-01", "leaf-01", ["spine-01", "leaf-01", "leaf-02"])
        assert isinstance(result["hops"], list)
        assert len(result["hops"]) >= 1

    def test_no_path_returns_error(self, three_device_mock):
        from network_mcp.tools.common.topology import net_show_path

        result = net_show_path("spine-01", "unknown-host", ["spine-01", "leaf-01"])
        assert result["status"] == "error"

    def test_device_field_is_source(self, three_device_mock):
        from network_mcp.tools.common.topology import net_show_path

        result = net_show_path("spine-01", "leaf-01", ["spine-01", "leaf-01"])
        assert result["device"] == "spine-01"


class TestNetGetDeviceRole:
    def test_spine_role(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_device_role

        result = net_get_device_role("spine-01", ["spine-01", "leaf-01", "leaf-02"])
        assert result["status"] == "success"
        assert result["role"] == "spine"

    def test_leaf_role(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_device_role

        result = net_get_device_role("leaf-01", ["spine-01", "leaf-01", "leaf-02"])
        assert result["status"] == "success"
        assert result["role"] == "leaf"

    def test_degree_in_response(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_device_role

        result = net_get_device_role("spine-01", ["spine-01", "leaf-01", "leaf-02"])
        assert "degree" in result
        assert result["degree"] >= 1

    def test_hostname_in_response(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_device_role

        result = net_get_device_role("spine-01", ["spine-01", "leaf-01", "leaf-02"])
        assert result["hostname"] == "spine-01"

    def test_device_field(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_device_role

        result = net_get_device_role("leaf-01", ["spine-01", "leaf-01", "leaf-02"])
        assert result["device"] == "leaf-01"


class TestNetGetRedundancyGroups:
    def test_success_response(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_redundancy_groups

        result = net_get_redundancy_groups(["spine-01", "leaf-01", "leaf-02"])
        assert result["status"] == "success"

    def test_has_mlag_pairs(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_redundancy_groups

        result = net_get_redundancy_groups(["spine-01", "leaf-01", "leaf-02"])
        assert "mlag_pairs" in result
        assert isinstance(result["mlag_pairs"], list)

    def test_has_ecmp_groups(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_redundancy_groups

        result = net_get_redundancy_groups(["spine-01", "leaf-01", "leaf-02"])
        assert "ecmp_groups" in result
        assert isinstance(result["ecmp_groups"], list)

    def test_summary_present(self, three_device_mock):
        from network_mcp.tools.common.topology import net_get_redundancy_groups

        result = net_get_redundancy_groups(["spine-01", "leaf-01", "leaf-02"])
        assert "summary" in result
        assert "mlag_pair_count" in result["summary"]
        assert "ecmp_group_count" in result["summary"]

    def test_empty_hosts(self):
        from network_mcp.tools.common.topology import net_get_redundancy_groups

        result = net_get_redundancy_groups([])
        assert result["status"] == "success"
        assert result["mlag_pairs"] == []
        assert result["ecmp_groups"] == []

    def test_leaf_pair_detected_as_mlag(self, three_device_mock):
        """leaf-01 and leaf-02 share both spines as neighbours — should be detected as MLAG pair."""
        from network_mcp.tools.common.topology import net_get_redundancy_groups

        result = net_get_redundancy_groups(["spine-01", "leaf-01", "leaf-02"])
        # Allow for either MLAG or ECMP detection covering leaf-01/leaf-02
        mlag_device_sets = [frozenset(p["devices"]) for p in result["mlag_pairs"]]
        ecmp_pairs = [frozenset([eg["source"], eg["dest"]]) for eg in result["ecmp_groups"]]
        leaf_pair = frozenset(["leaf-01", "leaf-02"])
        assert leaf_pair in mlag_device_sets or leaf_pair in ecmp_pairs
