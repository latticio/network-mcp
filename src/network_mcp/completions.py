"""Argument completions for MCP completion/complete handler.

Provides auto-complete for key arguments: host, interface, vlan_id, neighbor_ip,
category, and checkpoint. Device-dependent completions (interface, vlan_id, etc.)
are cached for 60s to stay fast (<500ms).
"""

import logging
import threading
import time

from mcp.types import Completion, PromptReference, ResourceTemplateReference

from network_mcp.helpers import run_show_command
from network_mcp.server import conn_mgr, mcp
from network_mcp.tools.meta import get_category_names

logger = logging.getLogger("network-mcp")

# --- Completion Cache ---

_cache: dict[str, tuple[list[str], float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 60  # seconds


def _get_cached(key: str) -> list[str] | None:
    """Get cached completion values if still fresh."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.monotonic() - entry[1]) < _CACHE_TTL:
            return entry[0]
    return None


def _set_cached(key: str, values: list[str]) -> None:
    """Cache completion values."""
    with _cache_lock:
        _cache[key] = (values, time.monotonic())


# --- Completion Providers ---


def _complete_host(partial: str) -> list[str]:
    """Complete host from inventory device names."""
    devices = conn_mgr.list_devices()
    if not partial:
        return devices[:100]
    return [d for d in devices if d.lower().startswith(partial.lower())][:100]


def _complete_interface(host: str, partial: str) -> list[str]:
    """Complete interface names from device (cached 60s)."""
    cache_key = f"interfaces:{host}"
    cached = _get_cached(cache_key)
    if cached is None:
        try:
            result = run_show_command(conn_mgr, host, ["show interfaces status"])
            if result["status"] == "success":
                cached = sorted(result["data"][0].get("interfaceStatuses", {}).keys())
                _set_cached(cache_key, cached)
            else:
                cached = []
        except Exception:
            cached = []

    if not partial:
        return cached[:100]
    return [i for i in cached if i.lower().startswith(partial.lower())][:100]


def _complete_vlan_id(host: str, partial: str) -> list[str]:
    """Complete VLAN IDs from device (cached 60s)."""
    cache_key = f"vlans:{host}"
    cached = _get_cached(cache_key)
    if cached is None:
        try:
            result = run_show_command(conn_mgr, host, ["show vlan"])
            if result["status"] == "success":
                cached = sorted(result["data"][0].get("vlans", {}).keys(), key=lambda x: int(x) if x.isdigit() else 0)
                _set_cached(cache_key, cached)
            else:
                cached = []
        except Exception:
            cached = []

    if not partial:
        return cached[:100]
    return [v for v in cached if v.startswith(partial)][:100]


def _complete_neighbor_ip(host: str, partial: str) -> list[str]:
    """Complete BGP neighbor IPs from device (cached 60s)."""
    cache_key = f"bgp_peers:{host}"
    cached = _get_cached(cache_key)
    if cached is None:
        try:
            result = run_show_command(conn_mgr, host, ["show ip bgp summary"])
            if result["status"] == "success":
                peers = result["data"][0].get("vrfs", {}).get("default", {}).get("peers", {})
                cached = sorted(peers.keys())
                _set_cached(cache_key, cached)
            else:
                cached = []
        except Exception:
            cached = []

    if not partial:
        return cached[:100]
    return [p for p in cached if p.startswith(partial)][:100]


def _complete_category(partial: str) -> list[str]:
    """Complete category names from meta-tools registry."""
    categories = get_category_names()
    if not partial:
        return categories
    return [c for c in categories if c.lower().startswith(partial.lower())]


def _complete_checkpoint(host: str, partial: str) -> list[str]:
    """Complete checkpoint names from device (cached 60s)."""
    cache_key = f"checkpoints:{host}"
    cached = _get_cached(cache_key)
    if cached is None:
        try:
            result = run_show_command(conn_mgr, host, ["show config checkpoints"], encoding="text")
            if result["status"] == "success":
                output = result["data"][0].get("output", "")
                # Parse checkpoint names from text output
                lines = output.strip().splitlines()
                cached = [line.strip() for line in lines if line.strip() and not line.startswith("---")]
                _set_cached(cache_key, cached)
            else:
                cached = []
        except Exception:
            cached = []

    if not partial:
        return cached[:100]
    return [c for c in cached if c.lower().startswith(partial.lower())][:100]


# --- MCP Completion Handler ---


@mcp.completion()
async def handle_completion(
    ref: PromptReference | ResourceTemplateReference,
    argument,
    context,
) -> Completion | None:
    """Handle completion/complete requests for tool and resource arguments.

    Provides completions for: host, interface, vlan_id, neighbor_ip, category, checkpoint.
    Device-dependent completions run in a thread to avoid blocking the async event loop.
    """
    import asyncio

    arg_name = argument.name
    partial = argument.value or ""

    # Resolve host from context if available (for device-dependent completions)
    resolved_host = None
    if context and context.arguments:
        resolved_host = context.arguments.get("host")

    # Resource template completions
    if isinstance(ref, ResourceTemplateReference):
        if arg_name == "host":
            values = _complete_host(partial)
            return Completion(values=values, total=len(values), hasMore=False)

        if arg_name == "interface" and resolved_host:
            values = await asyncio.to_thread(_complete_interface, resolved_host, partial)
            return Completion(values=values, total=len(values), hasMore=False)

        if arg_name == "section":
            # Common config sections (EOS naming convention)
            sections = [
                "router bgp",
                "interface",
                "vlan",
                "ip access-list",
                "route-map",
                "ip prefix-list",
                "management api",
                "spanning-tree",
                "mlag",
                "ntp",
                "logging",
                "aaa",
                "radius-server",
                "tacacs-server",
            ]
            if partial:
                sections = [s for s in sections if s.lower().startswith(partial.lower())]
            return Completion(values=sections, total=len(sections), hasMore=False)

    # Tool argument completions (works for both prompt and resource refs)
    # Device-dependent completions use asyncio.to_thread to avoid blocking the event loop
    if arg_name == "host":
        values = _complete_host(partial)
        return Completion(values=values, total=len(values), hasMore=False)

    if arg_name == "interface" and resolved_host:
        values = await asyncio.to_thread(_complete_interface, resolved_host, partial)
        return Completion(values=values, total=len(values), hasMore=False)

    if arg_name == "vlan_id" and resolved_host:
        values = await asyncio.to_thread(_complete_vlan_id, resolved_host, partial)
        return Completion(values=values, total=len(values), hasMore=False)

    if arg_name == "neighbor_ip" and resolved_host:
        values = await asyncio.to_thread(_complete_neighbor_ip, resolved_host, partial)
        return Completion(values=values, total=len(values), hasMore=False)

    if arg_name == "category":
        values = _complete_category(partial)
        return Completion(values=values, total=len(values), hasMore=False)

    if arg_name == "checkpoint" and resolved_host:
        values = await asyncio.to_thread(_complete_checkpoint, resolved_host, partial)
        return Completion(values=values, total=len(values), hasMore=False)

    return None


def clear_completion_cache() -> None:
    """Clear the completion cache. Used in testing."""
    with _cache_lock:
        _cache.clear()
