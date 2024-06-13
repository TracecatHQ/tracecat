"""Sentinel One integration.

Authentication method: Token

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

import datetime
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, registry

ALERTS_ENDPOINT = "/web/api/v2.1/cloud-detection/alerts"


@registry.register(
    description="Fetch all Sentinel One alerts.",
    namespace="sentinelone",
)
async def list_sentinelone_alerts(
    base_url: Annotated[
        str, Field(..., description="The base URL for the Sentinel One API")
    ],
    api_token: Annotated[
        str, Field(..., description="The API token for Sentinel One API")
    ],
    start_time: Annotated[
        datetime.datetime, Field(..., description="The start time for the alerts")
    ],
    end_time: Annotated[
        datetime.datetime, Field(..., description="The end time for the alerts")
    ],
    limit: Annotated[
        int, Field(default=1000, description="The maximum number of alerts to return")
    ] = 1000,
) -> list[dict[str, Any]]:
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
