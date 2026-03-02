# Plugin Development Guide

network-mcp supports third-party plugins via Python entry points. Plugins can add new device drivers, compliance check sets, and custom tool modules without modifying network-mcp itself.

## How Entry Points Work

Python entry points are a standard packaging mechanism for plugin discovery. When a package declares entry points in its `pyproject.toml`, any Python program can discover and load them using `importlib.metadata.entry_points()`. This is the same pattern used by pytest, NAPALM, and many other projects.

network-mcp discovers plugins at startup and loads them after all built-in modules. Use the `net_list_plugins` tool to see what plugins are installed.

## Entry Point Groups

| Group | Purpose | Example |
|-------|---------|---------|
| `network_mcp.drivers` | NetworkDriver implementations | Nokia SR OS, Fortinet, Cumulus |
| `network_mcp.compliance` | Compliance check sets | DISA STIG, PCI-DSS, SOX |
| `network_mcp.tools` | Custom tool modules | Organization-specific tools |

## Creating a Driver Plugin

A driver plugin provides a `NetworkDriver` implementation for a new platform.

### 1. Create the driver class

Your driver must implement the `NetworkDriver` protocol from `network_mcp.drivers.base`:

```python
# src/network_mcp_driver_nokia/driver.py
from typing import Any


class SROSDriver:
    """Nokia SR OS driver for network-mcp."""

    @property
    def platform(self) -> str:
        return "sros"

    @property
    def vendor(self) -> str:
        return "nokia"

    def connect(self, **kwargs: Any) -> None:
        # Establish connection to Nokia SR OS device
        ...

    def close(self) -> None:
        ...

    def is_connected(self) -> bool:
        ...

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        ...

    def run_config(self, commands: list[str]) -> list[str]:
        ...

    def get_facts(self) -> dict:
        ...

    def get_interfaces(self) -> dict[str, dict]:
        ...

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        ...

    def get_arp_table(self) -> list[dict]:
        ...

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        ...

    def get_config(self, sanitized: bool = True) -> dict:
        ...
```

### 2. Declare the entry point

In your package's `pyproject.toml`:

```toml
[project]
name = "network-mcp-driver-nokia"
version = "0.1.0"
dependencies = ["network-mcp>=3.0.0"]

[project.entry-points."network_mcp.drivers"]
nokia_sros = "network_mcp_driver_nokia.driver:SROSDriver"
```

The entry point name (`nokia_sros`) becomes the platform key in the driver registry. When a device in the inventory has `platform: nokia_sros`, network-mcp will use your driver.

### 3. Install and verify

```bash
pip install network-mcp-driver-nokia
```

Use the `net_list_plugins` tool to verify the driver was discovered:

```json
{
  "status": "success",
  "data": {
    "network_mcp.drivers": [
      {"name": "nokia_sros", "module": "network_mcp_driver_nokia.driver:SROSDriver", "loaded": true}
    ],
    "total_plugins": 1
  }
}
```

## Creating a Compliance Plugin

A compliance plugin provides additional check sets for the compliance framework.

### 1. Define compliance checks

Your entry point should resolve to a list of `ComplianceCheck` objects:

```python
# src/network_mcp_compliance_stig/checks.py
from network_mcp.tools.compliance import ComplianceCheck


def _check_ssh_v2(output: str) -> bool:
    return "ip ssh version 2" in output


STIG_CHECKS = [
    ComplianceCheck(
        id="STIG-NET-001",
        title="Ensure SSH protocol version 2",
        severity="HIGH",
        category="management",
        command="show running-config section ssh",
        encoding="text",
        check_fn=_check_ssh_v2,
        remediation="Configure: ip ssh version 2",
    ),
    # ... more checks
]
```

### 2. Declare the entry point

```toml
[project]
name = "network-mcp-compliance-stig"
version = "0.1.0"
dependencies = ["network-mcp>=3.0.0"]

[project.entry-points."network_mcp.compliance"]
disa_stig = "network_mcp_compliance_stig.checks:STIG_CHECKS"
```

The entry point value must resolve to a list of `ComplianceCheck` instances.

## Creating a Tool Plugin

A tool plugin provides custom MCP tools.

### 1. Create a setup function

Your entry point should resolve to a callable that receives the MCP server instance:

```python
# src/network_mcp_tools_custom/tools.py
from network_mcp.helpers import READ_ONLY, run_show_command
from network_mcp.server import conn_mgr


def setup(mcp):
    """Register custom tools with the MCP server."""

    @mcp.tool(annotations=READ_ONLY)
    def custom_get_uptime(host: str) -> dict:
        """Get device uptime.

        Args:
            host: Hostname or IP of the device.
        """
        result = run_show_command(conn_mgr, host, ["show version"])
        if result["status"] == "error":
            return result
        return {
            "status": "success",
            "device": host,
            "data": {"uptime": result["data"][0].get("uptime", "unknown")},
        }
```

### 2. Declare the entry point

```toml
[project]
name = "network-mcp-tools-custom"
version = "0.1.0"
dependencies = ["network-mcp>=3.0.0"]

[project.entry-points."network_mcp.tools"]
custom_tools = "network_mcp_tools_custom.tools:setup"
```

The entry point value must resolve to a callable that accepts the `mcp` instance.

## Testing Your Plugin

### Test with mock entry points

```python
from unittest.mock import MagicMock, patch

import importlib.metadata


def test_my_driver_plugin():
    """Test that the driver plugin loads correctly."""
    from network_mcp_driver_nokia.driver import SROSDriver

    driver = SROSDriver()
    assert driver.platform == "sros"
    assert driver.vendor == "nokia"
```

### Test integration with network-mcp

```python
def test_driver_registered():
    """Test that the driver appears in the registry after plugin load."""
    from network_mcp.drivers import DRIVER_REGISTRY
    assert "nokia_sros" in DRIVER_REGISTRY
```

### Verify with net_list_plugins

After installing your plugin package, call `net_list_plugins` through an MCP client to confirm it appears in the plugin list.

## Plugin Guidelines

1. **Don't conflict with built-in drivers** - Plugin drivers that use the same platform key as a built-in driver (e.g., `eos`, `iosxe`, `nxos`, `junos`) will be skipped.
2. **Handle import errors** - network-mcp catches all exceptions during plugin loading. Your plugin won't crash the server, but errors are logged.
3. **Follow naming conventions** - Use the `network-mcp-driver-*`, `network-mcp-compliance-*`, or `network-mcp-tools-*` naming pattern for your package.
4. **Depend on `network-mcp>=3.0.0`** - The plugin system requires the renamed package.
5. **Test without real devices** - Use mock drivers and mock responses for unit testing.
