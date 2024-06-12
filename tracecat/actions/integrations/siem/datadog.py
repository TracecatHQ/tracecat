"""Datadog SIEM integration.

Authentication method: Token-based

Requires:

- DD_APP_KEY: Datadog application key
- DD_API_KEY: Datadog API key

Scopes:

- `list_alerts`: security_monitoring_signals_read

References: https://docs.datadoghq.com/api/latest/security-monitoring

Supported APIs:

```python
list_alerts = {
    "endpoint": "/v2/security_monitoring/signals",
    "method": "GET",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://docs.datadoghq.com/api/latest/security-monitoring/#get-a-quick-list-of-security-signals"
}
```
"""

from datetime import datetime
from typing import Any

import httpx
from fastapi.exceptions import HTTPException

DD_REGION_TO_API_URL = {
    "us1": "https://api.datadoghq.com",
    "us3": "https://api.us3.datadoghq.com",
    "us5": "https://api.us5.datadoghq.com",
    "eu": "https://api.datadoghq.eu",
    "ap1": "https://api.ap1.datadoghq.com",
}


async def list_datadog_alerts(
    app_key: str,
    api_key: str,
    region: str,
    start_time: datetime,
    end_time: datetime,
    # NOTE: Should be 1000 (mentioned in another endpoint "List Findings")
    # but there's no clear documentation on the limit for "List Signals"
    limit: int = 1000,
) -> list[dict[str, Any]]:
    dt_format = "%Y-%m-%dT%H:%M:%S+00:00"
    headers = {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
    }
    try:
        api_url = DD_REGION_TO_API_URL[region]
    except KeyError as err:
        raise HTTPException(
            status_code=400, detail=f"Invalid Datadog region: {region}"
        ) from err

    # TODO: Add support for pagination
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url=api_url,
            headers=headers,
            params={
                "filter[from]": start_time.strftime(dt_format),
                "filter[to]": end_time.strftime(dt_format),
                "page[limit]": limit,
            },
        )
        response.raise_for_status()

    return response.json()
