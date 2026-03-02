"""Tests for version string consistency across the codebase."""

import subprocess


def test_version_strings_consistent():
    """Verify observability returns the same version as __init__."""
    from network_mcp import __version__
    from network_mcp.observability import get_version

    ver = get_version()
    assert ver == __version__ or ver.endswith("-dev")


def test_no_stale_version_references():
    """No hardcoded '3.2.0' version references should exist in src/ (excluding mock data and caches)."""
    result = subprocess.run(
        ["/usr/bin/grep", "-rn", "--include=*.py", '"3.2.0"', "src/network_mcp/"],
        capture_output=True,
        text=True,
    )
    # Filter out mock data which may contain numeric coincidences
    lines = [line for line in result.stdout.strip().splitlines() if line and "mock_data/" not in line]
    assert lines == [], "Found stale version references:\n" + "\n".join(lines)
