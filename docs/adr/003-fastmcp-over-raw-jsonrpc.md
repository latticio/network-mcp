# ADR-003: Use FastMCP Framework Instead of Raw JSON-RPC

**Date:** 2024-08-10

**Status:** Accepted

## Context

The Model Context Protocol (MCP) defines a JSON-RPC 2.0 based protocol for AI assistants to interact with external tools and resources. To implement an MCP server, we needed to choose between:

1. **Raw JSON-RPC implementation** — Building the protocol handler from scratch using a JSON-RPC library (e.g., `jsonrpcserver`) or raw socket/HTTP handling.

2. **FastMCP** (`mcp[cli]`) — The official Python MCP SDK that provides a high-level framework with decorator-based tool registration, automatic transport handling (stdio, HTTP), lifecycle management, and protocol compliance.

Key considerations:

- The MCP protocol involves complex lifecycle management: initialization, capability negotiation, tool listing, tool invocation, resource serving, prompt templates, and graceful shutdown.
- Raw JSON-RPC requires implementing all protocol messages, error codes, and transport framing manually.
- FastMCP provides decorator-based registration (`@mcp.tool()`, `@mcp.resource()`, `@mcp.prompt()`) that maps directly to MCP primitives.
- FastMCP handles both stdio and Streamable HTTP transports, including OAuth 2.1 authentication for HTTP.
- FastMCP auto-generates tool schemas from Python function signatures and type hints.
- The MCP specification is evolving; using the SDK ensures protocol compliance as the spec updates.

## Decision

Use **FastMCP** (`mcp[cli]>=1.26,<2.0`) as the MCP server framework.

## Consequences

### Positive

- **Decorator-based tool registration** — tools are defined with `@mcp.tool()` and automatically registered with correct JSON schemas derived from function signatures and docstrings.
- **Automatic transport handling** — stdio and Streamable HTTP work out of the box via `mcp.run()` with a `--transport` flag. No custom socket or HTTP server code needed.
- **Protocol compliance** — FastMCP implements the full MCP specification including capability negotiation, pagination, and error handling. As the spec evolves, updating the SDK keeps us compliant.
- **Built-in auth** — FastMCP's HTTP transport includes OAuth 2.1 / Bearer token middleware, eliminating the need for custom auth handling.
- **Type-safe** — pydantic v2 integration for input validation and output serialization.
- **Resource and prompt support** — `@mcp.resource()` and `@mcp.prompt()` decorators provide MCP resources and prompt templates alongside tools.

### Negative

- **Framework coupling** — the codebase is tightly coupled to FastMCP's API. Migrating to a different MCP SDK would require rewriting tool registrations. Given that FastMCP is the official SDK, this risk is acceptable.
- **Version constraints** — pinning to `<2.0` means we need to explicitly test and update when FastMCP 2.0 releases, as breaking changes are possible.
- **Framework quirks** — some behaviors are non-obvious (e.g., `FastMCP()` does not accept a `version` kwarg, decorators must use parentheses). These are documented in CLAUDE.md and CONTRIBUTING.md.
- **Stdout is reserved** — FastMCP uses stdout for JSON-RPC in stdio mode, so all logging must go to stderr. This is a fundamental constraint of the MCP protocol, not specific to FastMCP.
