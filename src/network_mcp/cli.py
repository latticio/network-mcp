"""Interactive onboarding wizard for latticio (network-mcp).

Provides the ``latticio init`` CLI subcommand that interactively scaffolds:
- ``devices.yaml`` — device inventory
- ``.env`` — NET_* environment variables
- MCP client config for Claude Desktop, Cursor, or VS Code

Note: This module uses ``print()`` and ``input()`` intentionally. Unlike the MCP
server modules where stdout is the JSON-RPC transport, this is a standalone CLI tool
that writes directly to the terminal.  ``print()`` is safe here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

_PLATFORMS = ["eos", "iosxe", "nxos", "junos"]
_DEFAULT_TRANSPORT: dict[str, str] = {
    "eos": "https",
    "iosxe": "https",
    "nxos": "https",
    "junos": "netconf",
}


# ---------------------------------------------------------------------------
# Low-level prompt helpers
# ---------------------------------------------------------------------------


def _prompt(message: str, default: str = "") -> str:
    """Prompt for text input with an optional default."""
    hint = f" [{default}]" if default else ""
    response = input(f"{message}{hint}: ").strip()
    return response if response else default


def _prompt_bool(message: str, default: bool = True) -> bool:
    """Prompt for a yes/no answer."""
    hint = "Y/n" if default else "y/N"
    response = input(f"{message} [{hint}]: ").strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


def _prompt_choice(message: str, choices: list[str], default: str = "") -> str:
    """Prompt until the user enters a valid choice from *choices*."""
    opts = "/".join(choices)
    hint = f"[{opts}]" + (f" (default: {default})" if default else "")
    while True:
        response = input(f"{message} {hint}: ").strip().lower()
        if not response and default:
            return default
        if response in choices:
            return response
        print(f"  Invalid choice. Please choose from: {', '.join(choices)}")  # noqa: T201


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------


def _collect_devices() -> list[dict[str, Any]]:
    """Interactively build the list of device entries."""
    devices: list[dict[str, Any]] = []
    print("\n=== Step 1: Network Devices ===")  # noqa: T201
    print("Add the devices you want network-mcp to manage.")  # noqa: T201
    print("Press Enter with an empty device name to finish.\n")  # noqa: T201

    while True:
        idx = len(devices) + 1
        print(f"Device #{idx}:")  # noqa: T201

        name = _prompt("  Name (e.g., spine-01)")
        if not name:
            if not devices:
                print("  At least one device is required.")  # noqa: T201
                continue
            break

        host = _prompt("  IP address or hostname")
        if not host:
            print("  Host/IP is required.")  # noqa: T201
            continue

        plat = _prompt_choice("  Platform", _PLATFORMS, default="eos")
        transport = _prompt("  Transport", default=_DEFAULT_TRANSPORT[plat])
        username = _prompt("  Username", default="admin")
        password = _prompt("  Password (blank = use NET_PASSWORD env var)")

        cfg: dict[str, Any] = {"host": host, "platform": plat, "transport": transport}
        if username and username != "admin":
            cfg["username"] = username
        if password:
            cfg["password"] = password

        devices.append({"name": name, "config": cfg})
        print(f"  Added: {name} ({host}, {plat})")  # noqa: T201

        if not _prompt_bool("\n  Add another device?", default=False):
            break

    return devices


def _collect_env_settings() -> dict[str, str]:
    """Interactively collect the NET_* settings for the .env file."""
    print("\n=== Step 2: Environment Settings ===")  # noqa: T201

    username = _prompt("Default device username (NET_USERNAME)", default="admin")
    password = _prompt("Default device password (NET_PASSWORD, blank to set later)")
    read_only = _prompt_bool("Enable read-only mode? (NET_READ_ONLY, safer)", default=True)

    return {
        "NET_USERNAME": username,
        "NET_PASSWORD": password,
        "NET_READ_ONLY": "true" if read_only else "false",
        "NET_TRANSPORT": "https",
        "NET_DEMO_MODE": "false",
    }


def _select_mcp_client() -> str | None:
    """Prompt the user to pick an MCP client to configure."""
    print("\n=== Step 3: MCP Client Configuration ===")  # noqa: T201
    print("Which MCP client would you like to configure?")  # noqa: T201
    print("  1. Claude Desktop")  # noqa: T201
    print("  2. Cursor")  # noqa: T201
    print("  3. VS Code")  # noqa: T201
    print("  4. Skip")  # noqa: T201

    while True:
        choice = input("Choice [1-4] (default: 4): ").strip()
        if not choice or choice == "4":
            return None
        if choice == "1":
            return "claude-desktop"
        if choice == "2":
            return "cursor"
        if choice == "3":
            return "vscode"
        print("  Please enter 1, 2, 3, or 4.")  # noqa: T201


# ---------------------------------------------------------------------------
# File-writing helpers
# ---------------------------------------------------------------------------


def _get_mcp_client_config_path(client: str) -> Path | None:
    """Return the MCP client config file path for the current OS."""
    plat = sys.platform
    if client == "claude-desktop":
        if plat == "darwin":
            return Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
        if plat == "linux":
            return Path("~/.config/claude/claude_desktop_config.json").expanduser()
        if plat == "win32":
            return Path("~/AppData/Roaming/Claude/claude_desktop_config.json").expanduser()
        return None
    if client == "cursor":
        return Path("~/.cursor/mcp.json").expanduser()
    if client == "vscode":
        return Path(".vscode/mcp.json")
    return None


def _write_devices_yaml(devices: list[dict[str, Any]], path: Path) -> None:
    """Write a devices.yaml inventory file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"devices": {d["name"]: d["config"] for d in devices}}
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def _write_env_file(settings: dict[str, str], path: Path) -> None:
    """Write a .env file from *settings*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# network-mcp configuration — generated by latticio init",
        "",
        *[f"{k}={v}" for k, v in settings.items()],
        "",
    ]
    path.write_text("\n".join(lines))


def _update_mcp_client_config(client: str, config_path: Path, devices_file: Path) -> None:
    """Write or merge the network-mcp entry into the MCP client config file.

    - Claude Desktop & Cursor use ``mcpServers`` as the top-level key.
    - VS Code uses ``servers`` with an explicit ``"type": "stdio"`` field.

    Existing entries in the config file are preserved; only ``network-mcp`` is
    added or overwritten.
    """
    abs_inventory = str(devices_file.resolve())
    server_entry: dict[str, Any] = {
        "command": "uvx",
        "args": ["--from", "latticio", "network-mcp"],
        "env": {"NET_INVENTORY_FILE": abs_inventory},
    }
    if client == "vscode":
        server_entry["type"] = "stdio"

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    key = "servers" if client == "vscode" else "mcpServers"
    if key not in existing:
        existing[key] = {}
    existing[key]["network-mcp"] = server_entry

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------


def run_init_wizard() -> int:
    """Run the interactive onboarding wizard. Returns a process exit code."""
    print("Welcome to network-mcp!")  # noqa: T201
    print("This wizard scaffolds your devices.yaml, .env, and MCP client config.\n")  # noqa: T201

    # Step 1 – devices
    devices = _collect_devices()
    if not devices:
        print("\nNo devices configured. Exiting.")  # noqa: T201
        return 1

    # Step 2 – env settings
    env_settings = _collect_env_settings()

    # Step 3 – MCP client
    mcp_client = _select_mcp_client()

    # Step 4 – output paths
    print("\n=== Output Files ===")  # noqa: T201
    devices_path = Path(_prompt("devices.yaml path", default="devices.yaml"))
    env_path = Path(_prompt(".env path", default=".env"))

    # Finalise env settings with the resolved inventory path
    env_settings["NET_INVENTORY_FILE"] = str(devices_path)

    _write_devices_yaml(devices, devices_path)
    print(f"\n  \u2713 {devices_path} written ({len(devices)} device(s))")  # noqa: T201

    _write_env_file(env_settings, env_path)
    print(f"  \u2713 {env_path} written")  # noqa: T201

    if mcp_client:
        client_config_path = _get_mcp_client_config_path(mcp_client)
        if client_config_path:
            _update_mcp_client_config(mcp_client, client_config_path, devices_path)
            print(f"  \u2713 {client_config_path} updated")  # noqa: T201
        else:
            print(f"  \u2717 Could not determine config path for {mcp_client} on {sys.platform}")  # noqa: T201

    print("\nSetup complete!")  # noqa: T201
    print("Start the server with:  uvx --from latticio network-mcp")  # noqa: T201
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the ``latticio`` CLI command.

    Dispatches subcommands:
    - ``latticio init`` — interactive onboarding wizard
    - ``latticio playground`` / ``latticio repl`` — interactive tool REPL
    - everything else — forwarded to the MCP server
    """
    args = sys.argv[1:]
    if args and args[0] == "init":
        sys.exit(run_init_wizard())

    if args and args[0] in ("playground", "repl"):
        from network_mcp.playground import main as _playground_main  # noqa: PLC0415

        _playground_main()
        return

    # Lazy import: avoids triggering heavy module-level server initialisation
    # when the user runs `latticio init`.
    from network_mcp.server import main as _server_main  # noqa: PLC0415

    _server_main()
