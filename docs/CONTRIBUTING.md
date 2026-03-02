# Contributing to network-mcp

Thanks for your interest in contributing! This guide covers development setup, code style, testing patterns, how to add new tools, and how to write plugins.

## Development Environment Setup

### Prerequisites

- **Python 3.11+** (3.12 and 3.13 also supported)
- **uv** package manager (recommended) or pip
- **Git** for version control

### Clone and Install

```bash
# Clone the repository
git clone https://github.com/latticio/network-mcp.git
cd network-mcp

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies including dev extras
uv sync --extra dev

# Verify installation
uv run network-mcp --help
uv run pytest
```

### Local Configuration

Copy `.env.example` to `.env` for local overrides:

```bash
cp .env.example .env
```

The `.env` file is gitignored and will never be committed. pydantic-settings loads it automatically at startup.

> **Note:** `pythonpath = ["src"]` is configured in `pyproject.toml` so pytest can find the `network_mcp` package without installing it.

## Code Style & Linting

**Ruff** is the sole linter and formatter (replaces flake8, isort, and black).

### Configuration (from `pyproject.toml`)

- **Line length:** 120 characters
- **Target version:** Python 3.11
- **Ruff rules:** `E`, `F`, `I`, `W`, `B`, `UP`, `S` (security)
- **Test exemptions:** `S101` (assert), `S105`/`S106` (hardcoded passwords in test data)

### Commands

```bash
uv run ruff check src/ tests/           # lint
uv run ruff check --fix src/ tests/     # lint + autofix
uv run ruff format src/ tests/          # format
uv run ruff format --check src/ tests/  # check formatting without changing
```

## Running Tests

```bash
uv run pytest                              # run all tests
uv run pytest -v                           # verbose
uv run pytest tests/test_device_tools.py   # single file
uv run pytest -k "test_get_vlans"          # by name pattern
uv run pytest --cov=network_mcp            # with coverage
uv run pytest --cov=network_mcp -v         # coverage + verbose
```

- **Coverage target:** 85% (enforced via `fail_under = 85` in `pyproject.toml`)
- **Async tests:** `asyncio_mode = "auto"` is configured, so async test functions just work without extra decorators

## Testing Patterns

This is the most important section for new contributors. The mocking pattern is non-obvious and getting it wrong means your tests won't work.

### Why `patch.object()` Is Required

Tool modules import `conn_mgr` at the module level:

```python
# In src/network_mcp/tools/vlans.py (and every other tool module):
from network_mcp.server import conn_mgr, mcp
```

This creates a **local reference** in the tool module's namespace that points to the `ConnectionManager` instance. If you try to patch the server module's attribute:

```python
# WRONG - this only changes server.py's reference, not the tool module's copy
with patch("network_mcp.server.conn_mgr", mock):
    result = eos_get_vlans(host="spine-01")  # still uses the REAL conn_mgr
```

Instead, you must patch **the object itself** so all references see the change:

```python
# CORRECT - patches the actual object that all modules share
with patch.object(conn_mgr, "get_driver", return_value=mock_node):
    result = eos_get_vlans(host="spine-01")  # uses the mocked conn_mgr
```

### Key Fixtures in `conftest.py`

The test fixtures in `tests/conftest.py` handle this automatically:

#### `mock_node`

A `MagicMock` that satisfies the `DeviceDriver` interface. It wires `run_show` to `run_commands` and `run_config` to `config` so both the driver API and legacy pyeapi API share the same mock methods:

```python
@pytest.fixture
def mock_node():
    node = MagicMock()
    node.run_show = node.run_commands
    node.run_config = node.config
    node.platform = "eos"
    node.is_connected.return_value = True
    return node
```

#### `mock_conn_mgr`

Patches both `get_driver` and `acquire` on the real `ConnectionManager` instance so that `run_show_command()` (which uses `conn_mgr.acquire()`) and any direct `get_driver()` calls both return `mock_node`:

```python
@pytest.fixture
def mock_conn_mgr(mock_node):
    @contextmanager
    def _mock_acquire(host):
        yield mock_node

    with (
        patch.object(conn_mgr, "get_driver", return_value=mock_node) as patched,
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield patched
```

#### `make_test_settings(**overrides)`

Creates a `NetworkSettings` instance with test defaults. Use it when you need to test settings-dependent behavior:

```python
settings = make_test_settings(net_read_only=False)
```

#### Autouse Reset Fixtures

These run automatically before every test to prevent cross-test contamination:

- `_reset_rate_limiter` -- clears per-device rate limiter buckets
- `_reset_command_cache` -- clears the command cache
- `_reset_circuit_breaker` -- resets the circuit breaker registry
- `_reset_metrics_collector` -- resets the metrics collector

### Example Test

```python
from network_mcp.tools.vlans import eos_get_vlans


def test_get_vlans_success(mock_conn_mgr, mock_node):
    mock_node.run_commands.return_value = [{"vlans": {"100": {"name": "SERVERS"}}}]
    result = eos_get_vlans(host="spine-01")
    assert result["status"] == "success"
    assert result["device"] == "spine-01"
    assert "100" in result["data"]


def test_get_vlans_connection_error(mock_conn_mgr, mock_node):
    import pyeapi.eapilib
    mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("spine-01", "connection refused")
    result = eos_get_vlans(host="spine-01")
    assert result["status"] == "error"
    assert result["device"] == "spine-01"
    assert "Connection" in result["error"]
```

## How to Add a New Tool

This is a complete step-by-step tutorial for adding a new MCP tool to the server. Follow every step to ensure your tool integrates correctly with the platform's safety features, testing infrastructure, and documentation.

### Step 1: Choose the Right Module

Decide where your tool belongs based on its scope and vendor specificity:

| Tool type | Location | Namespace prefix | When to use |
|-----------|----------|------------------|-------------|
| Vendor-agnostic tool | `src/network_mcp/tools/common/<module>.py` | `net_` | Tool works across all vendors via `NetworkDriver` protocol |
| Arista EOS tool | `src/network_mcp/tools/<module>.py` | `eos_` | EOS-specific commands or features (eAPI) |
| Cisco NX-OS tool | `src/network_mcp/tools/cisco/<module>.py` | `nxos_` | NX-OS-specific commands or features (NX-API) |
| New protocol/feature area | Create a new module (see [Adding a New Tool Module](#adding-a-new-tool-module)) | `eos_`, `net_`, or `nxos_` |

**Choosing a namespace:**

- **`net_*`** — Use for tools that work identically across all supported vendors. These use the `NetworkDriver` protocol and should not contain vendor-specific commands.
- **`eos_*`** — Use for Arista EOS-specific tools. These use eAPI commands and may not work on other platforms.
- **`nxos_*`** — Use for Cisco NX-OS-specific tools. These use NX-API commands.

Existing EOS modules and what belongs in them:

- `device.py` -- device facts, health, inventory, TCAM, drops
- `interfaces.py` -- interface status, counters, errors, transceivers
- `vlans.py` -- VLAN CRUD
- `routing.py` -- BGP, OSPF, routes, ARP/NDP, prefix-lists, route-maps
- `switching.py` -- MLAG, STP, MAC table, LLDP, port-channels
- `monitoring.py` -- CPU, memory, temp, power, fans, NTP, logging
- `config_mgmt.py` -- running/startup config, diffs, save
- `sessions.py` -- config sessions
- `troubleshoot.py` -- ping, traceroute, interface flap detection
- `fabric.py` -- multi-device fabric health
- `validation.py` -- ANTA-based validation

### Step 2: Write the Tool Function

Here is a complete minimal read-only tool:

```python
from network_mcp.helpers import READ_ONLY, run_show_command
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
def eos_get_hardware_health(host: str) -> dict:
    """Get hardware health status including power supplies and cooling from an Arista EOS device.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show system environment power", "show system environment cooling"])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": {
            "power": result["data"][0],
            "cooling": result["data"][1],
        },
    }
```

Here is a complete write tool with input validation:

```python
from network_mcp.helpers import WRITE_SAFE, run_config_command, validate_cli_param, validate_interface_name
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=WRITE_SAFE)
def eos_set_interface_description(host: str, interface: str, description: str) -> dict:
    """[WRITE] Set the description on an interface. Requires NET_READ_ONLY=false.

    Args:
        host: Hostname, IP address, or inventory name of the device.
        interface: Interface name (e.g., 'Ethernet1', 'Loopback0').
        description: Description string for the interface.
    """
    iface_err = validate_interface_name(interface)
    if iface_err:
        return {"status": "error", "device": host, "error": iface_err}
    desc_err = validate_cli_param(description, "description")
    if desc_err:
        return {"status": "error", "device": host, "error": desc_err}
    return run_config_command(
        conn_mgr, host,
        [f"interface {interface}", f"description {description}"],
        "set_interface_description",
    )
```

Key rules for the tool function:

1. **`host` is always the first parameter** -- every tool takes `host` first
2. **Use `@mcp.tool()` with parentheses** -- `@mcp.tool` without parentheses breaks FastMCP
3. **Choose the right annotation:** `READ_ONLY`, `WRITE_SAFE`, or `DESTRUCTIVE`
4. **Use `run_show_command()`** for reads and **`run_config_command()`** for writes -- they handle retry, rate limiting, audit logging, sanitization, and error handling
5. **Validate all inputs** before sending to the device
6. **Return structured dicts:** `{"status": "success"|"error", "device": host, ...}`
7. **Prefix write tool docstrings with `[WRITE]`** so the LLM knows the tool modifies state
8. **The docstring becomes the tool description** visible to the AI assistant -- make it clear and concise
9. **Never call pyeapi directly** -- always go through the helpers
10. **Never use `print()`** -- stdout is the JSON-RPC transport

### Step 3: Write Tests

Create or update `tests/test_<module>_tools.py`. Use the `mock_conn_mgr` and `mock_node` fixtures from `conftest.py`:

```python
"""Tests for hardware health tools."""

from network_mcp.tools.device import eos_get_hardware_health


class TestGetHardwareHealth:
    """Tests for eos_get_hardware_health."""

    def test_success(self, mock_conn_mgr, mock_node):
        """Test successful hardware health retrieval."""
        mock_node.run_commands.return_value = [
            {"powerSupplies": {"1": {"state": "ok"}, "2": {"state": "ok"}}},
            {"coolingUnits": {"1": {"status": "ok"}}},
        ]
        result = eos_get_hardware_health(host="spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert "power" in result["data"]
        assert "cooling" in result["data"]

    def test_connection_error(self, mock_conn_mgr, mock_node):
        """Test handling of connection errors."""
        import pyeapi.eapilib

        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError(
            "spine-01", "connection refused"
        )
        result = eos_get_hardware_health(host="spine-01")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        """Test validation rejects invalid hostnames."""
        result = eos_get_hardware_health(host="host; rm -rf /")
        assert result["status"] == "error"
```

Checklist for tests:

- Test the success path with realistic mock data
- Test connection errors (device unreachable)
- Test invalid inputs (CLI injection attempts, out-of-range values)
- Verify `"device"` key is always present in responses
- Verify `"status"` is always `"success"` or `"error"`

### Step 4: Update Documentation

After adding your tool:

1. **Add to `docs/TOOLS.md`** -- document the tool name, parameters, and example output
2. **Update tool count in `README.md`** -- the header mentions the total tool count
3. **Update tool count in `CLAUDE.md`** -- the module listing includes per-module counts

### Step 5: Verify

Run the full check before committing:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest --tb=short -q
```

All three must pass. Fix any linting issues (`ruff check --fix` can auto-fix most) and test failures before creating a PR.

## Adding a New Tool Module

If your tools don't fit an existing module:

1. **Create** `src/network_mcp/tools/<new_module>.py`
2. **Register** the module in `server.py`:
   - Add to `_CORE_MODULES` if it should always load
   - Add to `_OPTIONAL_MODULES` if it requires an extra dependency or should be opt-in
3. **Create** `tests/test_<new_module>_tools.py` with corresponding tests
4. **Handle `ImportError`** if the module depends on an optional package (see `gnmi.py` or `validation.py` for examples)

The module loading system in `server.py` uses `importlib.import_module()` to dynamically load tool modules. Each module self-registers its tools via `@mcp.tool()` decorators at import time.

## How to Write a Plugin

The network-mcp server supports third-party plugins via Python entry points. Plugins can add device drivers, compliance checks, or additional MCP tools without modifying the core codebase.

### Plugin Types

| Entry point group | Purpose | Example |
|-------------------|---------|---------|
| `network_mcp.drivers` | Custom device drivers for new vendors | Nokia SR OS, Palo Alto PAN-OS |
| `network_mcp.compliance` | Compliance check packs | SOX controls, PCI-DSS checks |
| `network_mcp.tools` | Additional MCP tools | Custom monitoring, vendor-specific features |

### Step 1: Create Plugin Package

Create a new Python package with this structure:

```
network-mcp-driver-nokia/
  src/
    network_mcp_driver_nokia/
      __init__.py          # SROSDriver class
  tests/
    test_sros_driver.py
  pyproject.toml
  README.md
```

Configure the entry point in `pyproject.toml`:

```toml
[project]
name = "network-mcp-driver-nokia"
version = "0.1.0"
description = "Nokia SR OS driver plugin for network-mcp"
requires-python = ">=3.11"
dependencies = ["network-mcp>=3.0.0"]

[project.entry-points."network_mcp.drivers"]
nokia_sros = "network_mcp_driver_nokia:SROSDriver"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Step 2: Implement Your Plugin

#### Driver Plugin

Implement the `NetworkDriver` protocol defined in `network_mcp.drivers.base`:

```python
"""Nokia SR OS driver for network-mcp."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("network-mcp.nokia")


class SROSDriver:
    """NetworkDriver implementation for Nokia SR OS devices."""

    platform: str = "nokia_sros"

    def __init__(self) -> None:
        self._host: str | None = None
        self._connected: bool = False

    def connect(
        self, host: str, transport: str = "https",
        username: str = "admin", password: str = "", **kwargs: Any,
    ) -> None:
        """Connect to a Nokia SR OS device."""
        self._host = host
        self._connected = True

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands and return structured output."""
        if not self._connected:
            raise ConnectionError("Not connected")
        # Implement SR OS command execution here
        results = []
        for cmd in commands:
            results.append(self._execute_command(cmd))
        return results

    def run_config(self, commands: list[str]) -> list[str]:
        """Execute configuration commands."""
        if not self._connected:
            raise ConnectionError("Not connected")
        # Implement SR OS config mode here
        return [f"ok: {cmd}" for cmd in commands]

    def close(self) -> None:
        """Close the connection."""
        self._connected = False

    def is_connected(self) -> bool:
        """Return connection status."""
        return self._connected

    # NetworkDriver protocol: 6 normalized getters

    def get_facts(self) -> dict[str, Any]:
        """Return device facts (hostname, serial, model, version, uptime)."""
        result = self.run_show(["show system information"])[0]
        return {
            "hostname": result.get("name", self._host),
            "serial": result.get("serial-number", ""),
            "model": result.get("type", ""),
            "version": result.get("version", ""),
            "uptime_seconds": result.get("up-time", 0),
        }

    def get_interfaces(self) -> dict[str, Any]:
        """Return interface status dict keyed by interface name."""
        return self.run_show(["show port"])[0]

    def get_bgp_summary(self) -> dict[str, Any]:
        """Return BGP summary with peer states."""
        return self.run_show(["show router bgp summary"])[0]

    def get_arp_table(self) -> list[dict[str, Any]]:
        """Return ARP table entries."""
        return self.run_show(["show router arp"])[0].get("entries", [])

    def get_lldp_neighbors(self) -> dict[str, Any]:
        """Return LLDP neighbor information."""
        return self.run_show(["show system lldp neighbor"])[0]

    def get_config(self, section: str = "") -> str:
        """Return device configuration as text."""
        cmd = f"admin display-config {section}".strip()
        return self.run_show([cmd], encoding="text")[0].get("output", "")

    def _execute_command(self, cmd: str) -> dict:
        """Execute a single command on the device (implement with your transport)."""
        raise NotImplementedError("Implement with NETCONF, gRPC, or SSH transport")
```

Once installed, the driver is automatically available for devices with `platform: nokia_sros` in the inventory.

#### Compliance Plugin

Return a list of compliance check dicts:

```python
"""SOX compliance checks for network-mcp."""

SOX_CHECKS = [
    {
        "id": "SOX-NET-001",
        "name": "Require AAA authentication",
        "description": "All devices must use TACACS+ or RADIUS for authentication",
        "severity": "critical",
        "check_command": "show aaa",
        "check_fn": lambda data: bool(data.get("tacacs") or data.get("radius")),
    },
    {
        "id": "SOX-NET-002",
        "name": "Require encrypted management",
        "description": "Management API must use HTTPS, not HTTP",
        "severity": "critical",
        "check_command": "show management api http-commands",
        "check_fn": lambda data: data.get("httpsServer", {}).get("running", False),
    },
]
```

Entry point in `pyproject.toml`:

```toml
[project.entry-points."network_mcp.compliance"]
sox = "network_mcp_compliance_sox:SOX_CHECKS"
```

#### Tool Plugin

Tool plugins register MCP tools at import time. The entry point can be either a module (tools register via `@mcp.tool()` decorators) or a callable that receives the `mcp` instance:

```python
"""Custom monitoring tools for network-mcp."""

from network_mcp.helpers import READ_ONLY, run_show_command
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
def custom_get_optics_health(host: str) -> dict:
    """Get detailed optical transceiver health from a network device.

    Args:
        host: Hostname, IP address, or inventory name of the device.
    """
    result = run_show_command(conn_mgr, host, ["show interfaces transceiver detail"])
    if result["status"] == "error":
        return result
    return {"status": "success", "device": host, "data": result["data"][0]}
```

Entry point:

```toml
[project.entry-points."network_mcp.tools"]
optics_health = "network_mcp_optics:custom_get_optics_health"
```

### Step 3: Test Locally

Install your plugin package in development mode alongside network-mcp:

```bash
# From the plugin package directory
uv pip install -e .

# Verify the plugin is discovered
uv run python -c "from network_mcp.plugins import discover_plugins; print(discover_plugins())"

# Run the server in demo mode to test
NET_DEMO_MODE=true uv run network-mcp
```

### Security Considerations

- **Plugins run with full server privileges** -- they have the same access as core tools, including the ability to execute commands on devices
- **Operators can restrict plugins** via environment variables:
  - `NET_PLUGIN_ALLOWLIST` -- comma-separated list of allowed plugin package names (only these are loaded)
  - `NET_PLUGIN_BLOCKLIST` -- comma-separated list of blocked plugin package names (never loaded; takes precedence over allowlist)
- When both lists are empty, all discovered plugins are loaded (backward-compatible default)
- **Audit logging** tracks plugin lifecycle events (discovery, load, block, failure)
- **Driver plugins cannot override built-in drivers** -- if a plugin registers a driver with a name that conflicts with a built-in (`eos`, `iosxe`, `nxos`, `junos`), it is skipped with a warning

## Git Conventions

### Branch Naming

- `feature/<description>` -- new features
- `fix/<description>` -- bug fixes
- `docs/<description>` -- documentation changes

### Commit Messages

Use [conventional commits](https://www.conventionalcommits.org/):

- `feat:` -- new feature
- `fix:` -- bug fix
- `docs:` -- documentation
- `test:` -- test additions or changes
- `refactor:` -- code refactoring (no behavior change)
- `style:` -- formatting, linting (no behavior change)

### Pre-Commit Checklist

All code must pass these checks before merge:

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -v
```

## Critical Rules

These rules are essential for MCP server development. Violating them will break the server.

1. **Never log to stdout** -- stdout is the JSON-RPC transport for stdio mode. Use `logging` with `stream=sys.stderr`.
2. **Never use `print()`** -- it writes to stdout and corrupts the MCP protocol stream.
3. **Always use parentheses on decorators** -- `@mcp.tool()` not `@mcp.tool`. FastMCP requires the call syntax.
4. **`FastMCP()` does not accept a `version` kwarg** -- use `FastMCP("name")` only.
5. **pyeapi handles enable/config mode** -- don't send `enable` or `configure terminal` commands manually.
6. **JSON encoding is default** -- `run_commands()` returns parsed JSON dicts. Only use `encoding="text"` for commands that don't support JSON output (e.g., `show running-config`).
7. **Always handle exceptions** -- unhandled exceptions crash the MCP server. Use `run_show_command()` / `run_config_command()` which handle this for you, or use the `@handle_tool_errors` decorator.
8. **Never hardcode credentials** -- use environment variables or the inventory file.

## Project Structure

```
src/network_mcp/
  server.py            # FastMCP instance, lifespan, module loading
  connection.py        # Thread-safe connection pool (FIFO eviction)
  config.py            # Pydantic BaseSettings (env + .env)
  helpers.py           # run_show_command/run_config_command, validation, retry
  driver.py            # DeviceDriver protocol and PyeapiDriver adapter
  plugins.py           # Entry-point plugin discovery (drivers, compliance, tools)
  tools/               # Tool modules (each self-registers via @mcp.tool())
    common/            # Vendor-agnostic tools (net_* namespace)
    cisco/             # Cisco-specific tools (nxos_* namespace)
  integrations/        # Third-party platform integrations (NetBox, ServiceNow, etc.)
  resources/           # MCP resource handlers
  prompts/             # MCP prompt templates
tests/                 # Test files mirroring src structure
```

For the full structure and architecture, see [CLAUDE.md](../CLAUDE.md).

## Getting Help

- Open an [issue](https://github.com/latticio/network-mcp/issues) for bugs or feature requests
- Check [docs/TOOLS.md](TOOLS.md) for the complete tool reference
- Check [docs/SECURITY.md](SECURITY.md) for security architecture details
- Read [CLAUDE.md](../CLAUDE.md) for the full project reference used by AI assistants
