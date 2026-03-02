"""Memory profiling tests for connection pool and inventory scaling.

These are informational tests that measure memory usage at different scales.
They use tracemalloc to track allocations and log results for analysis.
"""

import logging
import tracemalloc

import pytest
from pydantic import SecretStr

from network_mcp.connection import ConnectionManager, DeviceCredentials
from tests.conftest import make_test_settings

logger = logging.getLogger(__name__)


def _create_mock_inventory(size: int) -> dict[str, DeviceCredentials]:
    """Create a mock inventory of the given size."""
    return {
        f"device-{i:04d}": DeviceCredentials(
            host=f"10.{i // 256}.{i % 256}.1",
            username="admin",
            password=SecretStr("test"),
            transport="https",
            platform="eos",
        )
        for i in range(size)
    }


def test_memory_per_connection():
    """Measure memory overhead per device connection entry in the pool.

    Creates connection pool entries for N devices (without real connections)
    and measures memory before and after. Asserts memory per device is under 50KB.
    """
    tracemalloc.start()

    settings = make_test_settings()
    mgr = ConnectionManager(settings=settings, max_connections=500)

    # Baseline memory snapshot
    snapshot_before = tracemalloc.take_snapshot()
    before_size, _ = tracemalloc.get_traced_memory()

    # Populate inventory with 100 devices
    device_count = 100
    mgr._inventory = _create_mock_inventory(device_count)

    snapshot_after = tracemalloc.take_snapshot()
    after_size, _ = tracemalloc.get_traced_memory()

    delta_bytes = after_size - before_size
    per_device_bytes = delta_bytes / device_count if device_count > 0 else 0
    per_device_kb = per_device_bytes / 1024

    # Log results for analysis
    logger.info("Memory profiling: %d devices", device_count)
    logger.info("  Total delta: %d bytes (%.1f KB)", delta_bytes, delta_bytes / 1024)
    logger.info("  Per device: %.1f bytes (%.2f KB)", per_device_bytes, per_device_kb)

    # Print top memory consumers for debugging
    top_stats = snapshot_after.compare_to(snapshot_before, "lineno")
    logger.info("  Top 5 memory consumers:")
    for stat in top_stats[:5]:
        logger.info("    %s", stat)

    tracemalloc.stop()

    # Assert per-device overhead is under 50KB
    assert per_device_kb < 50, f"Per-device memory {per_device_kb:.1f} KB exceeds 50 KB threshold"


def test_inventory_scaling():
    """Test that inventory loading scales linearly with device count.

    Creates inventories of increasing sizes and verifies that per-device
    memory remains stable (no quadratic blowup).
    """
    sizes = [10, 100, 1000]
    results: list[dict] = []

    for size in sizes:
        tracemalloc.start()
        before_size, _ = tracemalloc.get_traced_memory()

        settings = make_test_settings()
        mgr = ConnectionManager(settings=settings, max_connections=max(size * 2, 100))
        mgr._inventory = _create_mock_inventory(size)

        after_size, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        delta_bytes = after_size - before_size
        per_device_bytes = delta_bytes / size

        results.append(
            {
                "size": size,
                "total_kb": delta_bytes / 1024,
                "per_device_bytes": per_device_bytes,
            }
        )

        logger.info(
            "Inventory scaling: %4d devices -> %.1f KB total, %.0f bytes/device",
            size,
            delta_bytes / 1024,
            per_device_bytes,
        )

    # Verify per-device cost doesn't grow significantly with scale
    # The largest inventory's per-device cost should be within 5x of the smallest
    smallest_per_device = results[0]["per_device_bytes"]
    largest_per_device = results[-1]["per_device_bytes"]

    if smallest_per_device > 0:
        ratio = largest_per_device / smallest_per_device
        logger.info("Scaling ratio (1000/10): %.1fx per-device memory", ratio)
        assert ratio < 5, (
            f"Per-device memory grew {ratio:.1f}x from {sizes[0]} to {sizes[-1]} devices — possible quadratic scaling"
        )


def test_connection_manager_base_memory():
    """Measure base memory of an empty ConnectionManager."""
    tracemalloc.start()
    before_size, _ = tracemalloc.get_traced_memory()

    settings = make_test_settings()
    mgr = ConnectionManager(settings=settings)  # noqa: F841

    after_size, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    base_kb = (after_size - before_size) / 1024
    logger.info("ConnectionManager base memory: %.1f KB", base_kb)

    # Base ConnectionManager should be under 100KB
    assert base_kb < 100, f"Base ConnectionManager uses {base_kb:.1f} KB, exceeds 100 KB"


@pytest.mark.parametrize("size", [10, 50, 100])
def test_device_credentials_memory(size):
    """Measure memory per DeviceCredentials object at different scales."""
    tracemalloc.start()
    before_size, _ = tracemalloc.get_traced_memory()

    creds = [
        DeviceCredentials(
            host=f"10.0.{i // 256}.{i % 256}",
            username="admin",
            password=SecretStr("testpass"),
            platform="eos",
        )
        for i in range(size)
    ]

    after_size, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    delta_bytes = after_size - before_size
    per_cred_bytes = delta_bytes / size

    logger.info(
        "DeviceCredentials memory: %d objects -> %.1f KB total, %.0f bytes/object",
        size,
        delta_bytes / 1024,
        per_cred_bytes,
    )

    # Keep reference to prevent GC
    assert len(creds) == size

    # Each DeviceCredentials should be under 10KB
    assert per_cred_bytes < 10240, f"Per-credential memory {per_cred_bytes:.0f} bytes exceeds 10 KB"
