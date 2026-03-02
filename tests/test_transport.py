"""Tests for CLI argument parsing and server configuration."""

import pytest

from network_mcp.server import _parse_args


class TestParseArgs:
    """Tests for _parse_args() argparse-based CLI parsing."""

    def test_default_stdio(self):
        """Default transport is stdio when no arguments given."""
        args = _parse_args([])
        assert args.transport == "stdio"

    def test_transport_streamable_http(self):
        """--transport streamable-http is parsed correctly."""
        args = _parse_args(["--transport", "streamable-http"])
        assert args.transport == "streamable-http"

    def test_transport_sse(self):
        """--transport sse is parsed correctly."""
        args = _parse_args(["--transport", "sse"])
        assert args.transport == "sse"

    def test_transport_stdio_explicit(self):
        """--transport stdio is parsed correctly."""
        args = _parse_args(["--transport", "stdio"])
        assert args.transport == "stdio"

    def test_invalid_transport_rejected(self):
        """Invalid transport value is rejected by argparse."""
        with pytest.raises(SystemExit):
            _parse_args(["--transport", "grpc"])

    def test_default_host(self):
        """Default host is 0.0.0.0."""
        args = _parse_args([])
        assert args.host == "0.0.0.0"  # noqa: S104

    def test_custom_host(self):
        """--host flag is parsed correctly."""
        args = _parse_args(["--host", "127.0.0.1"])
        assert args.host == "127.0.0.1"

    def test_default_port(self):
        """Default port is 8000."""
        args = _parse_args([])
        assert args.port == 8000

    def test_custom_port(self):
        """--port flag is parsed correctly."""
        args = _parse_args(["--port", "9000"])
        assert args.port == 9000

    def test_invalid_port_rejected(self):
        """Non-integer port value is rejected."""
        with pytest.raises(SystemExit):
            _parse_args(["--port", "abc"])

    def test_all_args_combined(self):
        """All arguments can be specified together."""
        args = _parse_args(["--transport", "streamable-http", "--host", "127.0.0.1", "--port", "9000"])
        assert args.transport == "streamable-http"
        assert args.host == "127.0.0.1"
        assert args.port == 9000

    def test_transport_equals_format(self):
        """--transport=value format is supported by argparse."""
        args = _parse_args(["--transport=streamable-http"])
        assert args.transport == "streamable-http"

    def test_port_equals_format(self):
        """--port=value format is supported by argparse."""
        args = _parse_args(["--port=9000"])
        assert args.port == 9000
