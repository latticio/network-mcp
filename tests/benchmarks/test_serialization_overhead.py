"""Serialization overhead benchmarks.

Measures JSON encoding/decoding costs for tool responses at various sizes.
This matters because MCP tools return JSON-serialized dicts over the
JSON-RPC transport, and large responses (e.g., full routing tables,
MAC address tables) can add significant serialization overhead.

Also measures Pydantic model validation overhead for structured output schemas.
"""

import json

import pytest

from network_mcp.schemas import (
    BgpSummaryOutput,
    DeviceFacts,
    DeviceInfoOutput,
    InterfaceEntry,
)


def _make_device_info_response(num_extra_fields: int = 0) -> dict:
    """Generate a device info response with optional padding."""
    data = {
        "status": "success",
        "device": "spine-01",
        "data": {
            "hostname": "spine-01",
            "model": "DCS-7280SR3-48YC8",
            "eos_version": "4.32.1F",
            "serial_number": "SSJ12345678",
            "uptime_seconds": 1234567,
            "total_memory": 8192000,
            "free_memory": 4096000,
        },
    }
    for i in range(num_extra_fields):
        data["data"][f"extra_field_{i}"] = f"value_{i}" * 10
    return data


def _make_interface_table(num_interfaces: int) -> dict:
    """Generate a large interfaces response."""
    interfaces = {}
    for i in range(num_interfaces):
        interfaces[f"Ethernet{i + 1}"] = {
            "name": f"Ethernet{i + 1}",
            "lineProtocolStatus": "up" if i % 3 != 2 else "down",
            "interfaceStatus": "connected" if i % 3 != 2 else "notconnect",
            "vlanInformation": {"interfaceMode": "access", "vlanId": 100 + (i % 50)},
            "bandwidth": 10000000000,
            "description": f"Server port {i + 1} -- rack {i // 48 + 1}",
            "mtu": 9214,
            "duplex": "full",
            "autoNegotiate": "on",
            "interfaceCounters": {
                "inOctets": 123456789 * (i + 1),
                "outOctets": 987654321 * (i + 1),
                "inUcastPkts": 12345 * (i + 1),
                "outUcastPkts": 54321 * (i + 1),
                "inErrors": 0,
                "outErrors": 0,
                "inDiscards": 0,
                "outDiscards": 0,
            },
        }
    return {
        "status": "success",
        "device": "spine-01",
        "data": interfaces,
    }


def _make_routing_table(num_routes: int) -> dict:
    """Generate a large routing table response."""
    routes = {}
    for i in range(num_routes):
        prefix = f"10.{(i >> 16) & 0xFF}.{(i >> 8) & 0xFF}.{i & 0xFF}/24"
        routes[prefix] = {
            "routeType": "eBGP" if i % 3 == 0 else "connected" if i % 3 == 1 else "OSPF",
            "metric": 20 + (i % 100),
            "preference": 200 if i % 3 == 0 else 0 if i % 3 == 1 else 110,
            "nextHops": [
                {
                    "nextHop": f"10.0.{(i % 4)}.1",
                    "interface": f"Ethernet{(i % 48) + 1}",
                    "weight": 1,
                }
            ],
        }
    return {
        "status": "success",
        "device": "spine-01",
        "data": {"vrfs": {"default": {"routes": routes}}},
    }


def _make_bgp_peers(num_peers: int) -> dict:
    """Generate BGP peer summary with many peers."""
    peers = {}
    for i in range(num_peers):
        peer_ip = f"10.{(i >> 8) & 0xFF}.{i & 0xFF}.1"
        peers[peer_ip] = {
            "peerState": "Established" if i % 5 != 4 else "Active",
            "asn": 65000 + (i % 200),
            "prefixesReceived": 1000 + i * 10,
            "prefixesSent": 500 + i * 5,
            "upDownTime": 86400 * (i % 30 + 1),
            "underMaintenance": False,
        }
    return {
        "status": "success",
        "device": "spine-01",
        "data": {
            "vrf": "default",
            "router_id": "10.0.0.1",
            "local_as": 65000,
            "peers": peers,
        },
    }


# --- JSON Encoding Benchmarks ---


@pytest.mark.benchmark(group="json-encode")
def test_json_encode_small_response(benchmark):
    """JSON encode a small device info response (~500 bytes)."""
    data = _make_device_info_response()
    result = benchmark(json.dumps, data)
    assert len(result) > 100


@pytest.mark.benchmark(group="json-encode")
def test_json_encode_medium_response(benchmark):
    """JSON encode a medium interface table (~50 interfaces, ~30KB)."""
    data = _make_interface_table(50)
    result = benchmark(json.dumps, data)
    assert len(result) > 10000


@pytest.mark.benchmark(group="json-encode")
def test_json_encode_large_response(benchmark):
    """JSON encode a large routing table (~1000 routes, ~200KB)."""
    data = _make_routing_table(1000)
    result = benchmark(json.dumps, data)
    assert len(result) > 100000


@pytest.mark.benchmark(group="json-encode")
def test_json_encode_xlarge_response(benchmark):
    """JSON encode an extra-large response (~5000 routes, ~1MB)."""
    data = _make_routing_table(5000)
    result = benchmark(json.dumps, data)
    assert len(result) > 500000


# --- JSON Decoding Benchmarks ---


@pytest.mark.benchmark(group="json-decode")
def test_json_decode_small_response(benchmark):
    """JSON decode a small device info response."""
    data_str = json.dumps(_make_device_info_response())
    result = benchmark(json.loads, data_str)
    assert result["status"] == "success"


@pytest.mark.benchmark(group="json-decode")
def test_json_decode_medium_response(benchmark):
    """JSON decode a medium interface table (~50 interfaces)."""
    data_str = json.dumps(_make_interface_table(50))
    result = benchmark(json.loads, data_str)
    assert result["status"] == "success"


@pytest.mark.benchmark(group="json-decode")
def test_json_decode_large_response(benchmark):
    """JSON decode a large routing table (~1000 routes)."""
    data_str = json.dumps(_make_routing_table(1000))
    result = benchmark(json.loads, data_str)
    assert result["status"] == "success"


# --- JSON Round-Trip Benchmarks ---


@pytest.mark.benchmark(group="json-roundtrip")
def test_json_roundtrip_small(benchmark):
    """Full encode + decode cycle for small response."""
    data = _make_device_info_response()

    def _roundtrip():
        return json.loads(json.dumps(data))

    result = benchmark(_roundtrip)
    assert result["status"] == "success"


@pytest.mark.benchmark(group="json-roundtrip")
def test_json_roundtrip_large(benchmark):
    """Full encode + decode cycle for large routing table."""
    data = _make_routing_table(1000)

    def _roundtrip():
        return json.loads(json.dumps(data))

    result = benchmark(_roundtrip)
    assert result["status"] == "success"


# --- Pydantic Model Validation Benchmarks ---


@pytest.mark.benchmark(group="pydantic-validation")
def test_pydantic_device_info_output(benchmark):
    """Validate DeviceInfoOutput model (structured output schema)."""
    data = {
        "status": "success",
        "device": "spine-01",
        "data": {"model": "vEOS-lab", "version": "4.32.1F"},
    }
    result = benchmark(DeviceInfoOutput.model_validate, data)
    assert result.status == "success"


@pytest.mark.benchmark(group="pydantic-validation")
def test_pydantic_bgp_summary_output(benchmark):
    """Validate BgpSummaryOutput model (more complex schema)."""
    data = {
        "status": "success",
        "device": "spine-01",
        "data": {
            "vrf": "default",
            "router_id": "10.0.0.1",
            "local_as": 65000,
            "peers": {"10.0.1.1": {"state": "Established", "prefixes": 100}},
        },
        "pagination": {"offset": 0, "limit": 50, "total": 1},
    }
    result = benchmark(BgpSummaryOutput.model_validate, data)
    assert result.status == "success"


@pytest.mark.benchmark(group="pydantic-validation")
def test_pydantic_device_facts(benchmark):
    """Validate cross-vendor DeviceFacts model."""
    data = {
        "hostname": "spine-01",
        "model": "DCS-7280SR3-48YC8",
        "serial_number": "SSJ12345678",
        "version": "4.32.1F",
        "uptime_seconds": 1234567,
        "platform": "eos",
        "vendor": "arista",
    }
    result = benchmark(DeviceFacts.model_validate, data)
    assert result.hostname == "spine-01"


@pytest.mark.benchmark(group="pydantic-validation")
def test_pydantic_interface_entries_batch(benchmark):
    """Validate a batch of InterfaceEntry models (simulates large response)."""
    entries = [
        {
            "is_up": i % 3 != 2,
            "is_enabled": True,
            "description": f"Port {i}",
            "speed": "10G",
            "mtu": 9214,
            "mac_address": f"00:1c:73:00:{i >> 8:02x}:{i & 0xFF:02x}",
        }
        for i in range(100)
    ]

    def _validate_batch():
        return [InterfaceEntry.model_validate(e) for e in entries]

    results = benchmark(_validate_batch)
    assert len(results) == 100


@pytest.mark.benchmark(group="pydantic-serialization")
def test_pydantic_model_dump(benchmark):
    """Benchmark Pydantic model_dump() (model → dict)."""
    model = DeviceInfoOutput(
        status="success",
        device="spine-01",
        data={"model": "vEOS-lab", "version": "4.32.1F"},
    )
    result = benchmark(model.model_dump)
    assert result["status"] == "success"


@pytest.mark.benchmark(group="pydantic-serialization")
def test_pydantic_model_dump_json(benchmark):
    """Benchmark Pydantic model_dump_json() (model → JSON string)."""
    model = DeviceInfoOutput(
        status="success",
        device="spine-01",
        data={"model": "vEOS-lab", "version": "4.32.1F"},
    )
    result = benchmark(model.model_dump_json)
    assert len(result) > 10
