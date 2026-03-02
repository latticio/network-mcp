"""Tests to close coverage gaps in network_mcp/tools/validation.py.

Covers: ANTA import branch (when not available), exception handlers in
eos_validate_mlag, eos_validate_interfaces exception branches, and
host validation for eos_validate_interfaces and eos_validate_mlag.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Exception handlers for eos_validate_mlag (lines 191-192)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_mlag_exception():
    """Test that exceptions in ANTA execution are caught in eos_validate_mlag."""
    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
    ):
        MockCatalog.from_dict.side_effect = RuntimeError("MLAG catalog failed")
        from network_mcp.tools.validation import eos_validate_mlag

        result = await eos_validate_mlag("test-switch")

    assert result["status"] == "error"
    assert "ANTA validation failed" in result["error"]
    assert "MLAG catalog failed" in result["error"]


# ---------------------------------------------------------------------------
# Exception handlers for eos_validate_interfaces (lines 227-228)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_interfaces_exception():
    """Test that exceptions in ANTA execution are caught in eos_validate_interfaces."""
    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
    ):
        MockCatalog.from_dict.side_effect = RuntimeError("Interface catalog failed")
        from network_mcp.tools.validation import eos_validate_interfaces

        result = await eos_validate_interfaces("test-switch")

    assert result["status"] == "error"
    assert "ANTA validation failed" in result["error"]
    assert "Interface catalog failed" in result["error"]


# ---------------------------------------------------------------------------
# Exception handlers for eos_validate_bgp (lines 156-157)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_bgp_exception():
    """Test that exceptions in ANTA execution are caught in eos_validate_bgp."""
    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
    ):
        MockCatalog.from_dict.side_effect = RuntimeError("BGP catalog failed")
        from network_mcp.tools.validation import eos_validate_bgp

        result = await eos_validate_bgp("test-switch")

    assert result["status"] == "error"
    assert "ANTA validation failed" in result["error"]


# ---------------------------------------------------------------------------
# Host validation for eos_validate_mlag (lines 185-186)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_mlag_invalid_host():
    """Test eos_validate_mlag with an invalid host."""
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
        from network_mcp.tools.validation import eos_validate_mlag

        result = await eos_validate_mlag("")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Host validation for eos_validate_interfaces (lines 221-222)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_interfaces_invalid_host():
    """Test eos_validate_interfaces with an invalid host."""
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
        from network_mcp.tools.validation import eos_validate_interfaces

        result = await eos_validate_interfaces("")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Host validation for eos_validate_system (lines 261-262)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_system_invalid_host():
    """Test eos_validate_system with an invalid host."""
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
        from network_mcp.tools.validation import eos_validate_system

        result = await eos_validate_system("")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Host validation for eos_run_anta_tests (lines 295-297)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_anta_tests_invalid_host_empty():
    """Test eos_run_anta_tests with an empty host."""
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
        from network_mcp.tools.validation import eos_run_anta_tests

        result = await eos_run_anta_tests("", "anta.tests.system:\n  - VerifyNTP:")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# _run_anta_catalog function body (lines 67-111) with mocked ANTA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_anta_catalog_function():
    """Test the _run_anta_catalog function directly with mocked ANTA classes."""
    # Create mocks for all ANTA components
    mock_device = MagicMock()
    mock_device.established = True

    mock_inventory = MagicMock()
    mock_inventory.connect_inventory = AsyncMock()

    mock_catalog = MagicMock()

    # Mock result objects
    mock_result1 = MagicMock()
    mock_result1.test = "VerifyNTP"
    mock_result1.result = "success"
    mock_result1.messages = []
    mock_result1.description = "NTP check"

    mock_result2 = MagicMock()
    mock_result2.test = "VerifyCPU"
    mock_result2.result = "failure"
    mock_result2.messages = ["CPU high"]
    mock_result2.description = "CPU check"

    mock_result_mgr = MagicMock()
    mock_result_mgr.results = [mock_result1, mock_result2]

    # Import and patch everything
    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AsyncEOSDevice", create=True, return_value=mock_device),
        patch("network_mcp.tools.validation.AntaInventory", create=True, return_value=mock_inventory),
        patch("network_mcp.tools.validation.ResultManager", create=True, return_value=mock_result_mgr),
        patch("network_mcp.tools.validation.anta_runner", create=True, new_callable=AsyncMock),
    ):
        from network_mcp.tools.validation import _run_anta_catalog

        result = await _run_anta_catalog("test-switch", mock_catalog)

    assert result["status"] == "success"
    assert result["data"]["summary"]["total"] == 2
    assert result["data"]["summary"]["passed"] == 1
    assert result["data"]["summary"]["failed"] == 1


@pytest.mark.asyncio
async def test_run_anta_catalog_device_not_established():
    """Test _run_anta_catalog when the device cannot be connected."""
    mock_device = MagicMock()
    mock_device.established = False

    mock_inventory = MagicMock()
    mock_inventory.connect_inventory = AsyncMock()

    mock_catalog = MagicMock()

    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AsyncEOSDevice", create=True, return_value=mock_device),
        patch("network_mcp.tools.validation.AntaInventory", create=True, return_value=mock_inventory),
    ):
        from network_mcp.tools.validation import _run_anta_catalog

        result = await _run_anta_catalog("unreachable-switch", mock_catalog)

    assert result["status"] == "error"
    assert "Could not connect" in result["error"]
