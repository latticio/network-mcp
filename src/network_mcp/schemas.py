"""Pydantic output models for structured output on high-use tools.

These models are used with FastMCP's ``structured_output=True`` parameter to
declare output schemas in the MCP tool manifest.  FastMCP auto-generates a
JSON Schema from the return-type annotation and validates the tool's return
value at runtime via ``model_validate()``.

Keep models permissive — ``data`` is typed as ``dict | None`` so the schema
covers both success and error responses without breaking runtime validation.

Cross-vendor normalization models (DeviceFacts, InterfaceEntry, ArpEntry,
BgpPeerEntry) provide consistent data structures regardless of vendor.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

# ============================================================================
# Cross-vendor normalized data models
# ============================================================================


class DeviceFacts(BaseModel):
    """Vendor-neutral device facts returned by ``get_facts()``."""

    hostname: str = ""
    model: str = ""
    serial_number: str = ""
    version: str = ""
    uptime_seconds: int = 0
    platform: str = "unknown"
    vendor: str = "unknown"

    @model_validator(mode="after")
    def _check_required(self) -> DeviceFacts:
        if not self.hostname:
            raise ValueError("hostname must not be empty")
        return self


class InterfaceEntry(BaseModel):
    """Vendor-neutral interface entry returned by ``get_interfaces()``."""

    is_up: bool = False
    is_enabled: bool = False
    description: str = ""
    speed: str = "unknown"
    mtu: int = 0
    mac_address: str = ""


class ArpEntry(BaseModel):
    """Vendor-neutral ARP entry returned by ``get_arp_table()``."""

    ip: str
    mac: str = ""
    interface: str = ""
    age: float = -1.0

    @model_validator(mode="after")
    def _check_required(self) -> ArpEntry:
        if not self.ip:
            raise ValueError("ip must not be empty")
        return self


class BgpPeerEntry(BaseModel):
    """Vendor-neutral BGP peer entry returned by ``get_bgp_summary()``."""

    state: str = "Unknown"
    prefixes_received: int = 0
    uptime: int = 0


# ============================================================================
# MCP tool output schemas (existing)
# ============================================================================


class DeviceInfoOutput(BaseModel):
    """Output schema for eos_get_device_info."""

    status: str = Field(description="'success' or 'error'")
    device: str = Field(default="", description="Target device hostname or IP")
    data: dict[str, Any] | None = Field(
        default=None, description="Device info: hostname, model, version, serial, uptime, memory"
    )
    error: str | None = Field(default=None, description="Error message if status is 'error'")


class ServerHealthOutput(BaseModel):
    """Output schema for eos_server_health."""

    status: str = Field(description="'success' or 'error'")
    data: dict[str, Any] | None = Field(
        default=None, description="Server health: connection pool, cache, circuit breakers, uptime"
    )
    error: str | None = Field(default=None, description="Error message if status is 'error'")


class BgpSummaryOutput(BaseModel):
    """Output schema for eos_get_bgp_summary."""

    status: str = Field(description="'success' or 'error'")
    device: str = Field(default="", description="Target device hostname or IP")
    data: dict[str, Any] | None = Field(
        default=None, description="BGP summary: VRF, address family, router ID, ASN, peers"
    )
    pagination: dict[str, Any] | None = Field(
        default=None, description="Pagination metadata when limit/offset are used"
    )
    error: str | None = Field(default=None, description="Error message if status is 'error'")


class BgpDiagnosticOutput(BaseModel):
    """Output schema for eos_diagnose_bgp_session."""

    status: str = Field(description="'success', 'partial', or 'error'")
    device: str = Field(default="", description="Target device hostname or IP")
    workflow: str = Field(default="", description="Workflow name identifier")
    duration_ms: int = Field(default=0, description="Workflow execution time in milliseconds")
    diagnosis: dict[str, Any] | None = Field(default=None, description="Severity, summary, and findings list")
    raw_data: dict[str, Any] | None = Field(default=None, description="Raw command output for follow-up analysis")
    sub_operations: list[dict[str, Any]] | None = Field(
        default=None, description="Per-command execution status and timing"
    )
    error: str | None = Field(default=None, description="Error message if status is 'error'")


class FabricHealthOutput(BaseModel):
    """Output schema for eos_validate_fabric_health."""

    status: str = Field(description="'success', 'partial', or 'error'")
    device: str = Field(default="", description="Always 'fabric' for fabric-wide operations")
    workflow: str = Field(default="", description="Workflow name identifier")
    duration_ms: int = Field(default=0, description="Workflow execution time in milliseconds")
    diagnosis: dict[str, Any] | None = Field(default=None, description="Severity, summary, health score, and findings")
    raw_data: dict[str, Any] | None = Field(default=None, description="Per-device raw command output")
    sub_operations: list[dict[str, Any]] | None = Field(default=None, description="Per-device execution status")
    error: str | None = Field(default=None, description="Error message if status is 'error'")
