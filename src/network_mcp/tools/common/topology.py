"""Vendor-agnostic topology path and redundancy analysis tools.

Builds a topology graph from LLDP data (reusing net_build_topology_from_lldp
logic) and exposes MCP tools for path computation, device role inference, and
redundancy group detection.
"""

from __future__ import annotations

import logging

from network_mcp.helpers import READ_ONLY
from network_mcp.server import conn_mgr, mcp  # noqa: F401 (conn_mgr imported for mcp side-effects)
from network_mcp.tools.common.switching import net_build_topology_from_lldp
from network_mcp.topology import TopologyGraph

logger = logging.getLogger("network-mcp")


def _build_graph(hosts: list[str]) -> tuple[TopologyGraph | None, dict | None]:
    """Build a TopologyGraph from LLDP data across *hosts*.

    Returns ``(graph, None)`` on success or ``(None, error_dict)`` on failure.
    """
    result = net_build_topology_from_lldp(hosts)
    if result.get("status") != "success":
        return None, {"status": "error", "error": result.get("error", "Failed to build topology")}
    return TopologyGraph.from_topology_dict(result["topology"]), None


@mcp.tool(annotations=READ_ONLY)
def net_show_path(source_host: str, dest_host: str, hosts: list[str]) -> dict:
    """Show the network path between two devices using topology built from LLDP data.

    Queries LLDP neighbors from all *hosts*, assembles a topology graph, then
    computes the shortest (fewest-hop) path from *source_host* to *dest_host*.
    Each hop includes the device hostname, platform, inferred role, egress/ingress
    ports, and link speed.

    Args:
        source_host: Source device hostname or IP (must appear in *hosts*).
        dest_host: Destination device hostname or IP (must appear in *hosts*).
        hosts: All devices to query when building the topology graph.
               Should include source_host and dest_host plus any intermediate
               devices that form the path.
    """
    graph, err = _build_graph(hosts)
    if err:
        return {"status": "error", "device": source_host, **err}

    path_nodes = graph.shortest_path(source_host, dest_host)
    if path_nodes is None:
        return {
            "status": "error",
            "device": source_host,
            "error": f"No path found from {source_host!r} to {dest_host!r}. "
            "Ensure both devices are reachable and included in hosts.",
        }

    hops = graph.get_path_detail(source_host, dest_host)
    return {
        "status": "success",
        "device": source_host,
        "source": source_host,
        "dest": dest_host,
        "hop_count": len(path_nodes) - 1,
        "path": path_nodes,
        "hops": hops,
    }


@mcp.tool(annotations=READ_ONLY)
def net_get_device_role(host: str, hosts: list[str]) -> dict:
    """Infer the network role of a device from topology analysis.

    Builds a topology graph from LLDP data and infers the role of *host*
    using naming patterns (spine/leaf/border/core in hostname) and degree
    heuristics (connection count relative to the graph average).

    Possible roles: ``spine``, ``leaf``, ``border``, ``core``, ``endpoint``,
    ``unknown``.

    Args:
        host: Device hostname or IP whose role to infer.
        hosts: All devices to query when building the topology graph.
               Should include *host* and its neighbours for accurate results.
    """
    graph, err = _build_graph(hosts)
    if err:
        return {"status": "error", "device": host, **err}

    role = graph.infer_device_role(host)
    degree = len(graph._adj.get(host, []))
    hostname = graph.nodes.get(host, {}).get("hostname", host)

    return {
        "status": "success",
        "device": host,
        "hostname": hostname,
        "role": role,
        "degree": degree,
    }


@mcp.tool(annotations=READ_ONLY)
def net_get_redundancy_groups(hosts: list[str]) -> dict:
    """Detect redundancy groups (MLAG pairs and ECMP path groups) in the topology.

    Builds a topology graph from LLDP data across *hosts* and returns:
    - **MLAG pairs**: Device pairs detected as MLAG/vPC peers via naming patterns
      (e.g. ``leaf-01a``/``leaf-01b``) or high neighbour overlap (≥ 60 % Jaccard).
    - **ECMP groups**: Source/destination pairs with two or more equal-hop-count
      simple paths, indicating load-balanced redundant connectivity.

    Args:
        hosts: All devices to query when building the topology graph.
    """
    if not hosts:
        return {
            "status": "success",
            "device": "",
            "mlag_pairs": [],
            "ecmp_groups": [],
            "summary": {"mlag_pair_count": 0, "ecmp_group_count": 0},
        }

    graph, err = _build_graph(hosts)
    if err:
        return {"status": "error", "device": hosts[0], **err}

    mlag_pairs = graph.detect_mlag_pairs()
    ecmp_groups = graph.detect_ecmp_groups()

    return {
        "status": "success",
        "device": hosts[0],
        "mlag_pairs": mlag_pairs,
        "ecmp_groups": ecmp_groups,
        "summary": {
            "mlag_pair_count": len(mlag_pairs),
            "ecmp_group_count": len(ecmp_groups),
        },
    }
