# ADR-004: Use Python Entry Points for Plugin Discovery

**Date:** 2024-09-05

**Status:** Accepted

## Context

The network-mcp server needs an extension mechanism for third-party drivers, compliance checks, and tool modules. We evaluated three approaches:

1. **Configuration-based plugins** — Operators specify plugin module paths in a config file or environment variable. The server imports and loads them at startup.

2. **Directory-based scanning** — The server scans a designated plugins directory (e.g., `~/.network-mcp/plugins/`) for Python modules and loads them.

3. **Python entry points** — Third-party packages declare entry points in their `pyproject.toml` under designated groups (`network_mcp.drivers`, `network_mcp.compliance`, `network_mcp.tools`). The server discovers them via `importlib.metadata.entry_points()`.

Key considerations:

- Configuration-based plugins require operators to know exact module paths and maintain a config file. This is fragile across environments and package updates.
- Directory-based scanning requires a well-known directory, file naming conventions, and doesn't integrate with package management (pip/uv).
- Entry points are the standard Python mechanism for plugin discovery. `pip install plugin-package` automatically makes the plugin discoverable — no config file changes needed.
- Entry points work across virtual environments, Docker containers, and CI/CD pipelines without additional configuration.
- Security is a concern: any installed package can register entry points. An allowlist/blocklist mechanism is needed.

## Decision

Use **Python entry points** for plugin discovery, with security controls via `EOS_PLUGIN_ALLOWLIST` and `EOS_PLUGIN_BLOCKLIST` environment variables.

Three entry point groups are defined:

- `network_mcp.drivers` — `NetworkDriver` implementations for new device platforms
- `network_mcp.compliance` — Compliance check sets (lists of check definitions)
- `network_mcp.tools` — MCP tool modules that register tools via `@mcp.tool()`

## Consequences

### Positive

- **Standard Python mechanism** — `pip install latticio-driver-nokia` immediately makes the Nokia driver available. No config file editing required.
- **Package management integration** — plugins are installed, upgraded, and removed via pip/uv like any other package. Dependencies are handled by the package manager.
- **Environment-portable** — works identically in local development, Docker containers, and production deployments. No path-dependent configuration.
- **Discoverable** — `importlib.metadata.entry_points()` provides a clean API for listing all installed plugins, which powers the `net_list_plugins` tool.
- **Versioned** — each plugin is a proper Python package with a version, making it easy to pin, upgrade, and audit.

### Negative

- **Security surface** — any installed package can register entry points. Mitigated by:
  - `EOS_PLUGIN_BLOCKLIST` to explicitly block untrusted packages (takes precedence)
  - `EOS_PLUGIN_ALLOWLIST` to restrict to only approved packages
  - Audit logging of all plugin lifecycle events (discovery, load, block, failure)
  - Built-in drivers cannot be overridden by plugins
- **Discovery overhead** — `entry_points()` scans all installed packages on first call. Mitigated by caching results after first discovery.
- **Implicit loading** — plugins load at import time, which can have side effects. Mitigated by try/except around each plugin load with error logging.
- **Testing complexity** — testing plugin discovery requires either installing test packages or mocking `importlib.metadata`. The `_reset_plugins()` helper in `plugins.py` supports test isolation.
