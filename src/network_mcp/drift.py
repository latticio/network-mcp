"""Configuration drift detection and remediation.

Compares running device configuration against a "golden" intended state.
Golden configs can come from: local files, Git repository, or NetBox.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from network_mcp.config import NetworkSettings

logger = logging.getLogger("network-mcp.drift")


class DriftSeverity(Enum):
    """Severity levels for configuration drift items."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class DriftItem:
    """A single configuration drift between golden and running config."""

    section: str
    expected: str
    actual: str
    severity: DriftSeverity
    description: str
    change_type: str = ""  # "added", "removed", "modified"


@dataclass
class DriftReport:
    """Aggregated drift report for a device."""

    device: str
    timestamp: str
    golden_source: str
    total_drifts: int = 0
    critical_drifts: int = 0
    warning_drifts: int = 0
    info_drifts: int = 0
    items: list[DriftItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize report to a dict for MCP tool responses."""
        return {
            "device": self.device,
            "timestamp": self.timestamp,
            "golden_source": self.golden_source,
            "total_drifts": self.total_drifts,
            "critical_drifts": self.critical_drifts,
            "warning_drifts": self.warning_drifts,
            "info_drifts": self.info_drifts,
            "items": [
                {
                    "section": item.section,
                    "expected": item.expected,
                    "actual": item.actual,
                    "severity": item.severity.value,
                    "description": item.description,
                    "change_type": item.change_type,
                }
                for item in self.items
            ],
        }


# --- Severity classification patterns ---

_CRITICAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"access-list",
        r"ip access-group",
        r"firewall filter",
        r"neighbor\s+\S+\s+remote-as",
        r"network\s+\S+\s+area",
        r"router ospf",
        r"router bgp",
        r"aaa\s",
        r"radius-server",
        r"tacacs-server",
        r"username\s+\S+\s+privilege",
        r"enable secret",
        r"crypto\s",
        r"key chain",
        r"ip route\s",
        r"route-map\s",
        r"prefix-list\s",
        r"management api",
        r"management ssh",
        r"set community",
        r"policy-statement",
    ]
]

_WARNING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"description\s",
        r"logging\s",
        r"ntp server",
        r"snmp-server",
        r"banner\s",
        r"interface\s",
        r"vlan\s",
        r"spanning-tree",
        r"mtu\s",
    ]
]


class DriftDetector:
    """Detect configuration drift across any vendor."""

    def __init__(self, settings: NetworkSettings) -> None:
        self.golden_config_dir = settings.net_golden_config_dir

    def detect_drift(self, device: str, running_config: str, golden_config: str) -> DriftReport:
        """Compare running config against golden config.

        Uses unified diff to find differences, then classifies each
        difference by severity based on the config section.
        """
        timestamp = datetime.now(UTC).isoformat()
        report = DriftReport(
            device=device,
            timestamp=timestamp,
            golden_source=f"file:{self.golden_config_dir}/{device}.conf" if self.golden_config_dir else "inline",
        )

        golden_lines = golden_config.splitlines(keepends=True)
        running_lines = running_config.splitlines(keepends=True)

        diff = list(
            difflib.unified_diff(
                golden_lines,
                running_lines,
                fromfile="golden-config",
                tofile="running-config",
                lineterm="",
            )
        )

        if not diff:
            return report

        # Parse the unified diff into drift items grouped by context section
        current_section = "global"
        added_lines: list[str] = []
        removed_lines: list[str] = []

        for line in diff:
            # Skip diff headers
            if line.startswith("---") or line.startswith("+++"):
                continue
            # Hunk header — extract section context if available
            if line.startswith("@@"):
                # Flush any pending changes
                self._flush_drift_group(report, current_section, removed_lines, added_lines)
                added_lines = []
                removed_lines = []
                continue

            # Context line — track which section we're in
            if line.startswith(" "):
                self._flush_drift_group(report, current_section, removed_lines, added_lines)
                added_lines = []
                removed_lines = []
                stripped = line[1:].strip()
                if stripped and not stripped.startswith("!") and not stripped.startswith("#"):
                    # Non-indented lines are section headers
                    if not line[1:].startswith((" ", "\t")):
                        current_section = stripped
                continue

            if line.startswith("-"):
                removed_lines.append(line[1:])
            elif line.startswith("+"):
                added_lines.append(line[1:])

        # Flush remaining
        self._flush_drift_group(report, current_section, removed_lines, added_lines)

        # Compute summary counts
        report.total_drifts = len(report.items)
        report.critical_drifts = sum(1 for i in report.items if i.severity == DriftSeverity.CRITICAL)
        report.warning_drifts = sum(1 for i in report.items if i.severity == DriftSeverity.WARNING)
        report.info_drifts = sum(1 for i in report.items if i.severity == DriftSeverity.INFO)

        return report

    def _flush_drift_group(
        self,
        report: DriftReport,
        section: str,
        removed_lines: list[str],
        added_lines: list[str],
    ) -> None:
        """Create drift items from accumulated added/removed lines."""
        if not removed_lines and not added_lines:
            return

        expected = "\n".join(line.rstrip() for line in removed_lines) if removed_lines else ""
        actual = "\n".join(line.rstrip() for line in added_lines) if added_lines else ""

        # Determine change type
        if removed_lines and added_lines:
            change_type = "modified"
            description = f"Configuration modified in section '{section}'"
        elif removed_lines:
            change_type = "removed"
            description = f"Configuration removed from section '{section}'"
        else:
            change_type = "added"
            description = f"Configuration added to section '{section}'"

        # Classify severity — check all lines in the group
        all_content = expected + "\n" + actual
        severity = self._classify_severity(section, all_content)

        report.items.append(
            DriftItem(
                section=section,
                expected=expected,
                actual=actual,
                severity=severity,
                description=description,
                change_type=change_type,
            )
        )

    def _classify_severity(self, section: str, content: str) -> DriftSeverity:
        """Classify drift severity based on config section and content."""
        combined = f"{section} {content}"
        for pattern in _CRITICAL_PATTERNS:
            if pattern.search(combined):
                return DriftSeverity.CRITICAL
        for pattern in _WARNING_PATTERNS:
            if pattern.search(combined):
                return DriftSeverity.WARNING
        return DriftSeverity.INFO

    def load_golden_config(self, device: str, source: str = "file") -> str | None:
        """Load golden config from configured source.

        Args:
            device: Device hostname or inventory name.
            source: Source type — "file" loads from golden_config_dir/{device}.conf.

        Returns:
            Golden config text, or None if not found.
        """
        if source == "file":
            return self._load_from_file(device)
        if source == "netbox":
            return self._load_from_netbox(device)
        logger.warning("Unsupported golden config source: %s", source)
        return None

    def _load_from_file(self, device: str) -> str | None:
        """Load golden config from local file."""
        if not self.golden_config_dir:
            return None
        config_path = Path(self.golden_config_dir) / f"{device}.conf"
        if not config_path.is_file():
            logger.warning("Golden config file not found: %s", config_path)
            return None
        return config_path.read_text(encoding="utf-8")

    def _load_from_netbox(self, device: str) -> str | None:
        """Load golden config from NetBox rendered config context."""
        try:
            from network_mcp.server import settings

            if not settings.netbox_url or not settings.netbox_token:
                logger.warning("NetBox not configured — cannot load golden config for %s", device)
                return None

            import pynetbox

            nb = pynetbox.api(settings.netbox_url, token=settings.netbox_token.get_secret_value())
            nb_device = nb.dcim.devices.get(name=device)
            if not nb_device:
                logger.warning("Device %s not found in NetBox", device)
                return None
            # NetBox config context is typically rendered via config-contexts
            config_context = nb_device.config_context
            if config_context:
                import json

                return json.dumps(config_context, indent=2)
            logger.warning("No config context found for device %s in NetBox", device)
            return None
        except ImportError:
            logger.warning("pynetbox not installed — cannot load golden config from NetBox")
            return None
        except Exception as e:
            logger.warning("Failed to load golden config from NetBox for %s: %s", device, e)
            return None

    def generate_remediation_commands(self, report: DriftReport, min_severity: DriftSeverity) -> list[str]:
        """Generate remediation commands from a drift report.

        Returns the golden config lines that should be pushed to restore
        the expected state, filtered by minimum severity.

        Args:
            report: Drift report with items to remediate.
            min_severity: Minimum severity threshold (items at or above are included).
        """
        severity_order = [DriftSeverity.CRITICAL, DriftSeverity.WARNING, DriftSeverity.INFO]
        min_index = severity_order.index(min_severity)
        allowed_severities = set(severity_order[: min_index + 1])

        commands: list[str] = []
        for item in report.items:
            if item.severity not in allowed_severities:
                continue

            if item.change_type == "removed" or item.change_type == "modified":
                # Push the expected (golden) lines
                for line in item.expected.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("!") and not stripped.startswith("#"):
                        commands.append(stripped)
            elif item.change_type == "added":
                # Lines added that shouldn't be there — negate them
                for line in item.actual.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("!") and not stripped.startswith("#"):
                        commands.append(f"no {stripped}")

        return commands
