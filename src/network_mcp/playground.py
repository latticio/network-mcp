"""Interactive tool playground REPL for network-mcp.

Provides a terminal REPL that calls MCP tools directly without a client.
Usage: ``latticio playground`` or ``latticio repl``.

Note: Like cli.py, this module uses ``print()`` intentionally — it is a
standalone CLI tool that writes directly to the terminal, not the MCP
server transport.
"""

from __future__ import annotations

import inspect
import json
import sys
from typing import Any


def _get_tools() -> dict[str, Any]:
    """Return the registered tool dict from the FastMCP instance."""
    from network_mcp.server import mcp

    tool_mgr = getattr(mcp, "_tool_manager", None)
    if tool_mgr is None:
        return {}
    return getattr(tool_mgr, "_tools", {})


def _format_result(result: Any) -> str:
    """Pretty-print a tool result as indented JSON or plain text."""
    if isinstance(result, dict):
        return json.dumps(result, indent=2, default=str)
    if isinstance(result, list):
        return json.dumps(result, indent=2, default=str)
    return str(result)


def _parse_args(raw: str) -> dict[str, Any]:
    """Parse ``key=value`` pairs from user input into a kwargs dict.

    Supports: strings, integers, floats, booleans (true/false), and
    JSON values (lists/dicts via ``[...]`` or ``{...}``).
    """
    kwargs: dict[str, Any] = {}
    if not raw.strip():
        return kwargs

    # Split on spaces, but respect quoted values
    tokens = _tokenize(raw.strip())
    for token in tokens:
        if "=" not in token:
            raise ValueError(f"Invalid argument (expected key=value): {token!r}")
        key, _, value = token.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Empty key in argument: {token!r}")
        kwargs[key] = _coerce_value(value)
    return kwargs


def _tokenize(raw: str) -> list[str]:
    """Split input on unquoted spaces, keeping quoted strings intact."""
    tokens: list[str] = []
    current: list[str] = []
    in_quote: str | None = None

    for char in raw:
        if in_quote:
            current.append(char)
            if char == in_quote:
                in_quote = None
        elif char in ('"', "'"):
            in_quote = char
            current.append(char)
        elif char == " ":
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)

    if current:
        tokens.append("".join(current))
    return tokens


def _coerce_value(value: str) -> Any:
    """Coerce a string value to an appropriate Python type."""
    # Strip surrounding quotes
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]

    # Booleans
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False

    # None
    if value.lower() == "none":
        return None

    # JSON arrays/objects
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

    # Integer
    try:
        return int(value)
    except ValueError:
        pass

    # Float
    try:
        return float(value)
    except ValueError:
        pass

    return value


def _print_help(tools: dict[str, Any]) -> None:
    """Print REPL help text with available commands."""
    print("Commands:")  # noqa: T201
    print("  help, ?          — Show this help message")  # noqa: T201
    print("  list, tools      — List all available tools")  # noqa: T201
    print("  describe <tool>  — Show tool description and parameters")  # noqa: T201
    print("  <tool> k=v ...   — Call a tool with keyword arguments")  # noqa: T201
    print("  exit, quit       — Exit the REPL")  # noqa: T201
    print(f"\n{len(tools)} tools available. Type 'list' to see them.")  # noqa: T201


def _list_tools(tools: dict[str, Any]) -> None:
    """Print all available tool names in columns."""
    names = sorted(tools.keys())
    if not names:
        print("No tools registered.")  # noqa: T201
        return

    # Determine column width
    max_len = max(len(n) for n in names)
    try:
        import shutil

        cols = max(1, shutil.get_terminal_size().columns // (max_len + 2))
    except Exception:
        cols = 3

    for i, name in enumerate(names):
        end = "\n" if (i + 1) % cols == 0 else "  "
        print(f"{name:<{max_len}}", end=end)  # noqa: T201
    if len(names) % cols != 0:
        print()  # noqa: T201
    print(f"\n{len(names)} tools total.")  # noqa: T201


def _describe_tool(tools: dict[str, Any], tool_name: str) -> None:
    """Print detailed info about a specific tool."""
    tool = tools.get(tool_name)
    if tool is None:
        print(f"Unknown tool: {tool_name!r}")  # noqa: T201
        return

    print(f"\n{tool_name}")  # noqa: T201
    if tool.description:
        print(f"  {tool.description}")  # noqa: T201

    sig = inspect.signature(tool.fn)
    params = sig.parameters
    if params:
        print("\nParameters:")  # noqa: T201
        for pname, param in params.items():
            annotation = param.annotation
            type_hint = annotation.__name__ if hasattr(annotation, "__name__") else str(annotation)
            if type_hint == "<class 'inspect._empty'>":
                type_hint = "any"
            default = ""
            if param.default is not inspect.Parameter.empty:
                default = f" = {param.default!r}"
            print(f"  {pname}: {type_hint}{default}")  # noqa: T201
    else:
        print("\nNo parameters.")  # noqa: T201


def _call_tool(tools: dict[str, Any], tool_name: str, kwargs: dict[str, Any]) -> None:
    """Call a tool function and print its result."""
    tool = tools.get(tool_name)
    if tool is None:
        print(f"Unknown tool: {tool_name!r}. Type 'list' to see available tools.")  # noqa: T201
        return

    fn = tool.fn
    try:
        if inspect.iscoroutinefunction(fn):
            import asyncio  # noqa: PLC0415

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(fn(**kwargs))
            finally:
                loop.close()
        else:
            result = fn(**kwargs)
        print(_format_result(result))  # noqa: T201
    except TypeError as exc:
        print(f"Error: {exc}")  # noqa: T201
        print(f"Hint: use 'describe {tool_name}' to see expected parameters.")  # noqa: T201
    except Exception as exc:
        print(f"Error calling {tool_name}: {type(exc).__name__}: {exc}")  # noqa: T201


def _setup_readline(tools: dict[str, Any]) -> None:
    """Set up tab completion for tool names if readline is available."""
    try:
        import readline
    except ImportError:
        return

    tool_names = sorted(tools.keys())
    commands = ["help", "list", "tools", "describe", "exit", "quit"]

    def completer(text: str, state: int) -> str | None:
        options = [w for w in tool_names + commands if w.startswith(text)]
        if state < len(options):
            return options[state]
        return None

    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")


def run_repl() -> int:
    """Run the interactive tool playground REPL. Returns exit code."""
    tools = _get_tools()
    _setup_readline(tools)

    print("network-mcp tool playground")  # noqa: T201
    print("Type 'help' or '?' for commands, 'exit' to quit.\n")  # noqa: T201

    while True:
        try:
            line = input("mcp> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()  # noqa: T201
            break

        if not line:
            continue

        # Split into command and rest
        parts = line.split(None, 1)
        cmd = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        if cmd in ("exit", "quit"):
            break
        elif cmd in ("help", "?"):
            _print_help(tools)
        elif cmd in ("list", "tools"):
            _list_tools(tools)
        elif cmd == "describe":
            if not rest:
                print("Usage: describe <tool_name>")  # noqa: T201
            else:
                _describe_tool(tools, rest.strip())
        else:
            # Treat as tool invocation
            try:
                kwargs = _parse_args(rest)
            except ValueError as exc:
                print(f"Parse error: {exc}")  # noqa: T201
                continue
            _call_tool(tools, cmd, kwargs)

    print("Goodbye!")  # noqa: T201
    return 0


def main() -> None:
    """Entry point for ``latticio playground`` / ``latticio repl``."""
    sys.exit(run_repl())
