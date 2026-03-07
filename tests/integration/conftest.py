"""Fixtures for integration tests against real containerlab cEOS devices.

These tests require:
  - A running containerlab topology (lab/setup.sh)
  - The generated inventory file (lab/devices.yaml)
  - pytest --run-integration flag

All tests are marked with @pytest.mark.integration and skipped by default.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

logger = logging.getLogger("integration-tests")

# ---------------------------------------------------------------------------
# Discovery: find running lab nodes
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_INVENTORY = _PROJECT_ROOT / "lab" / "devices.yaml"


def _discover_lab_nodes() -> list[dict[str, str]]:
    """Discover running containerlab nodes via `containerlab inspect`."""
    try:
        result = subprocess.run(
            ["containerlab", "inspect", "--all", "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    # Normalize output format
    containers: list[dict[str, Any]] = []
    if isinstance(data, list):
        containers = data
    elif isinstance(data, dict):
        if "containers" in data:
            containers = data["containers"]
        else:
            for lab_data in data.values():
                if isinstance(lab_data, dict) and "containers" in lab_data:
                    containers.extend(lab_data["containers"])
                elif isinstance(lab_data, list):
                    containers.extend(lab_data)

    nodes = []
    for c in containers:
        if not isinstance(c, dict):
            continue
        state = c.get("state", "").lower()
        if state not in ("running", "up"):
            continue
        name = c.get("name", "")
        lab = c.get("lab_name", "")
        ip = c.get("ipv4_address", c.get("mgmt_ipv4", ""))
        if "/" in ip:
            ip = ip.split("/")[0]
        if not name or not ip:
            continue
        prefix = f"clab-{lab}-"
        short_name = name[len(prefix):] if name.startswith(prefix) else name
        nodes.append({"name": short_name, "ip": ip, "lab": lab})

    return nodes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def lab_nodes() -> list[dict[str, str]]:
    """Return list of discovered running lab nodes."""
    nodes = _discover_lab_nodes()
    if not nodes:
        pytest.skip("No running containerlab nodes found. Run: ./lab/setup.sh")
    return nodes


@pytest.fixture(scope="session")
def lab_node(lab_nodes) -> dict[str, str]:
    """Return the first available lab node (for single-device tests)."""
    return lab_nodes[0]


@pytest.fixture(scope="session")
def eapi_session(lab_node):
    """Return a raw pyeapi Node connected to the first lab node.

    Useful for direct eAPI validation outside the network-mcp framework.
    """
    try:
        import pyeapi
    except ImportError:
        pytest.skip("pyeapi not installed")

    conn = pyeapi.connect(
        host=lab_node["ip"],
        transport="https",
        username="admin",
        password="admin",
        return_node=True,
        timeout=30,
    )
    return conn


@pytest.fixture(scope="session")
def conn_mgr_live(lab_nodes):
    """Return a ConnectionManager loaded with real lab inventory.

    This creates a fresh ConnectionManager pointed at the running lab
    instead of using the global mock-configured one.
    """
    from network_mcp.config import NetworkSettings
    from network_mcp.connection import ConnectionManager, DeviceCredentials

    settings = NetworkSettings(
        net_username="admin",
        net_password="admin",
        net_transport="https",
        net_read_only=True,
        net_demo_mode=False,
        net_verify_ssl=False,
    )
    mgr = ConnectionManager(settings)

    # Load discovered nodes
    for node in lab_nodes:
        mgr._inventory[node["name"]] = DeviceCredentials(
            host=node["ip"],
            username="admin",
            password="admin",
            transport="https",
            port=443,
            platform="eos",
            tags=["containerlab", f"lab:{node['lab']}"],
        )

    return mgr


@pytest.fixture(scope="session")
def eos_driver(lab_node):
    """Return an EosDriver connected to the first lab node."""
    from network_mcp.driver import EosDriver

    driver = EosDriver()
    driver.connect(
        host=lab_node["ip"],
        transport="https",
        username="admin",
        password="admin",
        timeout_show=30,
        timeout_config=60,
    )
    yield driver
    driver.close()


@pytest.fixture
def inventory_env(lab_nodes, monkeypatch):
    """Set environment variables so network-mcp tools use the live lab."""
    # Write a temporary inventory
    import tempfile
    import yaml

    devices = {}
    for node in lab_nodes:
        devices[node["name"]] = {
            "host": node["ip"],
            "platform": "eos",
            "username": "admin",
            "password": "admin",
            "transport": "https",
            "port": 443,
        }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({"devices": devices}, f)
        inv_path = f.name

    monkeypatch.setenv("NET_INVENTORY_FILE", inv_path)
    monkeypatch.setenv("NET_READ_ONLY", "true")
    monkeypatch.setenv("NET_DEMO_MODE", "false")
    monkeypatch.setenv("NET_VERIFY_SSL", "false")
    monkeypatch.setenv("NET_USERNAME", "admin")
    monkeypatch.setenv("NET_PASSWORD", "admin")

    yield inv_path

    os.unlink(inv_path)
