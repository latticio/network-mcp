"""RFC 5424 structured syslog formatter for SIEM integration.

Produces messages in the format:
    <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [SD-ID SD-PARAMS] MSG

Structured data elements:
    - [meta@enterprise tool="..." host="..." user="..." tenant="..."]
    - [outcome@enterprise status="..." duration_ms="..."]

Suitable for ingestion by Splunk, QRadar, Sentinel, and other SIEM platforms.
"""

import logging
import os
import socket
from datetime import UTC, datetime


class RFC5424Formatter(logging.Formatter):
    """RFC 5424 structured syslog formatter for SIEM integration.

    Format: <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [SD-ID SD-PARAMS] MSG

    Structured data elements:
    - [meta@enterprise tool="..." host="..." user="..." tenant="..."]
    - [outcome@enterprise status="..." duration_ms="..."]
    """

    FACILITY = 16  # local0
    ENTERPRISE_ID = "48577"  # Private enterprise number placeholder
    SEVERITY_MAP = {
        logging.DEBUG: 7,  # debug
        logging.INFO: 6,  # informational
        logging.WARNING: 4,  # warning
        logging.ERROR: 3,  # error
        logging.CRITICAL: 2,  # critical
    }

    def __init__(self, app_name: str = "network-mcp") -> None:
        super().__init__()
        self._app_name = app_name
        self._hostname = socket.gethostname()

    @staticmethod
    def _escape_sd_value(value: str) -> str:
        r"""Escape special characters in structured data values per RFC 5424 Section 6.3.3.

        Characters that must be escaped: ``"``, ``\``, ``]``.
        """
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")

    def _compute_priority(self, levelno: int) -> int:
        """Compute PRI value: facility * 8 + severity."""
        severity = self.SEVERITY_MAP.get(levelno, 6)
        return self.FACILITY * 8 + severity

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as an RFC 5424 structured syslog message."""
        pri = self._compute_priority(record.levelno)

        # ISO 8601 timestamp with timezone (RFC 5424 Section 6.2.3)
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        timestamp = dt.isoformat()

        procid = str(os.getpid())
        msgid = "-"

        # Build structured data from audit_data if available
        sd = self._build_structured_data(record)

        msg = record.getMessage()

        return f"<{pri}>1 {timestamp} {self._hostname} {self._app_name} {procid} {msgid} {sd} {msg}"

    def _build_structured_data(self, record: logging.LogRecord) -> str:
        """Build RFC 5424 structured data elements from log record extra fields."""
        audit_data: dict | None = getattr(record, "audit_data", None)

        if not audit_data:
            return "-"

        elements: list[str] = []

        # meta@enterprise element
        meta_params: list[str] = []
        for field_name in ("tool", "device", "user", "tenant_id", "event"):
            value = audit_data.get(field_name)
            if value is not None:
                key = "host" if field_name == "device" else field_name
                meta_params.append(f'{key}="{self._escape_sd_value(str(value))}"')

        if meta_params:
            elements.append(f"[meta@{self.ENTERPRISE_ID} {' '.join(meta_params)}]")

        # outcome@enterprise element
        outcome_params: list[str] = []
        status = audit_data.get("result_status")
        if status is not None:
            outcome_params.append(f'status="{self._escape_sd_value(str(status))}"')
        duration = audit_data.get("duration_ms")
        if duration is not None:
            outcome_params.append(f'duration_ms="{self._escape_sd_value(str(duration))}"')
        error = audit_data.get("error")
        if error is not None:
            outcome_params.append(f'error="{self._escape_sd_value(str(error))}"')

        if outcome_params:
            elements.append(f"[outcome@{self.ENTERPRISE_ID} {' '.join(outcome_params)}]")

        return "".join(elements) if elements else "-"
