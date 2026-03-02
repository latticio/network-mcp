"""Tests for ANTA validation tools."""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.server import conn_mgr

# --- Helper to build mock ANTA result objects ---


def _make_mock_result(test_name: str, result: str, messages: list | None = None, description: str = ""):
    """Create a mock ANTA test result object."""
    mock = MagicMock()
    mock.test = test_name
    mock.result = result
    mock.messages = messages or []
    mock.description = description
    return mock


def _mock_run_anta_catalog_success(mock_results):
    """Create an AsyncMock for _run_anta_catalog that returns success with given results."""
    test_results = []
    for r in mock_results:
        test_results.append(
            {
                "test": r.test,
                "result": r.result,
                "messages": r.messages,
                "description": r.description,
            }
        )

    passed = sum(1 for r in test_results if r["result"] == "success")
    failed = sum(1 for r in test_results if r["result"] == "failure")
    skipped = sum(1 for r in test_results if r["result"] == "skipped")
    errored = sum(1 for r in test_results if r["result"] == "error")

    async def _mock(host, catalog):
        return {
            "status": "success",
            "device": host,
            "data": {
                "summary": {
                    "total": len(test_results),
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "errored": errored,
                },
                "results": test_results,
            },
        }

    return _mock


def _mock_run_anta_catalog_connection_failure():
    """Create an AsyncMock for _run_anta_catalog that returns connection failure."""

    async def _mock(host, catalog):
        return {
            "status": "error",
            "device": host,
            "error": f"Could not connect to device {host} via ANTA (asynceapi)",
        }

    return _mock


# --- ANTA not installed tests ---


@pytest.mark.asyncio
async def test_validate_bgp_anta_not_installed():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
        from network_mcp.tools.validation import eos_validate_bgp

        result = await eos_validate_bgp("test-switch")
    assert result["status"] == "error"
    assert "ANTA is not installed" in result["error"]
    assert "pip install latticio[anta]" in result["error"]


@pytest.mark.asyncio
async def test_validate_mlag_anta_not_installed():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
        from network_mcp.tools.validation import eos_validate_mlag

        result = await eos_validate_mlag("test-switch")
    assert result["status"] == "error"
    assert "ANTA is not installed" in result["error"]


@pytest.mark.asyncio
async def test_validate_interfaces_anta_not_installed():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
        from network_mcp.tools.validation import eos_validate_interfaces

        result = await eos_validate_interfaces("test-switch")
    assert result["status"] == "error"
    assert "ANTA is not installed" in result["error"]


@pytest.mark.asyncio
async def test_validate_system_anta_not_installed():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
        from network_mcp.tools.validation import eos_validate_system

        result = await eos_validate_system("test-switch")
    assert result["status"] == "error"
    assert "ANTA is not installed" in result["error"]


@pytest.mark.asyncio
async def test_run_anta_tests_anta_not_installed():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", False):
        from network_mcp.tools.validation import eos_run_anta_tests

        result = await eos_run_anta_tests("test-switch", "anta.tests.system:\n  - VerifyNTP:")
    assert result["status"] == "error"
    assert "ANTA is not installed" in result["error"]


# --- Host validation tests ---


@pytest.mark.asyncio
async def test_validate_bgp_invalid_host():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
        from network_mcp.tools.validation import eos_validate_bgp

        result = await eos_validate_bgp("")
    assert result["status"] == "error"
    assert "Host" in result["error"] or "host" in result["error"].lower()


@pytest.mark.asyncio
async def test_run_anta_tests_invalid_host():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
        from network_mcp.tools.validation import eos_run_anta_tests

        result = await eos_run_anta_tests("", "anta.tests.system:\n  - VerifyNTP:")
    assert result["status"] == "error"


# --- YAML parsing tests for run_anta_tests ---


@pytest.mark.asyncio
async def test_run_anta_tests_invalid_yaml():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
        from network_mcp.tools.validation import eos_run_anta_tests

        result = await eos_run_anta_tests("test-switch", "{{invalid yaml::")
    assert result["status"] == "error"
    assert "Invalid YAML" in result["error"]


@pytest.mark.asyncio
async def test_run_anta_tests_non_dict_yaml():
    with patch("network_mcp.tools.validation.ANTA_AVAILABLE", True):
        from network_mcp.tools.validation import eos_run_anta_tests

        result = await eos_run_anta_tests("test-switch", "- item1\n- item2")
    assert result["status"] == "error"
    assert "must be a mapping" in result["error"]


# --- Mocked ANTA execution tests ---
# These tests mock _run_anta_catalog directly to avoid needing ANTA installed.


@pytest.mark.asyncio
async def test_validate_bgp_success():
    """Test validate_bgp with mocked ANTA execution."""
    mock_results = [
        _make_mock_result("VerifyBGPPeersHealth", "success", description="BGP peers are healthy"),
    ]

    mock_fn = _mock_run_anta_catalog_success(mock_results)
    mock_catalog = MagicMock()

    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
        patch("network_mcp.tools.validation._run_anta_catalog", side_effect=mock_fn),
    ):
        MockCatalog.from_dict.return_value = mock_catalog
        from network_mcp.tools.validation import eos_validate_bgp

        result = await eos_validate_bgp("test-switch")

    assert result["status"] == "success"
    assert result["device"] == "test-switch"
    assert result["data"]["summary"]["total"] == 1
    assert result["data"]["summary"]["passed"] == 1
    assert result["data"]["summary"]["failed"] == 0
    assert result["data"]["results"][0]["test"] == "VerifyBGPPeersHealth"
    assert result["data"]["results"][0]["result"] == "success"


@pytest.mark.asyncio
async def test_validate_mlag_success():
    """Test validate_mlag with mocked ANTA execution."""
    mock_results = [
        _make_mock_result("VerifyMlagStatus", "success"),
        _make_mock_result("VerifyMlagInterfaces", "success"),
        _make_mock_result("VerifyMlagConfigSanity", "failure", messages=["Config mismatch detected"]),
    ]

    mock_fn = _mock_run_anta_catalog_success(mock_results)
    mock_catalog = MagicMock()

    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
        patch("network_mcp.tools.validation._run_anta_catalog", side_effect=mock_fn),
    ):
        MockCatalog.from_dict.return_value = mock_catalog
        from network_mcp.tools.validation import eos_validate_mlag

        result = await eos_validate_mlag("test-switch")

    assert result["status"] == "success"
    assert result["data"]["summary"]["total"] == 3
    assert result["data"]["summary"]["passed"] == 2
    assert result["data"]["summary"]["failed"] == 1
    assert result["data"]["results"][2]["messages"] == ["Config mismatch detected"]


@pytest.mark.asyncio
async def test_validate_interfaces_success():
    """Test validate_interfaces with mocked ANTA execution."""
    mock_results = [
        _make_mock_result("VerifyInterfaceUtilization", "success"),
        _make_mock_result("VerifyInterfaceErrors", "success"),
        _make_mock_result("VerifyInterfaceDiscards", "success"),
        _make_mock_result("VerifyInterfaceErrDisabled", "success"),
    ]

    mock_fn = _mock_run_anta_catalog_success(mock_results)
    mock_catalog = MagicMock()

    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
        patch("network_mcp.tools.validation._run_anta_catalog", side_effect=mock_fn),
    ):
        MockCatalog.from_dict.return_value = mock_catalog
        from network_mcp.tools.validation import eos_validate_interfaces

        result = await eos_validate_interfaces("test-switch")

    assert result["status"] == "success"
    assert result["data"]["summary"]["total"] == 4
    assert result["data"]["summary"]["passed"] == 4
    assert result["data"]["summary"]["failed"] == 0


@pytest.mark.asyncio
async def test_validate_system_success():
    """Test validate_system with mocked ANTA execution."""
    mock_results = [
        _make_mock_result("VerifyNTP", "success"),
        _make_mock_result("VerifyCPUUtilization", "success"),
        _make_mock_result("VerifyMemoryUtilization", "success"),
        _make_mock_result("VerifyFileSystemUtilization", "success"),
        _make_mock_result("VerifyCoredump", "success"),
        _make_mock_result("VerifyAgentLogs", "failure", messages=["Agent crash detected"]),
        _make_mock_result("VerifyReloadCause", "success"),
    ]

    mock_fn = _mock_run_anta_catalog_success(mock_results)
    mock_catalog = MagicMock()

    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
        patch("network_mcp.tools.validation._run_anta_catalog", side_effect=mock_fn),
    ):
        MockCatalog.from_dict.return_value = mock_catalog
        from network_mcp.tools.validation import eos_validate_system

        result = await eos_validate_system("test-switch")

    assert result["status"] == "success"
    assert result["data"]["summary"]["total"] == 7
    assert result["data"]["summary"]["passed"] == 6
    assert result["data"]["summary"]["failed"] == 1


@pytest.mark.asyncio
async def test_run_anta_tests_success():
    """Test run_anta_tests with a custom catalog."""
    mock_results = [
        _make_mock_result("VerifyNTP", "success"),
    ]

    mock_fn = _mock_run_anta_catalog_success(mock_results)
    mock_catalog = MagicMock()

    catalog_yaml = "anta.tests.system:\n  - VerifyNTP:\n"

    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
        patch("network_mcp.tools.validation._run_anta_catalog", side_effect=mock_fn),
    ):
        MockCatalog.from_dict.return_value = mock_catalog
        from network_mcp.tools.validation import eos_run_anta_tests

        result = await eos_run_anta_tests("test-switch", catalog_yaml)

    assert result["status"] == "success"
    assert result["data"]["summary"]["total"] == 1
    assert result["data"]["summary"]["passed"] == 1


# --- Connection failure tests ---


@pytest.mark.asyncio
async def test_validate_bgp_connection_failure():
    """Test validate_bgp when ANTA cannot connect to the device."""
    mock_fn = _mock_run_anta_catalog_connection_failure()
    mock_catalog = MagicMock()

    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
        patch("network_mcp.tools.validation._run_anta_catalog", side_effect=mock_fn),
    ):
        MockCatalog.from_dict.return_value = mock_catalog
        from network_mcp.tools.validation import eos_validate_bgp

        result = await eos_validate_bgp("unreachable-switch")

    assert result["status"] == "error"
    assert "Could not connect" in result["error"]
    assert result["device"] == "unreachable-switch"


# --- Exception handling tests ---


@pytest.mark.asyncio
async def test_validate_system_exception():
    """Test that exceptions in ANTA execution are caught and returned as errors."""
    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
    ):
        MockCatalog.from_dict.side_effect = RuntimeError("Catalog construction failed")
        from network_mcp.tools.validation import eos_validate_system

        result = await eos_validate_system("test-switch")

    assert result["status"] == "error"
    assert "ANTA validation failed" in result["error"]
    assert "Catalog construction failed" in result["error"]


@pytest.mark.asyncio
async def test_run_anta_tests_catalog_exception():
    """Test run_anta_tests when catalog construction raises an error."""
    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
    ):
        MockCatalog.from_dict.side_effect = ValueError("Invalid test definition")
        from network_mcp.tools.validation import eos_run_anta_tests

        result = await eos_run_anta_tests("test-switch", "anta.tests.bogus:\n  - FakeTest:\n")

    assert result["status"] == "error"
    assert "ANTA validation failed" in result["error"]


# --- Credential resolution tests ---


def test_get_device_credentials_from_inventory():
    """Test credential resolution from inventory."""
    from network_mcp.connection import DeviceCredentials
    from network_mcp.tools.validation import _get_device_credentials

    original = dict(conn_mgr._inventory)
    conn_mgr._inventory["spine-01"] = DeviceCredentials(
        host="10.0.0.1",
        username="testuser",
        password="testpass",
        transport="https",
        port=443,
    )
    try:
        creds = _get_device_credentials("spine-01")
        assert creds["host"] == "10.0.0.1"
        assert creds["username"] == "testuser"
        assert creds["password"] == "testpass"
        assert creds["port"] == 443
        assert creds["proto"] == "https"
    finally:
        conn_mgr._inventory.clear()
        conn_mgr._inventory.update(original)


def test_get_device_credentials_default():
    """Test credential resolution with default credentials."""
    from network_mcp.tools.validation import _get_device_credentials

    original = dict(conn_mgr._inventory)
    conn_mgr._inventory.clear()
    try:
        creds = _get_device_credentials("unknown-device")
        assert creds["host"] == "unknown-device"
        assert creds["username"] == conn_mgr._default_username
        assert creds["password"] == conn_mgr._default_password
    finally:
        conn_mgr._inventory.clear()
        conn_mgr._inventory.update(original)


def test_get_device_credentials_http_transport():
    """Test credential resolution with http transport."""
    from network_mcp.connection import DeviceCredentials
    from network_mcp.tools.validation import _get_device_credentials

    original = dict(conn_mgr._inventory)
    conn_mgr._inventory["lab-switch"] = DeviceCredentials(
        host="192.168.1.1",
        transport="http",
        port=80,
    )
    try:
        creds = _get_device_credentials("lab-switch")
        assert creds["proto"] == "http"
        assert creds["port"] == 80
    finally:
        conn_mgr._inventory.clear()
        conn_mgr._inventory.update(original)


# --- Mixed result types ---


@pytest.mark.asyncio
async def test_validate_with_skipped_and_errored_results():
    """Test result parsing handles skipped and errored test states."""
    mock_results = [
        _make_mock_result("VerifyNTP", "success"),
        _make_mock_result("VerifyCPUUtilization", "skipped", messages=["Test skipped: not applicable"]),
        _make_mock_result("VerifyMemoryUtilization", "error", messages=["Internal error"]),
    ]

    mock_fn = _mock_run_anta_catalog_success(mock_results)
    mock_catalog = MagicMock()

    with (
        patch("network_mcp.tools.validation.ANTA_AVAILABLE", True),
        patch("network_mcp.tools.validation.AntaCatalog", create=True) as MockCatalog,
        patch("network_mcp.tools.validation._run_anta_catalog", side_effect=mock_fn),
    ):
        MockCatalog.from_dict.return_value = mock_catalog
        from network_mcp.tools.validation import eos_validate_system

        result = await eos_validate_system("test-switch")

    assert result["status"] == "success"
    assert result["data"]["summary"]["passed"] == 1
    assert result["data"]["summary"]["skipped"] == 1
    assert result["data"]["summary"]["errored"] == 1
