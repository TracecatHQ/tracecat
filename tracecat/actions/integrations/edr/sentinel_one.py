"""Sentinel One integration.

Authentication method: Token

Requires: A secret named `sentinel_one` with the following keys:
- `SENTINEL_ONE_BASE_URL`
- `SENTINEL_ONE_API_TOKEN`

References: https://github.com/criblio/collector-templates/tree/main/collectors/rest/sentinel_one

Supported APIs:

```python
list_alerts: {
    "endpoint": "/web/api/v2.1/cloud-detection/alerts",
    "method": "GET",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://github.com/criblio/collector-templates/tree/main/collectors/rest/sentinel_one"
}
```
"""

import os
from datetime import datetime
from typing import Annotated, Any, Literal

import httpx

from tracecat.registry import Field, RegistrySecret, registry

ALERTS_ENDPOINT = "/cloud-detection/alerts"
ANALYST_VERDICT_ENDPOINT = "/cloud-detection/alerts/analyst-verdict"
AGENT_ENDPOINT = "/agents"
DISCONNECT_ENDPOINT = "/agents/actions/disconnect"
CONNECT_ENDPOINT = "/agents/actions/connect"
FIREWALL_CONTROL_ENDPOINT = "/firewall-control"

AnalystVerdict = Literal["FALSE_POSITIVE", "SUSPICIOUS", "TRUE_POSITIVE", "UNDEFINED"]

sentinel_one_secret = RegistrySecret(
    name="sentinel_one",
    keys=["SENTINEL_ONE_BASE_URL", "SENTINEL_ONE_API_TOKEN"],
)
"""Sentinel One secret.

- name: `sentinel_one`
- keys:
    - `SENTINEL_ONE_BASE_URL`
    - `SENTINEL_ONE_API_TOKEN`
"""


def create_sentinel_one_client(api_version: str = "v2.1") -> httpx.AsyncClient:
    SENTINEL_ONE_API_TOKEN = os.getenv("SENTINEL_ONE_API_TOKEN")
    if SENTINEL_ONE_API_TOKEN is None:
        raise ValueError("SENTINEL_ONE_API_TOKEN is not set")
    client = httpx.AsyncClient(
        base_url=f'{os.getenv("SENTINEL_ONE_BASE_URL")}/web/api/{api_version}',
        headers={
            "Authorization": f"ApiToken {SENTINEL_ONE_API_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    return client


@registry.register(
    default_title="List Sentinel One alerts",
    description="Fetch all Sentinel One alerts and filter by time range.",
    display_group="Sentinel One",
    namespace="integrations.sentinel_one",
    secrets=[sentinel_one_secret],
)
async def list_sentinelone_alerts(
    start_time: Annotated[
        datetime,
        Field(..., description="Start time, return alerts created after this time."),
    ],
    end_time: Annotated[
        datetime,
        Field(..., description="End time, return alerts created before this time."),
    ],
    limit: Annotated[
        int, Field(default=1000, description="Maximum number of alerts to return.")
    ] = 1000,
) -> list[dict[str, Any]]:
    params = {
        "createdAt__gte": start_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "createdAt__lte": end_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "limit": limit,
    }

    async with create_sentinel_one_client() as client:
        response = await client.get(
            ALERTS_ENDPOINT,
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Update Sentinel One alert status",
    description="Update the analyst verdict of Sentinel One alerts.",
    display_group="Sentinel One",
    namespace="integrations.sentinel_one",
    secrets=[sentinel_one_secret],
)
async def update_sentinelone_alert_status(
    alert_ids: Annotated[
        list[str], Field(..., description="List of alert IDs to update")
    ],
    status: Annotated[
        AnalystVerdict, Field(..., description="New status for the alerts")
    ],
) -> dict[str, Any]:
    async with create_sentinel_one_client() as client:
        response = await client.post(
            ANALYST_VERDICT_ENDPOINT,
            json={
                "data": {"analystVerdict": status},
                "filter": {"ids": alert_ids},
            },
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get Sentinel One agents by username",
    description="Find Sentinel One agent(s) by the last used username field",
    display_group="Sentinel One",
    namespace="integrations.sentinel_one",
    secrets=[sentinel_one_secret],
)
async def get_sentinelone_agents_by_username(
    username: Annotated[str, Field(..., description="Username to search for")],
    exact_match: Annotated[
        bool,
        Field(
            ..., description="Exact match only, otherwise partial matches are returned"
        ),
    ],
) -> list[dict[str, Any]]:
    async with create_sentinel_one_client() as client:
        params = {
            "lastLoggedInUserName__contains": username,
        }
        response = await client.get(
            AGENT_ENDPOINT,
            params=params,
        )
        response.raise_for_status()
        results = []
        if exact_match:
            for agent in response.json()["data"]:
                if agent["lastLoggedInUserName"].lower() == username.lower():
                    results.append(agent)
        else:
            results = response.json()["data"]

        return results


@registry.register(
    default_title="Get Sentinel One agents by hostname",
    description="Find Sentinel One agent(s) by hostname",
    display_group="Sentinel One",
    namespace="integrations.sentinel_one",
    secrets=[sentinel_one_secret],
)
async def get_sentinelone_agents_by_hostname(
    hostname: Annotated[str, Field(..., description="Hostname to search for")],
    exact_match: Annotated[
        bool,
        Field(
            ..., description="Exact match only, otherwise partial matches are returned"
        ),
    ],
) -> list[dict[str, Any]]:
    async with create_sentinel_one_client() as client:
        query_param = "computerName__contains"
        if exact_match:
            query_param = "computerName"
        params = {query_param: hostname}
        response = await client.get(AGENT_ENDPOINT, params=params)
        response.raise_for_status()
        return response.json()["data"]


@registry.register(
    default_title="Isolate Sentinel One agent",
    description="Isolate a Sentinel One agent from the network",
    display_group="Sentinel One",
    namespace="integrations.sentinel_one",
    secrets=[sentinel_one_secret],
)
async def isolate_sentinelone_agent(
    agent_id: Annotated[str, Field(..., description="ID of the agent to isolate")],
) -> dict[str, Any]:
    async with create_sentinel_one_client() as client:
        response = await client.post(
            DISCONNECT_ENDPOINT,
            json={"filter": {"ids": [agent_id]}},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Unisolate Sentinel One agent",
    description="Unisolate a Sentinel One agent from the network",
    display_group="Sentinel One",
    namespace="integrations.sentinel_one",
    secrets=[sentinel_one_secret],
)
async def unisolate_sentinelone_agent(
    agent_id: Annotated[str, Field(..., description="ID of the agent to unisolate")],
) -> dict[str, Any]:
    async with create_sentinel_one_client() as client:
        response = await client.post(
            CONNECT_ENDPOINT,
            json={"filter": {"ids": [agent_id]}},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get Sentinel One firewall rule",
    description="",
    display_group="Sentinel One",
    namespace="integrations.sentinel_one",
    secrets=[sentinel_one_secret],
)
async def get_sentinel_one_firewall_rule(
    rule_id: Annotated[str, Field(..., description="ID of the rule to get")],
    scope_type: Literal["accountIds", "siteIds", "groupIds"],
    scope_id: Annotated[
        str, Field(..., description="ID of the scope the rule is attached to")
    ],
) -> dict[str, Any]:
    async with create_sentinel_one_client() as client:
        params = {"ids": rule_id, scope_type: scope_id}
        response = await client.get(FIREWALL_CONTROL_ENDPOINT, params=params)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Update Sentinel One firewall rule",
    description="Update an existing Sentinel One firewall rule",
    display_group="Sentinel One",
    namespace="integrations.sentinel_one",
    secrets=[sentinel_one_secret],
)
async def update_sentinel_one_firewall_rule(
    rule_id: Annotated[str, Field(..., description="Existing rule ID")],
    rule_data: Annotated[
        dict[str, Any], Field(..., description="Updated rule data to set")
    ],
) -> dict[str, Any]:
    async with create_sentinel_one_client() as client:
        response = await client.put(
            f"{FIREWALL_CONTROL_ENDPOINT}/{rule_id}", json={"data": rule_data}
        )
        response.raise_for_status()
        return response.json()
