"""Network topology model with path computation and redundancy analysis.

Provides TopologyGraph, a pure-Python (stdlib-only) graph built from LLDP data
that supports:
- Shortest-path computation (BFS unweighted, Dijkstra by bandwidth)
- All-simple-paths enumeration for ECMP detection
- MLAG peer detection via naming patterns and neighbor-overlap
- Device role inference (spine/leaf/border/core/endpoint) via degree + naming heuristics
"""

from __future__ import annotations

import re
from collections import deque
from heapq import heappop, heappush

# ---------------------------------------------------------------------------
# Name-pattern helpers
# ---------------------------------------------------------------------------

_PAT_AB = re.compile(r"^(.*\d)([ab])$", re.IGNORECASE)
_PAT_NUM = re.compile(r"^(.*?)(\d+)$")

_ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bspine\b|[-_]sp[-_]?\d|[-_]sp\d|\bsp\d", re.IGNORECASE), "spine"),
    (re.compile(r"\bborder\b|\bedge\b", re.IGNORECASE), "border"),
    (re.compile(r"\bcore\b", re.IGNORECASE), "core"),
    (re.compile(r"\bleaf\b|\btor\b|\baccess\b", re.IGNORECASE), "leaf"),
]


def _mlag_name_match(name_a: str, name_b: str) -> bool:
    """Return True if the two hostnames suggest an MLAG peer relationship.

    Recognises two common naming conventions:
    - ``leaf-01a`` / ``leaf-01b`` — identical numeric prefix, letter suffix differs
    - ``leaf-01`` / ``leaf-02`` — same text prefix, sequential numeric suffix (diff == 1)
    """
    m_a = _PAT_AB.match(name_a)
    m_b = _PAT_AB.match(name_b)
    if m_a and m_b and m_a.group(1).lower() == m_b.group(1).lower():
        return True

    n_a = _PAT_NUM.match(name_a)
    n_b = _PAT_NUM.match(name_b)
    if n_a and n_b and n_a.group(1).lower() == n_b.group(1).lower():
        if abs(int(n_a.group(2)) - int(n_b.group(2))) == 1:
            return True

    return False


def _role_from_name(hostname: str) -> str | None:
    """Return a role string if the hostname matches a known naming pattern, else None."""
    for pat, role in _ROLE_PATTERNS:
        if pat.search(hostname):
            return role
    return None


# ---------------------------------------------------------------------------
# TopologyGraph
# ---------------------------------------------------------------------------


class TopologyGraph:
    """In-memory undirected graph of network topology built from LLDP data.

    Nodes are indexed by their ``id`` field (typically the management address or
    hostname used to query the device).  Edges carry the raw edge dict produced
    by ``net_build_topology_from_lldp`` (source, target, source_port, target_port,
    speed).

    The adjacency list ``_adj`` maps each node_id to a list of
    ``(neighbour_id, edge_dict)`` tuples.  Because edges are undirected, each
    edge appears twice in ``_adj``.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self._adj: dict[str, list[tuple[str, dict]]] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, attrs: dict) -> None:
        """Add a node, ignoring duplicates (first write wins)."""
        if node_id not in self.nodes:
            self.nodes[node_id] = attrs
        self._adj.setdefault(node_id, [])

    def add_edge(self, edge: dict) -> None:
        """Add an undirected edge.  Both endpoints are auto-registered as nodes."""
        src = edge["source"]
        tgt = edge["target"]
        self._adj.setdefault(src, []).append((tgt, edge))
        self._adj.setdefault(tgt, []).append((src, edge))

    @classmethod
    def from_topology_dict(cls, topology: dict) -> TopologyGraph:
        """Build from a topology dict as returned by ``net_build_topology_from_lldp``.

        Args:
            topology: Dict with ``nodes`` (list of node dicts) and ``edges``
                      (list of edge dicts).

        Returns:
            Populated TopologyGraph instance.
        """
        g = cls()
        for node in topology.get("nodes", []):
            g.add_node(node["id"], node)
        for edge in topology.get("edges", []):
            g.add_edge(edge)
        return g

    # ------------------------------------------------------------------
    # Path computation
    # ------------------------------------------------------------------

    def shortest_path(self, source: str, dest: str) -> list[str] | None:
        """BFS shortest path (minimum hops).

        Returns the node-id list ``[source, ..., dest]``, or ``None`` if
        source/dest are unknown or no path exists.
        """
        if source not in self._adj or dest not in self._adj:
            return None
        if source == dest:
            return [source]

        visited: set[str] = {source}
        queue: deque[list[str]] = deque([[source]])

        while queue:
            path = queue.popleft()
            node = path[-1]
            for neighbour, _edge in self._adj.get(node, []):
                if neighbour == dest:
                    return path + [neighbour]
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(path + [neighbour])

        return None

    def shortest_path_weighted(self, source: str, dest: str) -> list[str] | None:
        """Dijkstra shortest path using inverse link speed as cost.

        Higher speed links are preferred.  Falls back to hop count when
        speed information is missing.

        Returns the node-id list ``[source, ..., dest]``, or ``None`` if no
        path exists.
        """
        if source not in self._adj or dest not in self._adj:
            return None
        if source == dest:
            return [source]

        INF = float("inf")
        dist: dict[str, float] = {source: 0.0}
        prev: dict[str, str | None] = {source: None}
        heap: list[tuple[float, str]] = [(0.0, source)]

        while heap:
            cost, node = heappop(heap)
            if node == dest:
                break
            if cost > dist.get(node, INF):
                continue
            for neighbour, edge in self._adj.get(node, []):
                speed = edge.get("speed", 0) or 1  # avoid division by zero
                edge_cost = 1.0 / speed
                new_cost = cost + edge_cost
                if new_cost < dist.get(neighbour, INF):
                    dist[neighbour] = new_cost
                    prev[neighbour] = node
                    heappush(heap, (new_cost, neighbour))

        if dest not in prev:
            return None

        path: list[str] = []
        cur: str | None = dest
        while cur is not None:
            path.append(cur)
            cur = prev.get(cur)
        path.reverse()
        return path if path and path[0] == source else None

    def all_simple_paths(self, source: str, dest: str, max_paths: int = 16) -> list[list[str]]:
        """Enumerate simple (no repeated nodes) paths from *source* to *dest*.

        Uses an iterative DFS with an explicit visited set per path.
        Stops after *max_paths* results to bound runtime on dense graphs.

        Returns a (possibly empty) list of node-id lists.
        """
        if source not in self._adj or dest not in self._adj:
            return []
        if source == dest:
            return [[source]]

        results: list[list[str]] = []
        # Stack items: (current_node, path_so_far, visited_set)
        stack: list[tuple[str, list[str], set[str]]] = [(source, [source], {source})]

        while stack and len(results) < max_paths:
            node, path, visited = stack.pop()
            for neighbour, _edge in self._adj.get(node, []):
                if neighbour == dest:
                    results.append(path + [neighbour])
                elif neighbour not in visited:
                    stack.append((neighbour, path + [neighbour], visited | {neighbour}))

        return results

    # ------------------------------------------------------------------
    # Hop detail
    # ------------------------------------------------------------------

    def get_path_detail(self, source: str, dest: str) -> list[dict]:
        """Return per-hop detail for the BFS shortest path from *source* to *dest*.

        Each hop dict contains: hop index, device id, hostname, platform, inferred
        role, egress port (leaving this hop), ingress port at the next hop, and
        link speed in Mbps.
        """
        path = self.shortest_path(source, dest)
        if path is None:
            return []

        hops: list[dict] = []
        for i, node_id in enumerate(path):
            node_attrs = self.nodes.get(node_id, {})
            hop: dict = {
                "hop": i,
                "device": node_id,
                "hostname": node_attrs.get("hostname", node_id),
                "platform": node_attrs.get("platform", ""),
                "role": self.infer_device_role(node_id),
            }
            if i < len(path) - 1:
                next_id = path[i + 1]
                for neighbour, edge in self._adj.get(node_id, []):
                    if neighbour == next_id:
                        if edge.get("source") == node_id:
                            hop["egress_port"] = edge.get("source_port", "")
                            hop["ingress_port_next"] = edge.get("target_port", "")
                        else:
                            hop["egress_port"] = edge.get("target_port", "")
                            hop["ingress_port_next"] = edge.get("source_port", "")
                        hop["link_speed_mbps"] = edge.get("speed", 0)
                        break
            hops.append(hop)

        return hops

    # ------------------------------------------------------------------
    # Redundancy detection
    # ------------------------------------------------------------------

    def detect_mlag_pairs(self) -> list[dict]:
        """Detect MLAG peer pairs using naming patterns and neighbor-overlap heuristics.

        Two devices are considered an MLAG pair when they satisfy at least one of:
        - Their hostnames follow a known MLAG naming convention (e.g. ``leaf-01a``/``leaf-01b``
          or ``leaf-01``/``leaf-02``).
        - They share ≥ 60 % of the same neighbours (Jaccard similarity on neighbour sets,
          excluding each other).

        Returns a list of dicts, each with keys: ``type``, ``devices``,
        ``neighbor_overlap``, ``naming_match``.
        """
        node_ids = list(self._adj.keys())
        pairs: list[dict] = []
        seen: set[frozenset] = set()

        for i, a in enumerate(node_ids):
            for b in node_ids[i + 1 :]:
                key: frozenset = frozenset([a, b])
                if key in seen:
                    continue

                name_a = self.nodes.get(a, {}).get("hostname", a)
                name_b = self.nodes.get(b, {}).get("hostname", b)
                naming_match = _mlag_name_match(name_a, name_b)

                neighbors_a = {n for n, _ in self._adj.get(a, []) if n not in (a, b)}
                neighbors_b = {n for n, _ in self._adj.get(b, []) if n not in (a, b)}
                union = neighbors_a | neighbors_b
                overlap = len(neighbors_a & neighbors_b) / len(union) if union else 0.0

                if naming_match or overlap >= 0.6:
                    seen.add(key)
                    pairs.append(
                        {
                            "type": "mlag_pair",
                            "devices": sorted([a, b]),
                            "neighbor_overlap": round(overlap, 2),
                            "naming_match": naming_match,
                        }
                    )

        return pairs

    def detect_ecmp_groups(self) -> list[dict]:
        """Find ECMP groups: node pairs with ≥ 2 equal-hop-count simple paths.

        Returns a list of dicts with keys: ``type``, ``source``, ``dest``,
        ``path_count``, ``hop_count``, ``paths``.
        """
        node_ids = list(self._adj.keys())
        groups: list[dict] = []
        seen: set[frozenset] = set()

        for i, src in enumerate(node_ids):
            for dst in node_ids[i + 1 :]:
                key: frozenset = frozenset([src, dst])
                if key in seen:
                    continue

                paths = self.all_simple_paths(src, dst, max_paths=8)
                if len(paths) < 2:
                    continue

                min_hops = min(len(p) for p in paths)
                ecmp = [p for p in paths if len(p) == min_hops]
                if len(ecmp) >= 2:
                    seen.add(key)
                    groups.append(
                        {
                            "type": "ecmp",
                            "source": src,
                            "dest": dst,
                            "path_count": len(ecmp),
                            "hop_count": min_hops - 1,
                            "paths": ecmp,
                        }
                    )

        return groups

    # ------------------------------------------------------------------
    # Device role inference
    # ------------------------------------------------------------------

    def infer_device_role(self, host_id: str) -> str:
        """Infer device role from naming patterns and degree heuristics.

        Priority order:
        1. Hostname matches a known pattern → spine / border / core / leaf
        2. Degree ≥ 80 % of the maximum degree AND above average → spine
        3. Degree == 1 → endpoint
        4. Fallback → leaf

        Returns one of: ``"spine"``, ``"leaf"``, ``"border"``, ``"core"``,
        ``"endpoint"``, ``"unknown"``.
        """
        if host_id not in self._adj:
            return "unknown"

        hostname = self.nodes.get(host_id, {}).get("hostname", host_id)
        name_role = _role_from_name(hostname)
        if name_role is not None:
            return name_role

        degrees = [len(v) for v in self._adj.values()]
        if not degrees:
            return "unknown"

        degree = len(self._adj[host_id])
        avg_degree = sum(degrees) / len(degrees)
        max_degree = max(degrees)

        if max_degree > 0 and degree >= max_degree * 0.8 and degree > avg_degree * 1.5:
            return "spine"
        if degree == 1:
            return "endpoint"
        return "leaf"
