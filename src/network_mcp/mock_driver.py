"""MockDriver for demo mode — zero-hardware evaluation.

Implements the DeviceDriver and NetworkDriver protocols using mock
response data. When NET_DEMO_MODE=true, ConnectionManager creates
MockDriver instances instead of real vendor drivers.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from network_mcp.mock_data.responses import get_response

logger = logging.getLogger("network-mcp")

# Error returned for commands that are not mocked
DEMO_MODE_ERROR = {"errors": ["Not available in demo mode. This tool requires a real EOS device."]}


class MockDriver:
    """Mock driver for demo/evaluation mode.

    Satisfies the DeviceDriver protocol. Dispatches show commands to
    a lookup table of realistic EOS JSON responses. Config commands
    succeed as no-ops.
    """

    platform: str = "eos-mock"

    def __init__(self) -> None:
        self._host: str | None = None
        self._connected: bool = False

    def connect(
        self, host: str, transport: str = "https", username: str = "admin", password: str = "", **kwargs: Any
    ) -> None:
        """Simulate connecting to a device."""
        self._host = host
        self._connected = True
        logger.debug("MockDriver connected to '%s' (demo mode)", host)

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Return mock responses for show commands.

        Mocked commands return realistic EOS data. Unmocked commands
        return an error dict with a helpful message.
        """
        if not self._connected or not self._host:
            raise ConnectionError("MockDriver is not connected. Call connect() first.")

        results: list[dict] = []
        for cmd in commands:
            response = get_response(self._host, cmd)
            if response is not None:
                # Deep copy to prevent mutation across calls
                results.append(copy.deepcopy(response))
            else:
                results.append(copy.deepcopy(DEMO_MODE_ERROR))
        return results

    # Backward-compatibility aliases (same as EosDriver)
    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility with pyeapi.Node interface."""
        return self.run_show(commands, encoding=encoding)

    def run_config(self, commands: list[str]) -> list[str]:
        """Config commands are no-ops in demo mode."""
        if not self._connected:
            raise ConnectionError("MockDriver is not connected. Call connect() first.")
        logger.debug("MockDriver config no-op on '%s': %s", self._host, commands)
        return ["Demo mode: config command accepted (no-op)" for _ in commands]

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility with pyeapi.Node interface."""
        return self.run_config(commands)

    def close(self) -> None:
        """Simulate closing the connection."""
        self._connected = False
        self._host = None

    def is_connected(self) -> bool:
        """Return True if the driver is currently connected."""
        return self._connected
