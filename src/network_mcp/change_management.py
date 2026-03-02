"""Enterprise change management for network configuration changes.

When enabled (NET_CHANGE_MGMT_ENABLED=true), all write operations go through
a structured workflow: pre-validation -> approval -> execution -> post-validation.

Integrates with the existing audit logging system for full traceability.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as dt_time
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from network_mcp.connection import ConnectionManager

logger = logging.getLogger("network-mcp.change-mgmt")


class ChangeRisk(Enum):
    """Risk classification for configuration changes."""

    LOW = "low"  # Interface description, VLAN rename
    MEDIUM = "medium"  # VLAN create/delete, static route
    HIGH = "high"  # BGP neighbor, ACL change
    CRITICAL = "critical"  # Shutdown interface, config push


class ChangeStatus(Enum):
    """Lifecycle status of a change request."""

    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class ChangeRequest:
    """A tracked change request for a network configuration change."""

    id: str
    tool_name: str
    device: str
    commands: list[str]
    risk: ChangeRisk
    status: ChangeStatus = ChangeStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_by: str = ""
    approved_by: str = ""
    pre_snapshot: dict = field(default_factory=dict)
    post_snapshot: dict = field(default_factory=dict)
    rollback_commands: list[str] = field(default_factory=list)
    snow_incident_id: str = ""
    error: str = ""
    approval_chain: list[dict] = field(default_factory=list)
    execution_log: list[dict] = field(default_factory=list)


# --- Risk assessment patterns ---

# Tools/commands that map to CRITICAL risk
_CRITICAL_PATTERNS = [
    "shutdown",
    "no interface",
    "no router bgp",
    "no router ospf",
]

# Tools/commands that map to HIGH risk
_HIGH_TOOL_PATTERNS = [
    "delete",
    "push_config",
    "remove",
]

_HIGH_CMD_PATTERNS = [
    r"^router bgp",
    r"^router ospf",
    r"^ip access-list",
    r"^no neighbor",
    r"^no network",
]

# Tools/commands that map to MEDIUM risk
_MEDIUM_TOOL_PATTERNS = [
    "create",
    "configure",
    "add",
    "set",
]

# Maintenance window time pattern: "Day HH:MM-HH:MM"
_WINDOW_PATTERN = re.compile(r"^(Sun|Mon|Tue|Wed|Thu|Fri|Sat|\*)\s+(\d{2}):(\d{2})-(\d{2}):(\d{2})$")

# Day-of-week mapping (0=Monday in Python's datetime)
_DAY_MAP = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}


class ChangeManager:
    """Manages change requests and maintenance windows.

    Provides a structured workflow for network configuration changes:
    create -> approve -> execute -> validate (or rollback).
    """

    def __init__(self, settings) -> None:
        self.enabled: bool = settings.net_change_mgmt_enabled
        self.require_approval: bool = settings.net_change_require_approval
        self.auto_rollback_timeout: int = settings.net_auto_rollback_timeout
        self.maintenance_windows: list[tuple[int | None, dt_time, dt_time]] = self._parse_windows(
            settings.net_maintenance_windows
        )
        self._pending: dict[str, ChangeRequest] = {}
        self._completed: dict[str, ChangeRequest] = {}

    def create_change_request(
        self,
        tool_name: str,
        device: str,
        commands: list[str],
        user: str = "",
    ) -> ChangeRequest:
        """Create a change request for a write operation.

        Args:
            tool_name: Name of the MCP tool initiating the change.
            device: Target device hostname.
            commands: Proposed configuration commands.
            user: Authenticated user (from JWT subject).

        Returns:
            A new ChangeRequest. LOW-risk changes are auto-approved when
            require_approval is False.
        """
        cr_id = str(uuid.uuid4())
        risk = self._assess_risk(tool_name, commands)
        cr = ChangeRequest(
            id=cr_id,
            tool_name=tool_name,
            device=device,
            commands=commands,
            risk=risk,
            created_by=user,
        )
        cr.rollback_commands = self._generate_rollback_commands(tool_name, commands)

        # Auto-approve LOW risk if approval not required
        if not self.require_approval and risk == ChangeRisk.LOW:
            cr.status = ChangeStatus.APPROVED
            cr.approved_by = "auto"
            logger.info("Auto-approved low-risk change %s for %s", cr_id, device)

        self._pending[cr_id] = cr
        logger.info(
            "Change request %s created: tool=%s device=%s risk=%s status=%s",
            cr_id,
            tool_name,
            device,
            risk.value,
            cr.status.value,
        )
        return cr

    def approve_change(self, change_id: str, approved_by: str = "", reason: str = "") -> ChangeRequest | None:
        """Approve a pending change request.

        Args:
            change_id: UUID of the change request.
            approved_by: Who approved (admin user).
            reason: Reason for approval.

        Returns:
            The approved ChangeRequest, or None if not found or not pending.
        """
        cr = self._pending.get(change_id)
        if cr is None:
            return None
        if cr.status != ChangeStatus.PENDING:
            return None
        cr.status = ChangeStatus.APPROVED
        cr.approved_by = approved_by
        cr.approval_chain.append(
            {
                "user": approved_by,
                "timestamp": datetime.now(UTC).isoformat(),
                "reason": reason,
                "action": "approved",
            }
        )
        logger.info("Change %s approved by %s", change_id, approved_by)
        return cr

    def get_change_request(self, change_id: str) -> ChangeRequest | None:
        """Look up a change request by ID (pending or completed)."""
        return self._pending.get(change_id) or self._completed.get(change_id)

    def list_pending(self, device: str = "") -> list[ChangeRequest]:
        """List pending change requests, optionally filtered by device.

        Args:
            device: If non-empty, filter to changes for this device only.

        Returns:
            List of pending ChangeRequests.
        """
        results = []
        for cr in self._pending.values():
            if cr.status in (ChangeStatus.PENDING, ChangeStatus.APPROVED):
                if not device or cr.device == device:
                    results.append(cr)
        return results

    def check_maintenance_window(self, now: datetime | None = None) -> bool:
        """Check if current time is within a maintenance window.

        Args:
            now: Override for current time (for testing). Defaults to utcnow.

        Returns:
            True if in a maintenance window or no windows are configured
            (wildcard '*' means always allowed).
        """
        if not self.maintenance_windows:
            return True

        if now is None:
            now = datetime.now(UTC)

        current_day = now.weekday()  # 0=Monday
        current_time = now.time()

        for day_of_week, start_time, end_time in self.maintenance_windows:
            # day_of_week is None for wildcard '*'
            if day_of_week is not None and day_of_week != current_day:
                continue

            if start_time <= end_time:
                # Normal window (e.g., 02:00-06:00)
                if start_time <= current_time <= end_time:
                    return True
            else:
                # Overnight window (e.g., 22:00-04:00)
                if current_time >= start_time or current_time <= end_time:
                    return True

        return False

    def capture_pre_snapshot(self, cr: ChangeRequest, conn_mgr: "ConnectionManager") -> None:
        """Capture device state before change for rollback comparison.

        Retrieves the running config section relevant to the change.

        Args:
            cr: Change request to populate.
            conn_mgr: Connection manager for device access.
        """
        try:
            with conn_mgr.acquire(cr.device) as driver:
                result = driver.run_show(["show running-config"], encoding="text")
                cr.pre_snapshot = {"running_config": result[0] if result else {}}
                logger.info("Pre-snapshot captured for change %s on %s", cr.id, cr.device)
        except Exception as e:
            logger.warning("Failed to capture pre-snapshot for change %s: %s", cr.id, e)
            cr.pre_snapshot = {"error": str(e)}

    def capture_post_snapshot(self, cr: ChangeRequest, conn_mgr: "ConnectionManager") -> None:
        """Capture device state after change for verification.

        Args:
            cr: Change request to populate.
            conn_mgr: Connection manager for device access.
        """
        try:
            with conn_mgr.acquire(cr.device) as driver:
                result = driver.run_show(["show running-config"], encoding="text")
                cr.post_snapshot = {"running_config": result[0] if result else {}}
                logger.info("Post-snapshot captured for change %s on %s", cr.id, cr.device)
        except Exception as e:
            logger.warning("Failed to capture post-snapshot for change %s: %s", cr.id, e)
            cr.post_snapshot = {"error": str(e)}

    def mark_executing(self, cr: ChangeRequest) -> None:
        """Mark a change request as currently executing."""
        cr.status = ChangeStatus.EXECUTING
        cr.execution_log.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "action": "executing",
                "result": "in_progress",
                "details": f"Executing {len(cr.commands)} command(s) on {cr.device}",
            }
        )

    def mark_completed(self, cr: ChangeRequest) -> None:
        """Mark a change request as completed and move to completed store."""
        cr.status = ChangeStatus.COMPLETED
        cr.execution_log.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "action": "completed",
                "result": "success",
                "details": f"Change {cr.id} completed successfully on {cr.device}",
            }
        )
        self._completed[cr.id] = cr
        self._pending.pop(cr.id, None)

    def mark_failed(self, cr: ChangeRequest, error: str) -> None:
        """Mark a change request as failed.

        Args:
            cr: Change request to update.
            error: Error message describing the failure.
        """
        cr.status = ChangeStatus.FAILED
        cr.error = error
        cr.execution_log.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "action": "failed",
                "result": "error",
                "details": error,
            }
        )
        self._completed[cr.id] = cr
        self._pending.pop(cr.id, None)

    def validate_change(self, cr: ChangeRequest) -> bool:
        """Validate that post-change state reflects expected changes.

        Compares pre/post snapshots to verify that the commands had an effect
        and that no snapshot capture errors occurred.

        Args:
            cr: Change request with populated pre/post snapshots.

        Returns:
            True if validation passes, False otherwise.
        """
        # If either snapshot has an error, validation fails
        if cr.pre_snapshot.get("error") or cr.post_snapshot.get("error"):
            return False

        # If both snapshots are empty, we can't validate
        if not cr.pre_snapshot or not cr.post_snapshot:
            return False

        pre_config = cr.pre_snapshot.get("running_config", "")
        post_config = cr.post_snapshot.get("running_config", "")

        # Config should have changed (otherwise commands had no effect)
        if pre_config and post_config and pre_config == post_config:
            logger.warning("Change %s: pre/post configs are identical — commands may not have taken effect", cr.id)
            return False

        return True

    def rollback(self, cr: ChangeRequest, conn_mgr: "ConnectionManager") -> dict:
        """Roll back a failed change using pre-computed rollback commands.

        After applying rollback commands, validates the device state against
        the pre-change snapshot. If a pre-change snapshot exists, compares
        the post-rollback config to verify the rollback was successful.

        Args:
            cr: Change request to roll back.
            conn_mgr: Connection manager for device access.

        Returns:
            Status dict with success/error/warning result.
        """
        if not cr.rollback_commands:
            cr.status = ChangeStatus.FAILED
            cr.execution_log.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "action": "rollback_failed",
                    "result": "error",
                    "details": "No rollback commands available",
                }
            )
            return {
                "status": "error",
                "device": cr.device,
                "change_id": cr.id,
                "error": "No rollback commands available for this change",
            }

        try:
            with conn_mgr.acquire(cr.device) as driver:
                driver.run_config(cr.rollback_commands)

            cr.status = ChangeStatus.ROLLED_BACK
            self._completed[cr.id] = cr
            self._pending.pop(cr.id, None)
            logger.info("Change %s rolled back on %s", cr.id, cr.device)

            # Post-rollback validation: compare against pre-change snapshot
            validation_result = self._validate_rollback(cr, conn_mgr)

            cr.execution_log.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "action": "rolled_back",
                    "result": validation_result.get("validation_status", "success"),
                    "details": validation_result.get("message", "Rollback applied"),
                }
            )

            if validation_result.get("validation_status") == "warning":
                return {
                    "status": "warning",
                    "device": cr.device,
                    "change_id": cr.id,
                    "action": "rolled_back",
                    "rollback_commands": cr.rollback_commands,
                    "message": "Rollback applied but state differs from pre-change snapshot",
                    "validation": validation_result.get("validation"),
                }

            return {
                "status": "success",
                "device": cr.device,
                "change_id": cr.id,
                "action": "rolled_back",
                "rollback_commands": cr.rollback_commands,
                "message": validation_result.get("message", "Rollback applied successfully"),
            }
        except Exception as e:
            cr.status = ChangeStatus.FAILED
            cr.error = f"Rollback failed: {e}"
            cr.execution_log.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "action": "rollback_failed",
                    "result": "error",
                    "details": f"Rollback failed: {e}",
                }
            )
            logger.error("Rollback failed for change %s: %s", cr.id, e)
            return {
                "status": "error",
                "device": cr.device,
                "change_id": cr.id,
                "error": f"Rollback failed: {e}",
            }

    def _validate_rollback(self, cr: ChangeRequest, conn_mgr: "ConnectionManager") -> dict:
        """Compare post-rollback device state against pre-change snapshot.

        Args:
            cr: Change request with pre_snapshot populated.
            conn_mgr: Connection manager for device access.

        Returns:
            Dict with validation_status ('success', 'warning', or 'skipped') and details.
        """
        # If no pre-change snapshot exists, skip validation gracefully
        if not cr.pre_snapshot or cr.pre_snapshot.get("error"):
            return {
                "validation_status": "skipped",
                "message": "Rollback applied (no pre-change snapshot available for validation)",
            }

        pre_config = cr.pre_snapshot.get("running_config", "")
        if not pre_config:
            return {
                "validation_status": "skipped",
                "message": "Rollback applied (pre-change snapshot is empty)",
            }

        # Capture post-rollback state
        try:
            with conn_mgr.acquire(cr.device) as driver:
                result = driver.run_show(["show running-config"], encoding="text")
                post_rollback_config = result[0] if result else ""
        except Exception as e:
            logger.warning("Failed to capture post-rollback state for change %s: %s", cr.id, e)
            return {
                "validation_status": "skipped",
                "message": f"Rollback applied (post-rollback snapshot failed: {e})",
            }

        if not post_rollback_config:
            return {
                "validation_status": "skipped",
                "message": "Rollback applied (post-rollback config is empty)",
            }

        # Compare pre-change snapshot with post-rollback state
        pre_lines = str(pre_config).strip().splitlines()
        post_lines = str(post_rollback_config).strip().splitlines()

        if pre_lines == post_lines:
            return {
                "validation_status": "success",
                "message": "Rollback validated successfully",
            }

        # Find differences
        import difflib

        diff = list(difflib.unified_diff(pre_lines, post_lines, fromfile="pre-change", tofile="post-rollback", n=3))
        diff_summary = "\n".join(diff[:50])  # Limit diff output

        return {
            "validation_status": "warning",
            "message": "Rollback applied but state differs from pre-change snapshot",
            "validation": {
                "expected_sections": pre_lines[:20],
                "actual_sections": post_lines[:20],
                "diff_summary": diff_summary,
            },
        }

    def _assess_risk(self, tool_name: str, commands: list[str]) -> ChangeRisk:
        """Assess risk level of a change based on tool name and commands.

        Args:
            tool_name: MCP tool name (e.g., 'net_create_vlan').
            commands: Proposed configuration commands.

        Returns:
            ChangeRisk level.
        """
        tool_lower = tool_name.lower()
        cmd_text = " ".join(c.lower() for c in commands)

        # Check CRITICAL patterns first
        for pattern in _CRITICAL_PATTERNS:
            if pattern in tool_lower or pattern in cmd_text:
                return ChangeRisk.CRITICAL

        # Check HIGH patterns
        for pattern in _HIGH_TOOL_PATTERNS:
            if pattern in tool_lower:
                return ChangeRisk.HIGH
        for pattern in _HIGH_CMD_PATTERNS:
            if re.search(pattern, cmd_text, re.MULTILINE):
                return ChangeRisk.HIGH

        # Check MEDIUM patterns
        for pattern in _MEDIUM_TOOL_PATTERNS:
            if pattern in tool_lower:
                return ChangeRisk.MEDIUM

        return ChangeRisk.LOW

    def _generate_rollback_commands(self, tool_name: str, commands: list[str]) -> list[str]:
        """Generate rollback commands by prefixing 'no' to each command.

        This is a best-effort heuristic. For complex changes, users should
        provide explicit rollback commands.

        Args:
            tool_name: MCP tool name.
            commands: Proposed configuration commands.

        Returns:
            List of rollback commands (reversed order with 'no' prefix).
        """
        rollback = []
        for cmd in reversed(commands):
            stripped = cmd.strip()
            if not stripped:
                continue
            # If command already starts with 'no', remove it
            if stripped.startswith("no "):
                rollback.append(stripped[3:])
            else:
                rollback.append(f"no {stripped}")
        return rollback

    def _parse_windows(self, windows_str: str) -> list[tuple[int | None, dt_time, dt_time]]:
        """Parse maintenance window configuration string.

        Format: 'Sun 02:00-06:00,Sat 02:00-06:00' or '*' for always allowed.
        Each window is 'Day HH:MM-HH:MM' where Day is a 3-letter abbreviation
        or '*' for any day.

        Args:
            windows_str: Comma-separated maintenance window definitions.

        Returns:
            List of (day_of_week, start_time, end_time) tuples.
            day_of_week is None for wildcard '*'.
        """
        if not windows_str or windows_str.strip() == "*":
            return []  # Empty list means always allowed

        windows = []
        for window_str in windows_str.split(","):
            window_str = window_str.strip()
            if not window_str:
                continue

            match = _WINDOW_PATTERN.match(window_str)
            if not match:
                logger.warning("Invalid maintenance window format: %s (expected 'Day HH:MM-HH:MM')", window_str)
                continue

            day_str, start_h, start_m, end_h, end_m = match.groups()
            day_of_week = _DAY_MAP.get(day_str)  # None for '*'
            start_time = dt_time(int(start_h), int(start_m))
            end_time = dt_time(int(end_h), int(end_m))
            windows.append((day_of_week, start_time, end_time))

        return windows

    def to_dict(self, cr: ChangeRequest) -> dict:
        """Serialize a ChangeRequest to a dict for MCP tool responses.

        Args:
            cr: Change request to serialize.

        Returns:
            Dict suitable for JSON serialization.
        """
        return {
            "id": cr.id,
            "tool_name": cr.tool_name,
            "device": cr.device,
            "commands": cr.commands,
            "risk": cr.risk.value,
            "status": cr.status.value,
            "created_at": cr.created_at.isoformat(),
            "created_by": cr.created_by,
            "approved_by": cr.approved_by,
            "rollback_commands": cr.rollback_commands,
            "snow_incident_id": cr.snow_incident_id,
            "error": cr.error,
            "approval_chain": cr.approval_chain,
            "execution_log": cr.execution_log,
        }
