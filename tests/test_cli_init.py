"""Tests for the ``latticio init`` interactive onboarding wizard."""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from network_mcp.cli import (
    _collect_devices,
    _collect_env_settings,
    _get_mcp_client_config_path,
    _prompt,
    _prompt_bool,
    _prompt_choice,
    _select_mcp_client,
    _update_mcp_client_config,
    _write_devices_yaml,
    _write_env_file,
    run_init_wizard,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_input(responses: list[str]):
    """Return an input() mock that yields *responses* in sequence."""
    it = iter(responses)

    def _mock_input(prompt: str = "") -> str:  # noqa: ARG001
        return next(it)

    return _mock_input


# ---------------------------------------------------------------------------
# _prompt
# ---------------------------------------------------------------------------


def test_prompt_returns_user_input(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["my-value"]))
    assert _prompt("Enter something") == "my-value"


def test_prompt_returns_default_on_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input([""]))
    assert _prompt("Enter something", default="fallback") == "fallback"


def test_prompt_strips_whitespace(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["  hello  "]))
    assert _prompt("Enter") == "hello"


# ---------------------------------------------------------------------------
# _prompt_bool
# ---------------------------------------------------------------------------


def test_prompt_bool_yes(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["y"]))
    assert _prompt_bool("Continue?") is True


def test_prompt_bool_no(monkeypatch):
    # 'n' always means False regardless of default
    monkeypatch.setattr("builtins.input", _make_input(["n"]))
    assert _prompt_bool("Continue?", default=True) is False

    monkeypatch.setattr("builtins.input", _make_input(["n"]))
    assert _prompt_bool("Continue?", default=False) is False


def test_prompt_bool_default_true_on_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input([""]))
    assert _prompt_bool("Continue?", default=True) is True


def test_prompt_bool_default_false_on_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input([""]))
    assert _prompt_bool("Continue?", default=False) is False


def test_prompt_bool_yes_string(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["yes"]))
    assert _prompt_bool("Continue?") is True


# ---------------------------------------------------------------------------
# _prompt_choice
# ---------------------------------------------------------------------------


def test_prompt_choice_valid(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["eos"]))
    assert _prompt_choice("Platform", ["eos", "iosxe", "nxos", "junos"]) == "eos"


def test_prompt_choice_default_on_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["", ""]))
    assert _prompt_choice("Platform", ["eos", "iosxe"], default="eos") == "eos"


def test_prompt_choice_invalid_then_valid(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", _make_input(["bad", "nxos"]))
    result = _prompt_choice("Platform", ["eos", "iosxe", "nxos"], default="eos")
    assert result == "nxos"
    out = capsys.readouterr().out
    assert "Invalid choice" in out


def test_prompt_choice_case_insensitive(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["EOS"]))
    assert _prompt_choice("Platform", ["eos", "iosxe"], default="eos") == "eos"


# ---------------------------------------------------------------------------
# _collect_devices
# ---------------------------------------------------------------------------


def test_collect_devices_single(monkeypatch):
    responses = [
        "spine-01",   # name
        "10.0.0.1",   # host
        "eos",        # platform
        "https",      # transport
        "admin",      # username
        "",           # password (blank)
        "n",          # add another?
    ]
    monkeypatch.setattr("builtins.input", _make_input(responses))
    devices = _collect_devices()
    assert len(devices) == 1
    d = devices[0]
    assert d["name"] == "spine-01"
    assert d["config"]["host"] == "10.0.0.1"
    assert d["config"]["platform"] == "eos"
    assert d["config"]["transport"] == "https"
    assert "username" not in d["config"]  # default admin omitted
    assert "password" not in d["config"]


def test_collect_devices_with_credentials(monkeypatch):
    responses = [
        "leaf-01",
        "192.168.1.10",
        "iosxe",
        "https",
        "netops",    # non-default username → included
        "s3cr3t",    # password → included
        "n",
    ]
    monkeypatch.setattr("builtins.input", _make_input(responses))
    devices = _collect_devices()
    cfg = devices[0]["config"]
    assert cfg["username"] == "netops"
    assert cfg["password"] == "s3cr3t"


def test_collect_devices_multiple(monkeypatch):
    responses = [
        "spine-01", "10.0.0.1", "eos", "https", "admin", "", "y",
        "leaf-01",  "10.0.1.1", "eos", "https", "admin", "", "n",
    ]
    monkeypatch.setattr("builtins.input", _make_input(responses))
    devices = _collect_devices()
    assert len(devices) == 2
    assert devices[0]["name"] == "spine-01"
    assert devices[1]["name"] == "leaf-01"


def test_collect_devices_requires_at_least_one(monkeypatch):
    """Entering an empty name with no devices should re-prompt."""
    responses = [
        "",           # empty name — rejected, re-prompts
        "spine-01",
        "10.0.0.1",
        "eos",
        "https",
        "admin",
        "",
        "n",
    ]
    monkeypatch.setattr("builtins.input", _make_input(responses))
    devices = _collect_devices()
    assert len(devices) == 1


def test_collect_devices_junos_default_transport(monkeypatch):
    responses = [
        "edge-rtr-01",
        "10.0.0.30",
        "junos",
        "netconf",  # default for junos
        "admin",
        "",
        "n",
    ]
    monkeypatch.setattr("builtins.input", _make_input(responses))
    devices = _collect_devices()
    assert devices[0]["config"]["transport"] == "netconf"


# ---------------------------------------------------------------------------
# _collect_env_settings
# ---------------------------------------------------------------------------


def test_collect_env_settings_defaults(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["admin", "", "y"]))
    settings = _collect_env_settings()
    assert settings["NET_USERNAME"] == "admin"
    assert settings["NET_PASSWORD"] == ""
    assert settings["NET_READ_ONLY"] == "true"
    assert settings["NET_DEMO_MODE"] == "false"
    assert settings["NET_TRANSPORT"] == "https"


def test_collect_env_settings_read_write_mode(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["admin", "pass123", "n"]))
    settings = _collect_env_settings()
    assert settings["NET_READ_ONLY"] == "false"
    assert settings["NET_PASSWORD"] == "pass123"


# ---------------------------------------------------------------------------
# _select_mcp_client
# ---------------------------------------------------------------------------


def test_select_mcp_client_skip(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["4"]))
    assert _select_mcp_client() is None


def test_select_mcp_client_default_skip(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input([""]))
    assert _select_mcp_client() is None


def test_select_mcp_client_claude_desktop(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["1"]))
    assert _select_mcp_client() == "claude-desktop"


def test_select_mcp_client_cursor(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["2"]))
    assert _select_mcp_client() == "cursor"


def test_select_mcp_client_vscode(monkeypatch):
    monkeypatch.setattr("builtins.input", _make_input(["3"]))
    assert _select_mcp_client() == "vscode"


def test_select_mcp_client_invalid_then_valid(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", _make_input(["9", "2"]))
    result = _select_mcp_client()
    assert result == "cursor"
    out = capsys.readouterr().out
    assert "Please enter 1, 2, 3, or 4" in out


# ---------------------------------------------------------------------------
# _get_mcp_client_config_path
# ---------------------------------------------------------------------------


def test_get_config_path_claude_desktop_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    path = _get_mcp_client_config_path("claude-desktop")
    assert path is not None
    assert "Claude" in str(path)
    assert path.name == "claude_desktop_config.json"


def test_get_config_path_claude_desktop_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    path = _get_mcp_client_config_path("claude-desktop")
    assert path is not None
    assert "claude" in str(path).lower()


def test_get_config_path_claude_desktop_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    path = _get_mcp_client_config_path("claude-desktop")
    assert path is not None
    assert "Claude" in str(path)


def test_get_config_path_claude_desktop_unknown_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "freebsd")
    path = _get_mcp_client_config_path("claude-desktop")
    assert path is None


def test_get_config_path_cursor(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    path = _get_mcp_client_config_path("cursor")
    assert path is not None
    assert path.name == "mcp.json"
    assert ".cursor" in str(path)


def test_get_config_path_vscode():
    path = _get_mcp_client_config_path("vscode")
    assert path is not None
    assert str(path) == ".vscode/mcp.json"


def test_get_config_path_unknown_client():
    assert _get_mcp_client_config_path("unknown-client") is None


# ---------------------------------------------------------------------------
# _write_devices_yaml
# ---------------------------------------------------------------------------


def test_write_devices_yaml(tmp_path):
    devices: list[dict[str, Any]] = [
        {"name": "spine-01", "config": {"host": "10.0.0.1", "platform": "eos", "transport": "https"}},
        {"name": "leaf-01", "config": {"host": "10.0.1.1", "platform": "iosxe", "transport": "https"}},
    ]
    out = tmp_path / "devices.yaml"
    _write_devices_yaml(devices, out)

    parsed = yaml.safe_load(out.read_text())
    assert "devices" in parsed
    assert "spine-01" in parsed["devices"]
    assert parsed["devices"]["spine-01"]["host"] == "10.0.0.1"
    assert parsed["devices"]["leaf-01"]["platform"] == "iosxe"


def test_write_devices_yaml_single_device(tmp_path):
    devices = [{"name": "router-01", "config": {"host": "10.0.0.10", "platform": "junos", "transport": "netconf"}}]
    out = tmp_path / "devices.yaml"
    _write_devices_yaml(devices, out)

    parsed = yaml.safe_load(out.read_text())
    assert len(parsed["devices"]) == 1
    assert parsed["devices"]["router-01"]["platform"] == "junos"


# ---------------------------------------------------------------------------
# _write_env_file
# ---------------------------------------------------------------------------


def test_write_env_file(tmp_path):
    settings = {
        "NET_USERNAME": "admin",
        "NET_PASSWORD": "s3cr3t",
        "NET_READ_ONLY": "true",
        "NET_INVENTORY_FILE": "devices.yaml",
    }
    out = tmp_path / ".env"
    _write_env_file(settings, out)

    content = out.read_text()
    assert "NET_USERNAME=admin" in content
    assert "NET_PASSWORD=s3cr3t" in content
    assert "NET_READ_ONLY=true" in content
    assert "NET_INVENTORY_FILE=devices.yaml" in content
    assert "# network-mcp configuration" in content


def test_write_env_file_empty_password(tmp_path):
    settings = {"NET_USERNAME": "admin", "NET_PASSWORD": "", "NET_READ_ONLY": "true"}
    out = tmp_path / ".env"
    _write_env_file(settings, out)
    assert "NET_PASSWORD=" in out.read_text()


# ---------------------------------------------------------------------------
# _update_mcp_client_config
# ---------------------------------------------------------------------------


def test_update_mcp_client_config_claude_desktop(tmp_path):
    config_path = tmp_path / "claude_desktop_config.json"
    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text("devices: {}")

    _update_mcp_client_config("claude-desktop", config_path, devices_file)

    data = json.loads(config_path.read_text())
    assert "mcpServers" in data
    entry = data["mcpServers"]["network-mcp"]
    assert entry["command"] == "uvx"
    assert "--from" in entry["args"]
    assert "latticio" in entry["args"]
    assert "network-mcp" in entry["args"]
    assert "NET_INVENTORY_FILE" in entry["env"]


def test_update_mcp_client_config_cursor(tmp_path):
    config_path = tmp_path / "mcp.json"
    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text("devices: {}")

    _update_mcp_client_config("cursor", config_path, devices_file)

    data = json.loads(config_path.read_text())
    assert "mcpServers" in data
    assert "network-mcp" in data["mcpServers"]


def test_update_mcp_client_config_vscode(tmp_path):
    config_path = tmp_path / ".vscode" / "mcp.json"
    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text("devices: {}")

    _update_mcp_client_config("vscode", config_path, devices_file)

    data = json.loads(config_path.read_text())
    assert "servers" in data
    entry = data["servers"]["network-mcp"]
    assert entry["type"] == "stdio"


def test_update_mcp_client_config_merges_existing(tmp_path):
    config_path = tmp_path / "claude_desktop_config.json"
    existing = {"mcpServers": {"other-server": {"command": "other", "args": []}}}
    config_path.write_text(json.dumps(existing))

    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text("devices: {}")

    _update_mcp_client_config("claude-desktop", config_path, devices_file)

    data = json.loads(config_path.read_text())
    assert "other-server" in data["mcpServers"]
    assert "network-mcp" in data["mcpServers"]


def test_update_mcp_client_config_handles_corrupt_json(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text("{ not valid json }")
    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text("devices: {}")

    _update_mcp_client_config("cursor", config_path, devices_file)

    data = json.loads(config_path.read_text())
    assert "network-mcp" in data["mcpServers"]


def test_update_mcp_client_config_creates_parent_dirs(tmp_path):
    config_path = tmp_path / "nested" / "deep" / "mcp.json"
    devices_file = tmp_path / "devices.yaml"
    devices_file.write_text("devices: {}")

    _update_mcp_client_config("cursor", config_path, devices_file)

    assert config_path.exists()


# ---------------------------------------------------------------------------
# run_init_wizard — integration
# ---------------------------------------------------------------------------


def _wizard_inputs(
    *,
    device_name: str = "spine-01",
    device_host: str = "10.0.0.1",
    platform: str = "eos",
    transport: str = "https",
    username: str = "admin",
    password: str = "",
    another: str = "n",
    env_username: str = "admin",
    env_password: str = "",
    read_only: str = "y",
    mcp_client: str = "4",
    devices_path: str = "devices.yaml",
    env_path: str = ".env",
) -> list[str]:
    return [
        device_name, device_host, platform, transport, username, password, another,
        env_username, env_password, read_only,
        mcp_client,
        devices_path, env_path,
    ]


def test_run_init_wizard_basic(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    inputs = _wizard_inputs()
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    rc = run_init_wizard()

    assert rc == 0
    assert (tmp_path / "devices.yaml").exists()
    assert (tmp_path / ".env").exists()

    parsed = yaml.safe_load((tmp_path / "devices.yaml").read_text())
    assert "spine-01" in parsed["devices"]

    env_content = (tmp_path / ".env").read_text()
    assert "NET_INVENTORY_FILE=devices.yaml" in env_content


def test_run_init_wizard_with_claude_desktop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake_config = tmp_path / "claude_desktop_config.json"

    inputs = _wizard_inputs(mcp_client="1")
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    with patch("network_mcp.cli._get_mcp_client_config_path", return_value=fake_config):
        rc = run_init_wizard()

    assert rc == 0
    assert fake_config.exists()
    data = json.loads(fake_config.read_text())
    assert "network-mcp" in data["mcpServers"]


def test_run_init_wizard_with_cursor(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake_config = tmp_path / "mcp.json"

    inputs = _wizard_inputs(mcp_client="2")
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    with patch("network_mcp.cli._get_mcp_client_config_path", return_value=fake_config):
        rc = run_init_wizard()

    assert rc == 0
    data = json.loads(fake_config.read_text())
    assert "network-mcp" in data["mcpServers"]


def test_run_init_wizard_with_vscode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake_config = tmp_path / ".vscode" / "mcp.json"

    inputs = _wizard_inputs(mcp_client="3")
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    with patch("network_mcp.cli._get_mcp_client_config_path", return_value=fake_config):
        rc = run_init_wizard()

    assert rc == 0
    data = json.loads(fake_config.read_text())
    assert "servers" in data


def test_run_init_wizard_custom_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    inputs = _wizard_inputs(devices_path="my-devices.yaml", env_path="prod.env")
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    rc = run_init_wizard()

    assert rc == 0
    assert (tmp_path / "my-devices.yaml").exists()
    assert (tmp_path / "prod.env").exists()


def test_run_init_wizard_multiple_devices(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    inputs = [
        # Device 1
        "spine-01", "10.0.0.1", "eos", "https", "admin", "", "y",
        # Device 2
        "leaf-01", "10.0.1.1", "nxos", "https", "admin", "", "n",
        # Env settings
        "admin", "", "y",
        # MCP client
        "4",
        # Paths
        "devices.yaml", ".env",
    ]
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    rc = run_init_wizard()

    assert rc == 0
    parsed = yaml.safe_load((tmp_path / "devices.yaml").read_text())
    assert len(parsed["devices"]) == 2
    assert "spine-01" in parsed["devices"]
    assert "leaf-01" in parsed["devices"]


def test_run_init_wizard_inventory_path_in_env(monkeypatch, tmp_path):
    """NET_INVENTORY_FILE in .env should match the chosen devices.yaml path."""
    monkeypatch.chdir(tmp_path)
    inputs = _wizard_inputs(devices_path="custom/devices.yaml")
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    rc = run_init_wizard()

    assert rc == 0
    env_content = (tmp_path / ".env").read_text()
    assert "NET_INVENTORY_FILE=custom/devices.yaml" in env_content


def test_run_init_wizard_unknown_client_path(monkeypatch, tmp_path, capsys):
    """When the client config path can't be resolved, print an error but succeed."""
    monkeypatch.chdir(tmp_path)
    inputs = _wizard_inputs(mcp_client="1")
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    with patch("network_mcp.cli._get_mcp_client_config_path", return_value=None):
        rc = run_init_wizard()

    assert rc == 0
    out = capsys.readouterr().out
    assert "Could not determine config path" in out


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------


def test_main_dispatches_to_init_wizard(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["latticio", "init"])

    inputs = _wizard_inputs()
    monkeypatch.setattr("builtins.input", _make_input(inputs))

    with pytest.raises(SystemExit) as exc_info:
        from network_mcp.cli import main

        main()

    assert exc_info.value.code == 0
