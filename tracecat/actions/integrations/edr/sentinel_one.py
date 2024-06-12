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

import httpx

ALERTS_ENDPOINT = "/web/api/v2.1/cloud-detection/alerts"


async def list_sentinelone_alerts(
    base_url: str,
    api_token: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 1000,
):
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
