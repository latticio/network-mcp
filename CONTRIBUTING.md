# Contributing to network-mcp

Thank you for your interest in contributing to network-mcp! This guide will help you get started.

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Access to an Arista EOS device or cEOS/vEOS for integration testing (optional)

### Getting Started

```bash
# Clone the repository
git clone https://github.com/latticio/network-mcp.git
cd network-mcp

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Copy environment template
cp .env.example .env
```

### Running Tests

```bash
# Run all tests
uv run pytest -v

# Run with coverage
uv run pytest --cov -v

# Run a specific test file
uv run pytest tests/test_device_tools.py -v
```

### Linting and Formatting

```bash
# Check for lint errors
uv run ruff check src/ tests/

# Auto-fix lint errors
uv run ruff check --fix src/ tests/

# Check formatting
uv run ruff format --check src/ tests/

# Apply formatting
uv run ruff format src/ tests/
```

### Building

```bash
uv build
```

## Code Style

- We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting
- Line length: 120 characters
- Target Python version: 3.11+
- Use type hints on all function parameters and return values
- Follow existing patterns in the codebase

### Tool Pattern

Every MCP tool must follow this pattern:

```python
@mcp.tool()
def tool_name(host: str, ...other_params) -> dict:
    """Clear, one-line description of what this tool does.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    try:
        node = conn_mgr.get_node(host)
        result = node.run_commands(["show ..."])
        return {"status": "success", "device": host, "data": {...}}
    except Exception as e:
        return {"status": "error", "device": host, "error": str(e)}
```

Key rules:
- `host` is always the first parameter
- Return structured dicts with `status`, `device`, and `data`/`error` keys
- Always handle exceptions — never let a tool crash the server
- Write clear docstrings — the LLM reads these to decide which tool to use
- Never log to stdout (it breaks the MCP JSON-RPC stream)

## Pull Request Process

1. **Fork and branch**: Create a feature branch from `main` (e.g., `feature/add-acl-tools`)
2. **Write tests**: Add unit tests for new tools using mocked pyeapi responses
3. **Lint and test**: Ensure `ruff check` and `pytest` pass locally
4. **Commit**: Use [conventional commits](https://www.conventionalcommits.org/):
   - `feat:` — new feature
   - `fix:` — bug fix
   - `docs:` — documentation only
   - `test:` — adding or updating tests
   - `refactor:` — code change that neither fixes a bug nor adds a feature
5. **Open PR**: Fill out the PR template and link any related issues

## Adding a New Tool

1. Identify which module the tool belongs in (e.g., `tools/routing.py`)
2. Add the tool function with the `@mcp.tool()` decorator
3. Add mock response fixtures in `tests/conftest.py`
4. Write unit tests in the corresponding test file
5. Run `uv run python scripts/generate_docs.py` to regenerate tool docs

## Debugging with MCP Inspector

The [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) is a browser-based tool for testing MCP servers interactively. It lets you call tools, view responses, and debug issues without needing a full AI client:

```bash
npx @modelcontextprotocol/inspector uv run network-mcp
```

This opens a web UI where you can browse all registered tools, invoke them with test parameters, and inspect the JSON responses.

## Reporting Issues

- Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) for bugs
- Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md) for enhancements
- Use the [new tool request template](.github/ISSUE_TEMPLATE/new_tool_request.md) for new EOS tools

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
