"""Panther REST API integrations for alerts and queries."""

from typing import Annotated, Any, Literal

import httpx
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

panther_secret = RegistrySecret(name="panther", keys=["PANTHER_API_KEY"])


async def _request(
    method: str,
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make authenticated request to Panther API."""
    api_key = secrets.get("PANTHER_API_KEY")
    url = f"{base_url.rstrip('/')}/v1{path}"
    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=payload,
        )
        response.raise_for_status()
        if not response.content:
            return {"status": "success", "status_code": response.status_code}
        return response.json()


# =============================================================================
# Alerts
# =============================================================================


@registry.register(
    default_title="List Panther alerts",
    description="List alerts from Panther with optional filters.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def list_alerts(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    status: Annotated[
        Literal["OPEN", "TRIAGED", "CLOSED"] | None,
        Doc("Filter by alert status."),
    ] = None,
    severity: Annotated[
        Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] | None,
        Doc("Filter by alert severity."),
    ] = None,
    limit: Annotated[int | None, Doc("Maximum number of alerts to return.")] = None,
    cursor: Annotated[str | None, Doc("Pagination cursor for next page.")] = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if status:
        params["status"] = status
    if severity:
        params["severity"] = severity
    if limit:
        params["limit"] = limit
    if cursor:
        params["cursor"] = cursor
    return await _request("GET", base_url, "/alerts", params=params or None)


@registry.register(
    default_title="Get Panther alert",
    description="Get a single alert from Panther by ID.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def get_alert(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    alert_id: Annotated[str, Doc("The unique identifier of the alert.")],
) -> dict[str, Any]:
    return await _request("GET", base_url, f"/alerts/{alert_id}")


@registry.register(
    default_title="Update Panther alert",
    description="Update a Panther alert's status or assignee.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def update_alert(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    alert_id: Annotated[str, Doc("The unique identifier of the alert.")],
    status: Annotated[
        Literal["OPEN", "TRIAGED", "CLOSED"] | None,
        Doc("New status for the alert."),
    ] = None,
    assignee: Annotated[str | None, Doc("User ID to assign the alert to.")] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if status:
        payload["status"] = status
    if assignee:
        payload["assignee"] = assignee
    return await _request("PATCH", base_url, f"/alerts/{alert_id}", payload=payload)


@registry.register(
    default_title="Bulk update Panther alerts",
    description="Update multiple Panther alerts at once.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def bulk_update_alerts(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    alert_ids: Annotated[list[str], Doc("List of alert IDs to update.")],
    status: Annotated[
        Literal["OPEN", "TRIAGED", "CLOSED"] | None,
        Doc("New status for the alerts."),
    ] = None,
    assignee: Annotated[str | None, Doc("User ID to assign the alerts to.")] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"ids": alert_ids}
    if status:
        payload["status"] = status
    if assignee:
        payload["assignee"] = assignee
    return await _request("PATCH", base_url, "/alerts", payload=payload)


# =============================================================================
# Queries
# =============================================================================


@registry.register(
    default_title="List Panther queries",
    description="List saved queries from Panther.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def list_queries(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    limit: Annotated[int | None, Doc("Maximum number of queries to return.")] = None,
    cursor: Annotated[str | None, Doc("Pagination cursor for next page.")] = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if limit:
        params["limit"] = limit
    if cursor:
        params["cursor"] = cursor
    return await _request("GET", base_url, "/queries", params=params or None)


@registry.register(
    default_title="Get Panther query",
    description="Get a saved query from Panther by ID.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def get_query(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    query_id: Annotated[str, Doc("The unique identifier of the saved query.")],
) -> dict[str, Any]:
    return await _request("GET", base_url, f"/queries/{query_id}")


@registry.register(
    default_title="Create Panther query",
    description="Create a new saved query in Panther.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def create_query(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    name: Annotated[str, Doc("Name of the saved query.")],
    sql: Annotated[str, Doc("SQL query to save. Must be valid SQL, not PantherFlow.")],
    description: Annotated[str | None, Doc("Description of the query.")] = None,
    schedule: Annotated[
        dict[str, Any] | None,
        Doc("Schedule configuration for the query."),
    ] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "sql": sql}
    if description:
        payload["description"] = description
    if schedule:
        payload["schedule"] = schedule
    return await _request("POST", base_url, "/queries", payload=payload)


@registry.register(
    default_title="Update Panther query",
    description="Update a saved query in Panther.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def update_query(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    query_id: Annotated[str, Doc("The unique identifier of the saved query.")],
    name: Annotated[str | None, Doc("New name for the query.")] = None,
    sql: Annotated[str | None, Doc("New SQL query. Must be valid SQL.")] = None,
    description: Annotated[str | None, Doc("New description for the query.")] = None,
    schedule: Annotated[
        dict[str, Any] | None,
        Doc("New schedule configuration for the query."),
    ] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    if sql:
        payload["sql"] = sql
    if description:
        payload["description"] = description
    if schedule:
        payload["schedule"] = schedule
    return await _request("PUT", base_url, f"/queries/{query_id}", payload=payload)


@registry.register(
    default_title="Delete Panther query",
    description="Delete a saved query from Panther.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def delete_query(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    query_id: Annotated[str, Doc("The unique identifier of the saved query.")],
) -> dict[str, Any]:
    return await _request("DELETE", base_url, f"/queries/{query_id}")


@registry.register(
    default_title="Execute Panther query",
    description="Execute a saved query in Panther.",
    display_group="Panther",
    namespace="tools.panther",
    secrets=[panther_secret],
)
async def execute_query(
    base_url: Annotated[str, Doc("Panther API URL (e.g. https://api.runpanther.net).")],
    query_id: Annotated[str, Doc("The unique identifier of the saved query.")],
    parameters: Annotated[
        dict[str, Any] | None,
        Doc("Parameters to pass to the query."),
    ] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if parameters:
        payload["parameters"] = parameters
    return await _request(
        "POST", base_url, f"/queries/{query_id}/execute", payload=payload or None
    )
