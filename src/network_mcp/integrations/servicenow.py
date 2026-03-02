"""ServiceNow integration — incident management, CMDB sync, change validation."""

from __future__ import annotations

import copy
import logging
from typing import Any

import httpx

from network_mcp.helpers import WRITE_SAFE, run_show_command
from network_mcp.server import conn_mgr, mcp, settings

logger = logging.getLogger("network-mcp.servicenow")

_SNOW_NOT_CONFIGURED = {
    "status": "error",
    "error": (
        "ServiceNow is not configured. Set SNOW_INSTANCE, SNOW_USERNAME, and SNOW_PASSWORD "
        "environment variables to enable ServiceNow integration."
    ),
}


class ServiceNowClient:
    """ServiceNow REST API client with connection pooling."""

    def __init__(self, instance: str, username: str, password: str):
        self._base_url = f"https://{instance}.service-now.com/api/now"
        self._client = httpx.Client(
            base_url=self._base_url,
            auth=(username, password),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=30.0,
        )

    def create_incident(
        self,
        short_description: str,
        description: str,
        urgency: int = 2,
        category: str = "Network",
    ) -> dict:
        """Create a new incident in ServiceNow.

        Args:
            short_description: Brief summary of the incident.
            description: Full incident description with diagnostics.
            urgency: Urgency level (1=High, 2=Medium, 3=Low).
            category: Incident category (default: Network).

        Returns:
            The created incident record from ServiceNow.
        """
        response = self._client.post(
            "/table/incident",
            json={
                "short_description": short_description,
                "description": description,
                "urgency": str(urgency),
                "category": category,
                "subcategory": "Network Equipment",
            },
        )
        response.raise_for_status()
        return response.json()["result"]

    def update_incident(self, sys_id: str, work_notes: str | None = None, state: int | None = None) -> dict:
        """Update an existing incident.

        Args:
            sys_id: ServiceNow sys_id of the incident.
            work_notes: Optional work notes to add.
            state: Optional state change (1=New, 2=InProgress, 6=Resolved, 7=Closed).

        Returns:
            The updated incident record from ServiceNow.
        """
        payload: dict[str, Any] = {}
        if work_notes:
            payload["work_notes"] = work_notes
        if state is not None:
            payload["state"] = str(state)
        response = self._client.patch(f"/table/incident/{sys_id}", json=payload)
        response.raise_for_status()
        return response.json()["result"]

    def create_or_update_ci(self, name: str, data: dict, ci_class: str = "cmdb_ci_ip_switch") -> dict:
        """Create or update a Configuration Item in CMDB.

        Searches for an existing CI by name; updates it if found, otherwise creates a new one.

        Args:
            name: CI name (typically the device hostname).
            data: CI fields to set (serial_number, os_version, model_id, ip_address, etc.).
            ci_class: CMDB CI class name (default: cmdb_ci_ip_switch).

        Returns:
            The created or updated CI record from ServiceNow.
        """
        table_path = f"/table/{ci_class}"
        existing = self._client.get(table_path, params={"sysparm_query": f"name={name}"})
        existing.raise_for_status()
        records = existing.json()["result"]
        if records:
            response = self._client.patch(f"{table_path}/{records[0]['sys_id']}", json=data)
        else:
            data["name"] = name
            response = self._client.post(table_path, json=data)
        response.raise_for_status()
        return response.json()["result"]


def _get_snow_client() -> ServiceNowClient:
    """Get an authenticated ServiceNow REST API client.

    Returns:
        ServiceNowClient configured with instance, username, and password from settings.

    Raises:
        RuntimeError: If SNOW_INSTANCE, SNOW_USERNAME, or SNOW_PASSWORD is not configured.
    """
    if not settings.snow_instance or not settings.snow_username or not settings.snow_password:
        raise RuntimeError("SNOW_INSTANCE, SNOW_USERNAME, and SNOW_PASSWORD must be set")
    return ServiceNowClient(
        settings.snow_instance,
        settings.snow_username,
        settings.snow_password.get_secret_value(),
    )


@mcp.tool(annotations=WRITE_SAFE)
def net_create_incident(
    host: str,
    short_description: str,
    urgency: int = 2,
    include_diagnostics: bool = True,
) -> dict[str, Any]:
    """[WRITE] Create a ServiceNow incident for a network device issue.

    Creates a new incident in ServiceNow linked to the specified network device.
    Optionally attaches live device health diagnostics as the incident description.

    Args:
        host: Device hostname/IP that has the issue.
        short_description: Brief description of the issue.
        urgency: Urgency level (1=High, 2=Medium, 3=Low).
        include_diagnostics: If True, attach device health snapshot as work notes.
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import SERVICENOW_CREATE_INCIDENT_RESPONSE

        return {**copy.deepcopy(SERVICENOW_CREATE_INCIDENT_RESPONSE), "device": host}

    if not settings.snow_instance or not settings.snow_username or not settings.snow_password:
        return {**_SNOW_NOT_CONFIGURED, "device": host}

    try:
        snow = _get_snow_client()

        description = f"Network device: {host}\n{short_description}"
        if include_diagnostics:
            from network_mcp.tools.common.device import net_get_device_info

            diag = net_get_device_info(host)
            if diag["status"] == "success":
                description += f"\n\nDevice diagnostics:\n{diag['data']}"

        result = snow.create_incident(short_description, description, urgency)
        return {
            "status": "success",
            "device": host,
            "incident_number": result.get("number"),
            "sys_id": result.get("sys_id"),
        }
    except httpx.HTTPStatusError as e:
        return {"status": "error", "device": host, "error": f"ServiceNow API error: {e.response.status_code}"}
    except httpx.ConnectError:
        return {"status": "error", "device": host, "error": "Cannot connect to ServiceNow instance"}
    except Exception as e:
        return {"status": "error", "device": host, "error": f"ServiceNow incident creation failed: {e}"}


@mcp.tool(annotations=WRITE_SAFE)
def net_sync_cmdb(host: str) -> dict[str, Any]:
    """[WRITE] Sync device inventory to ServiceNow CMDB.

    Queries the live device for facts (hostname, serial, model, OS version) and
    creates or updates the corresponding Configuration Item in ServiceNow CMDB.

    Args:
        host: Device hostname/IP to sync to CMDB.
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import SERVICENOW_SYNC_CMDB_RESPONSE

        return {**copy.deepcopy(SERVICENOW_SYNC_CMDB_RESPONSE), "device": host}

    if not settings.snow_instance or not settings.snow_username or not settings.snow_password:
        return {**_SNOW_NOT_CONFIGURED, "device": host}

    try:
        snow = _get_snow_client()

        from network_mcp.tools.common.device import net_get_device_info

        facts = net_get_device_info(host)
        if facts["status"] != "success":
            return facts

        data = facts["data"]
        ci_data = {
            "serial_number": data.get("serial_number", ""),
            "os_version": data.get("version", ""),
            "model_id": data.get("model", ""),
            "ip_address": host,
        }
        result = snow.create_or_update_ci(data.get("hostname", host), ci_data, ci_class=settings.net_snow_ci_class)
        return {
            "status": "success",
            "device": host,
            "cmdb_sys_id": result.get("sys_id"),
            "action": "updated" if result.get("sys_updated_on") else "created",
        }
    except httpx.HTTPStatusError as e:
        return {"status": "error", "device": host, "error": f"ServiceNow API error: {e.response.status_code}"}
    except httpx.ConnectError:
        return {"status": "error", "device": host, "error": "Cannot connect to ServiceNow instance"}
    except Exception as e:
        return {"status": "error", "device": host, "error": f"ServiceNow CMDB sync failed: {e}"}


@mcp.tool(annotations=WRITE_SAFE)
def net_update_incident(
    host: str,
    sys_id: str,
    work_notes: str | None = None,
    state: int | None = None,
) -> dict[str, Any]:
    """[WRITE] Update an existing ServiceNow incident with work notes or state change.

    Args:
        host: Device hostname/IP associated with the incident.
        sys_id: ServiceNow sys_id of the incident to update.
        work_notes: Optional work notes to add to the incident.
        state: Optional state change (1=New, 2=InProgress, 6=Resolved, 7=Closed).
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import SERVICENOW_UPDATE_INCIDENT_RESPONSE

        return {**copy.deepcopy(SERVICENOW_UPDATE_INCIDENT_RESPONSE), "device": host}

    if not settings.snow_instance or not settings.snow_username or not settings.snow_password:
        return {**_SNOW_NOT_CONFIGURED, "device": host}

    if not work_notes and state is None:
        return {"status": "error", "device": host, "error": "At least one of work_notes or state must be provided"}

    try:
        snow = _get_snow_client()
        result = snow.update_incident(sys_id, work_notes=work_notes, state=state)
        return {
            "status": "success",
            "device": host,
            "incident_number": result.get("number"),
            "sys_id": result.get("sys_id"),
            "state": result.get("state"),
        }
    except httpx.HTTPStatusError as e:
        return {"status": "error", "device": host, "error": f"ServiceNow API error: {e.response.status_code}"}
    except httpx.ConnectError:
        return {"status": "error", "device": host, "error": "Cannot connect to ServiceNow instance"}
    except Exception as e:
        return {"status": "error", "device": host, "error": f"ServiceNow incident update failed: {e}"}


@mcp.tool(annotations=WRITE_SAFE)
def net_validate_change(
    host: str,
    change_sys_id: str,
    pre_snapshot_commands: list[str] | None = None,
    post_snapshot_commands: list[str] | None = None,
) -> dict[str, Any]:
    """[WRITE] Validate a network change by comparing pre/post device state.

    Captures device state before and after a change using specified show commands,
    compares the snapshots, and attaches the diff summary to the ServiceNow change
    ticket as work notes.

    Call once with pre_snapshot_commands to capture pre-change state, then call again
    with post_snapshot_commands after the change to compare and update the ticket.

    Args:
        host: Device hostname/IP where the change is being made.
        change_sys_id: ServiceNow sys_id of the change request.
        pre_snapshot_commands: Show commands to capture pre-change state.
        post_snapshot_commands: Show commands to capture post-change state.
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import SERVICENOW_VALIDATE_CHANGE_RESPONSE

        return {**copy.deepcopy(SERVICENOW_VALIDATE_CHANGE_RESPONSE), "device": host}

    if not settings.snow_instance or not settings.snow_username or not settings.snow_password:
        return {**_SNOW_NOT_CONFIGURED, "device": host}

    if not pre_snapshot_commands and not post_snapshot_commands:
        return {
            "status": "error",
            "device": host,
            "error": "At least one of pre_snapshot_commands or post_snapshot_commands must be provided",
        }

    try:
        snow = _get_snow_client()
        snapshots: dict[str, Any] = {}

        if pre_snapshot_commands:
            pre_result = run_show_command(conn_mgr, host, pre_snapshot_commands)
            if pre_result["status"] == "error":
                return pre_result
            snapshots["pre"] = {cmd: data for cmd, data in zip(pre_snapshot_commands, pre_result["data"], strict=False)}
            snow.update_incident(
                change_sys_id,
                work_notes=f"[Change Validation] Pre-change snapshot captured for {host}",
            )
            return {
                "status": "success",
                "device": host,
                "phase": "pre",
                "change_sys_id": change_sys_id,
                "snapshot": snapshots["pre"],
            }

        if post_snapshot_commands:
            post_result = run_show_command(conn_mgr, host, post_snapshot_commands)
            if post_result["status"] == "error":
                return post_result
            snapshots["post"] = {
                cmd: data for cmd, data in zip(post_snapshot_commands, post_result["data"], strict=False)
            }

            # Build a diff summary comparing pre and post output
            diff_lines: list[str] = []
            for cmd in post_snapshot_commands:
                post_data = snapshots["post"].get(cmd, {})
                diff_lines.append(f"Command: {cmd}")
                diff_lines.append(f"  Output: {post_data}")

            diff_summary = "\n".join(diff_lines) if diff_lines else "No changes detected"

            snow.update_incident(
                change_sys_id,
                work_notes=(f"[Change Validation] Post-change snapshot for {host}:\n{diff_summary}"),
            )
            return {
                "status": "success",
                "device": host,
                "phase": "post",
                "change_sys_id": change_sys_id,
                "snapshot": snapshots["post"],
                "diff_summary": diff_summary,
            }

        return {"status": "error", "device": host, "error": "Unexpected state"}  # pragma: no cover
    except httpx.HTTPStatusError as e:
        return {"status": "error", "device": host, "error": f"ServiceNow API error: {e.response.status_code}"}
    except httpx.ConnectError:
        return {"status": "error", "device": host, "error": "Cannot connect to ServiceNow instance"}
    except Exception as e:
        return {"status": "error", "device": host, "error": f"Change validation failed: {e}"}
