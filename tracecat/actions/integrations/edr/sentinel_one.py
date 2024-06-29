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

ALERTS_ENDPOINT = "/web/api/v2.1/cloud-detection/alerts"
ANALYST_VERDICT_ENDPOINT = "/web/api/v2.1/cloud-detection/alerts/analyst-verdict"

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
    api_token = os.getenv("SENTINEL_ONE_API_TOKEN")
    base_url = os.getenv("SENTINEL_ONE_BASE_URL")
    headers = {
        "Authorization": f"ApiToken {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    params = {
        "createdAt__gte": start_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "createdAt__lte": end_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "limit": limit,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/{ALERTS_ENDPOINT}",
            headers=headers,
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
    api_token = os.getenv("SENTINEL_ONE_API_TOKEN")
    base_url = os.getenv("SENTINEL_ONE_BASE_URL")
    headers = {
        "Authorization": f"ApiToken {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/{ANALYST_VERDICT_ENDPOINT}",
            headers=headers,
            json={
                "data": {"analystVerdict": status},
                "filter": {"ids": alert_ids},
            },
        )
        response.raise_for_status()
        return response.json()
