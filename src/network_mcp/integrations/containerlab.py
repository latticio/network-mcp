"""Containerlab integration — topology discovery and inventory population.

Detects running Containerlab topologies via ``containerlab inspect``,
maps container nodes to network-mcp device inventory entries, and provides
a ``ContainerlabInventoryBackend`` that implements the same interface as
``YamlInventoryBackend``.

No extra Python dependencies required — uses stdlib ``subprocess`` and ``json``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from pydantic import SecretStr

from network_mcp.connection import DeviceCredentials
from network_mcp.helpers import READ_ONLY, WRITE_SAFE, check_read_only
from network_mcp.server import conn_mgr, mcp, settings

logger = logging.getLogger("network-mcp.containerlab")

# ---------------------------------------------------------------------------
# Platform detection — image/kind → network-mcp platform string
# ---------------------------------------------------------------------------

# Ordered list of (substring, platform) tuples; first match wins.
# Substrings are checked against the lowercased image name or node kind.
_PLATFORM_PATTERNS: list[tuple[str, str]] = [
    ("ceos", "eos"),
    ("veos", "eos"),
    ("arista", "eos"),
    ("crpd", "junos"),
    ("vjunos", "junos"),
    ("vr-junos", "junos"),
    ("junos", "junos"),
    ("vr-nxos", "nxos"),
    ("nxos", "nxos"),
    ("vr-csr", "iosxe"),
    ("csr", "iosxe"),
    ("iosxe", "iosxe"),
    ("ios-xe", "iosxe"),
    ("xrd", "iosxe"),
    ("cisco_xrd", "iosxe"),
]

_DEFAULT_PLATFORM = "eos"


def _detect_platform(image_or_kind: str) -> str:
    """Detect network-mcp platform from a container image name or Containerlab kind.

    Checks the input (lowercased) against known substrings and returns the
    corresponding platform string.  Falls back to 'eos' for unknown images.

    Args:
        image_or_kind: Container image tag (e.g. ``ceos:4.28.0F``) or
                       Containerlab node kind (e.g. ``crpd``).

    Returns:
        Platform string compatible with DRIVER_REGISTRY: 'eos', 'junos', 'nxos', or 'iosxe'.
    """
    lower = image_or_kind.lower()
    for keyword, platform in _PLATFORM_PATTERNS:
        if keyword in lower:
            return platform
    return _DEFAULT_PLATFORM


# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------


def _run_inspect(lab_name: str | None = None) -> list[dict[str, Any]]:
    """Run ``containerlab inspect`` and return a flat list of container dicts.

    Supports both output formats produced by different Containerlab versions:
    - Flat JSON array: ``[{...}, ...]``
    - Wrapped object: ``{"containers": [{...}, ...]}``
    - Per-lab keyed dict: ``{"labname": {"containers": [{...}, ...]}}``

    Args:
        lab_name: If given, inspect only this lab (``--name``); otherwise inspect
                  all running labs (``--all``).

    Returns:
        List of container dicts (may be empty).

    Raises:
        RuntimeError: If the ``containerlab`` binary is not found, the command
                      fails, or the output cannot be parsed as JSON.
    """
    cmd = ["containerlab", "inspect", "--format", "json"]
    if lab_name:
        cmd += ["--name", lab_name]
    else:
        cmd += ["--all"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)  # noqa: S603
    except FileNotFoundError as exc:
        raise RuntimeError("containerlab CLI not found. Install from https://containerlab.dev") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("containerlab inspect timed out after 30 seconds") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"containerlab inspect failed (exit {result.returncode}): {stderr}")

    if not result.stdout.strip():
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse containerlab inspect output as JSON: {exc}") from exc

    return _normalise_inspect_output(data)


def _normalise_inspect_output(data: Any) -> list[dict[str, Any]]:
    """Normalise the various containerlab inspect JSON formats to a flat container list."""
    if isinstance(data, list):
        # New-style flat array
        return [c for c in data if isinstance(c, dict)]

    if isinstance(data, dict):
        # Wrapped object: {"containers": [...]}
        if "containers" in data and isinstance(data["containers"], list):
            return [c for c in data["containers"] if isinstance(c, dict)]

        # Per-lab keyed: {"labname": {"containers": [...]}} or {"labname": [...]}
        containers: list[dict[str, Any]] = []
        for lab_key, lab_data in data.items():
            if isinstance(lab_data, dict) and "containers" in lab_data:
                for c in lab_data["containers"]:
                    if isinstance(c, dict):
                        c.setdefault("lab_name", lab_key)
                        containers.append(c)
            elif isinstance(lab_data, list):
                for c in lab_data:
                    if isinstance(c, dict):
                        c.setdefault("lab_name", lab_key)
                        containers.append(c)
        return containers

    return []


def _parse_node_name(container_name: str, lab_name: str) -> str:
    """Extract the short node name from a Containerlab container name.

    Containerlab names containers as ``clab-{lab_name}-{node_name}``.
    Returns the short node name when the prefix matches, or the full
    container name as a fallback.
    """
    prefix = f"clab-{lab_name}-"
    if container_name.startswith(prefix):
        return container_name[len(prefix):]
    return container_name


def _extract_ip(address: str) -> str:
    """Strip CIDR prefix from an IPv4/IPv6 address string (e.g. '172.20.0.2/24' → '172.20.0.2')."""
    return address.split("/")[0] if address else ""


# ---------------------------------------------------------------------------
# Inventory backend
# ---------------------------------------------------------------------------


class ContainerlabInventoryBackend:
    """Inventory backend populated from a running Containerlab topology.

    Calls ``containerlab inspect`` at construction time and builds
    ``DeviceCredentials`` for each *running* container node.

    Implements the same interface as ``YamlInventoryBackend`` so it can be
    passed directly to ``ConnectionManager.load_inventory_from_backend()``.

    Args:
        lab_name: Inspect only this lab; if ``None`` all running labs are
                  inspected.
        default_username: Login username applied to all discovered nodes.
        default_password: Login password applied to all discovered nodes.
        default_transport: Transport protocol ('https' or 'http').
        default_port: Management API port (default 443).
    """

    def __init__(
        self,
        lab_name: str | None = None,
        default_username: str = "admin",
        default_password: str = "admin",  # noqa: S107
        default_transport: str = "https",
        default_port: int = 443,
    ) -> None:
        self._devices: dict[str, DeviceCredentials] = {}
        self._lab_name = lab_name
        self._load(default_username, default_password, default_transport, default_port)

    def _load(self, username: str, password: str, transport: str, port: int) -> None:
        containers = _run_inspect(self._lab_name)
        seen_ips: dict[str, str] = {}

        for container in containers:
            state = container.get("state", "").lower()
            if state not in ("running", "up"):
                logger.debug("Skipping container '%s' (state=%s)", container.get("name", "?"), state)
                continue

            container_name: str = container.get("name", "")
            lab: str = container.get("lab_name", "")
            kind: str = container.get("kind", "")
            image: str = container.get("image", "")

            # Support both field names used by different clab versions
            raw_ip: str = container.get("ipv4_address") or container.get("mgmt_ipv4", "")
            mgmt_ip = _extract_ip(raw_ip)

            if not container_name or not mgmt_ip:
                logger.debug("Skipping container with missing name or IP: %s", container)
                continue

            # Prefer the short node name extracted from the container name
            node_name = _parse_node_name(container_name, lab) if lab else container_name

            # Detect platform: kind takes precedence over image
            platform = _detect_platform(kind) if kind else _detect_platform(image)

            if mgmt_ip in seen_ips:
                logger.warning(
                    "Duplicate management IP '%s': nodes '%s' and '%s' share the same address",
                    mgmt_ip,
                    seen_ips[mgmt_ip],
                    node_name,
                )
            else:
                seen_ips[mgmt_ip] = node_name

            tags = ["containerlab"]
            if lab:
                tags.append(f"lab:{lab}")

            self._devices[node_name] = DeviceCredentials(
                host=mgmt_ip,
                username=username,
                password=SecretStr(password),
                transport=transport,
                port=port,
                platform=platform,
                tags=tags,
            )

        logger.info(
            "ContainerlabInventoryBackend loaded %d devices from %s",
            len(self._devices),
            f"lab '{self._lab_name}'" if self._lab_name else "all labs",
        )

    def get_devices(self) -> dict[str, DeviceCredentials]:
        """Return all discovered devices keyed by node name."""
        return dict(self._devices)

    def get_device(self, name: str) -> DeviceCredentials | None:
        """Return credentials for a single node by name, or None if not found."""
        return self._devices.get(name)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def net_containerlab_discover() -> dict[str, Any]:
    """Discover running Containerlab topologies via the containerlab CLI.

    Runs ``containerlab inspect --all --format json`` to enumerate all running
    labs and their nodes.  Returns a summary of discovered labs with per-lab
    node counts and per-node platform/IP details.

    Returns a dict with keys:
    - ``status``: ``"success"`` or ``"error"``
    - ``lab_count``: Number of distinct labs found
    - ``total_nodes``: Total running nodes across all labs
    - ``labs``: Dict keyed by lab name; each value has ``node_count`` and ``nodes``
    """
    if settings.net_demo_mode:
        return {
            "status": "success",
            "lab_count": 1,
            "total_nodes": 2,
            "labs": {
                "demo-lab": {
                    "node_count": 2,
                    "nodes": [
                        {"name": "leaf1", "platform": "eos", "ip": "172.20.20.2", "kind": "ceos",
                         "image": "ceos:4.28.0F"},
                        {"name": "leaf2", "platform": "eos", "ip": "172.20.20.3", "kind": "ceos",
                         "image": "ceos:4.28.0F"},
                    ],
                }
            },
        }

    try:
        containers = _run_inspect()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    if not containers:
        return {"status": "success", "lab_count": 0, "total_nodes": 0, "labs": {}}

    labs: dict[str, dict[str, Any]] = {}
    running_total = 0

    for container in containers:
        state = container.get("state", "").lower()
        if state not in ("running", "up"):
            continue

        lab = container.get("lab_name", "<unknown>")
        container_name: str = container.get("name", "")
        kind: str = container.get("kind", "")
        image: str = container.get("image", "")
        raw_ip: str = container.get("ipv4_address") or container.get("mgmt_ipv4", "")
        mgmt_ip = _extract_ip(raw_ip)

        node_name = _parse_node_name(container_name, lab) if lab != "<unknown>" else container_name
        platform = _detect_platform(kind) if kind else _detect_platform(image)

        if lab not in labs:
            labs[lab] = {"node_count": 0, "nodes": []}

        labs[lab]["nodes"].append(
            {
                "name": node_name,
                "container_name": container_name,
                "platform": platform,
                "ip": mgmt_ip,
                "kind": kind,
                "image": image,
            }
        )
        labs[lab]["node_count"] += 1
        running_total += 1

    return {
        "status": "success",
        "lab_count": len(labs),
        "total_nodes": running_total,
        "labs": labs,
    }


@mcp.tool(annotations=READ_ONLY)
def net_containerlab_inventory(
    lab_name: str,
    username: str = "admin",
    password: str = "admin",  # noqa: S107
    transport: str = "https",
    port: int = 443,
) -> dict[str, Any]:
    """Import devices from a running Containerlab lab into the active inventory.

    Discovers all running nodes in the named lab via ``containerlab inspect``,
    maps each node to a ``DeviceCredentials`` entry (auto-detecting platform
    from the container image/kind), and loads them into the live
    ``ConnectionManager`` inventory.

    After calling this tool, all imported node names become valid ``host``
    arguments for every other network-mcp tool.

    Args:
        lab_name: Name of the Containerlab lab to import (e.g. ``"mylab"``).
        username: Login username for all nodes (default ``"admin"``).
        password: Login password for all nodes (default ``"admin"``).
        transport: Connection transport, ``"https"`` or ``"http"`` (default ``"https"``).
        port: Management API port (default ``443``).
    """
    if settings.net_demo_mode:
        return {
            "status": "success",
            "lab_name": lab_name,
            "imported_count": 2,
            "devices": [
                {"name": "leaf1", "host": "172.20.20.2", "platform": "eos"},
                {"name": "leaf2", "host": "172.20.20.3", "platform": "eos"},
            ],
        }

    try:
        backend = ContainerlabInventoryBackend(
            lab_name=lab_name,
            default_username=username,
            default_password=password,
            default_transport=transport,
            default_port=port,
        )
    except RuntimeError as exc:
        return {"status": "error", "lab_name": lab_name, "error": str(exc)}

    devices = backend.get_devices()
    if not devices:
        return {
            "status": "error",
            "lab_name": lab_name,
            "error": (
                f"No running nodes found in lab '{lab_name}'. "
                f"Verify the lab is running with: containerlab inspect --name {lab_name}"
            ),
        }

    conn_mgr.load_inventory_from_backend(backend)

    device_list = [
        {"name": name, "host": creds.host, "platform": creds.platform}
        for name, creds in sorted(devices.items())
    ]

    logger.info("Imported %d devices from Containerlab lab '%s' into inventory", len(devices), lab_name)

    return {
        "status": "success",
        "lab_name": lab_name,
        "imported_count": len(devices),
        "devices": device_list,
    }


# ---------------------------------------------------------------------------
# Lifecycle CLI helpers
# ---------------------------------------------------------------------------

_FEATURE_DISABLED_MSG = (
    "Containerlab lifecycle tools are disabled. Set NET_CONTAINERLAB_ENABLED=true to enable."
)


def _run_deploy(topology_file: str, *, reconfigure: bool = False) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run ``containerlab deploy -t <topology_file>`` and return the CompletedProcess.

    Args:
        topology_file: Path to the Containerlab topology YAML file.
        reconfigure: If True, pass ``--reconfigure`` to force re-creation of nodes.

    Raises:
        RuntimeError: If the containerlab binary is not found or the command times out.
    """
    cmd = ["containerlab", "deploy", "-t", topology_file]
    if reconfigure:
        cmd.append("--reconfigure")
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
    except FileNotFoundError as exc:
        raise RuntimeError("containerlab CLI not found. Install from https://containerlab.dev") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("containerlab deploy timed out after 300 seconds") from exc


def _run_destroy(lab_name: str, *, cleanup: bool = False) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run ``containerlab destroy --name <lab_name>`` and return the CompletedProcess.

    Args:
        lab_name: Name of the lab to destroy.
        cleanup: If True, pass ``--cleanup`` to remove container images and volumes.

    Raises:
        RuntimeError: If the containerlab binary is not found or the command times out.
    """
    cmd = ["containerlab", "destroy", "--name", lab_name]
    if cleanup:
        cmd.append("--cleanup")
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)  # noqa: S603
    except FileNotFoundError as exc:
        raise RuntimeError("containerlab CLI not found. Install from https://containerlab.dev") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("containerlab destroy timed out after 120 seconds") from exc


# ---------------------------------------------------------------------------
# Lifecycle MCP tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=WRITE_SAFE)
def net_containerlab_deploy(
    topology_file: str,
    reconfigure: bool = False,
) -> dict[str, Any]:
    """[WRITE] Deploy a Containerlab topology from a YAML topology file.

    Runs ``containerlab deploy -t <topology_file>`` to start all nodes defined
    in the topology.  Optionally passes ``--reconfigure`` to force re-creation
    of existing nodes.

    Requires ``NET_CONTAINERLAB_ENABLED=true`` and ``NET_READ_ONLY=false``.

    Args:
        topology_file: Path to the Containerlab topology YAML file (e.g. ``"./topologies/lab.yaml"``).
        reconfigure: If True, force re-creation of all nodes even if they already exist.
    """
    if not settings.net_containerlab_enabled:
        return {"status": "error", "error": _FEATURE_DISABLED_MSG}

    if settings.net_demo_mode:
        return {
            "status": "success",
            "topology_file": topology_file,
            "reconfigure": reconfigure,
            "output": "[demo] containerlab deploy completed successfully.",
        }

    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "topology_file": topology_file, "error": ro_err}

    try:
        result = _run_deploy(topology_file, reconfigure=reconfigure)
    except RuntimeError as exc:
        return {"status": "error", "topology_file": topology_file, "error": str(exc)}

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return {
            "status": "error",
            "topology_file": topology_file,
            "error": f"containerlab deploy failed (exit {result.returncode}): {stderr}",
        }

    logger.info("Containerlab deploy succeeded for topology '%s'", topology_file)
    return {
        "status": "success",
        "topology_file": topology_file,
        "reconfigure": reconfigure,
        "output": result.stdout.strip(),
    }


@mcp.tool(annotations=WRITE_SAFE)
def net_containerlab_destroy(
    lab_name: str,
    cleanup: bool = False,
) -> dict[str, Any]:
    """[WRITE] Tear down a running Containerlab topology.

    Runs ``containerlab destroy --name <lab_name>`` to stop and remove all
    containers belonging to the named lab.  Optionally passes ``--cleanup``
    to also remove associated container images and volumes.

    Requires ``NET_CONTAINERLAB_ENABLED=true`` and ``NET_READ_ONLY=false``.

    Args:
        lab_name: Name of the Containerlab lab to destroy (e.g. ``"mylab"``).
        cleanup: If True, also remove container images and volumes after destroying.
    """
    if not settings.net_containerlab_enabled:
        return {"status": "error", "error": _FEATURE_DISABLED_MSG}

    if settings.net_demo_mode:
        return {
            "status": "success",
            "lab_name": lab_name,
            "cleanup": cleanup,
            "output": f"[demo] containerlab destroy '{lab_name}' completed successfully.",
        }

    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "lab_name": lab_name, "error": ro_err}

    try:
        result = _run_destroy(lab_name, cleanup=cleanup)
    except RuntimeError as exc:
        return {"status": "error", "lab_name": lab_name, "error": str(exc)}

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return {
            "status": "error",
            "lab_name": lab_name,
            "error": f"containerlab destroy failed (exit {result.returncode}): {stderr}",
        }

    logger.info("Containerlab destroy succeeded for lab '%s'", lab_name)
    return {
        "status": "success",
        "lab_name": lab_name,
        "cleanup": cleanup,
        "output": result.stdout.strip(),
    }


@mcp.tool(annotations=READ_ONLY)
def net_containerlab_status() -> dict[str, Any]:
    """Show the current lifecycle status of all Containerlab labs.

    Runs ``containerlab inspect --all --format json`` and returns a summary of
    all known labs including nodes in any state (running, exited, etc.).

    Requires ``NET_CONTAINERLAB_ENABLED=true``.

    Returns a dict with keys:
    - ``status``: ``"success"`` or ``"error"``
    - ``lab_count``: Number of distinct labs found
    - ``total_nodes``: Total nodes across all labs (all states)
    - ``running_nodes``: Nodes in ``"running"`` or ``"up"`` state
    - ``labs``: Dict keyed by lab name; each value has ``node_count``, ``running``, and ``nodes``
    """
    if not settings.net_containerlab_enabled:
        return {"status": "error", "error": _FEATURE_DISABLED_MSG}

    if settings.net_demo_mode:
        return {
            "status": "success",
            "lab_count": 1,
            "total_nodes": 2,
            "running_nodes": 2,
            "labs": {
                "demo-lab": {
                    "node_count": 2,
                    "running": 2,
                    "nodes": [
                        {"name": "leaf1", "state": "running", "platform": "eos", "ip": "172.20.20.2"},
                        {"name": "leaf2", "state": "running", "platform": "eos", "ip": "172.20.20.3"},
                    ],
                }
            },
        }

    try:
        containers = _run_inspect()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    if not containers:
        return {"status": "success", "lab_count": 0, "total_nodes": 0, "running_nodes": 0, "labs": {}}

    labs: dict[str, dict[str, Any]] = {}
    total_running = 0

    for container in containers:
        lab = container.get("lab_name", "<unknown>")
        container_name: str = container.get("name", "")
        kind: str = container.get("kind", "")
        image: str = container.get("image", "")
        state: str = container.get("state", "unknown").lower()
        raw_ip: str = container.get("ipv4_address") or container.get("mgmt_ipv4", "")
        mgmt_ip = _extract_ip(raw_ip)

        node_name = _parse_node_name(container_name, lab) if lab != "<unknown>" else container_name
        platform = _detect_platform(kind) if kind else _detect_platform(image)
        is_running = state in ("running", "up")

        if lab not in labs:
            labs[lab] = {"node_count": 0, "running": 0, "nodes": []}

        labs[lab]["nodes"].append(
            {
                "name": node_name,
                "container_name": container_name,
                "state": state,
                "platform": platform,
                "ip": mgmt_ip,
                "kind": kind,
            }
        )
        labs[lab]["node_count"] += 1
        if is_running:
            labs[lab]["running"] += 1
            total_running += 1

    total_nodes = sum(lab_data["node_count"] for lab_data in labs.values())

    return {
        "status": "success",
        "lab_count": len(labs),
        "total_nodes": total_nodes,
        "running_nodes": total_running,
        "labs": labs,
    }
