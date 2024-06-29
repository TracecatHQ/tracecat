"""Datadog SIEM integration.

Authentication method: Token-based

Requires: secret named `datadog` with the following keys:
- `DD_APP_KEY`: Datadog application key
- `DD_API_KEY`: Datadog API key
- `DD_REGION`: Datadog region (e.g., us1, us3, us5, eu, ap1)

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

import os
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi.exceptions import HTTPException

from tracecat.registry import Field, RegistrySecret, registry

DD_REGION_TO_API_URL = {
    "us1": "https://api.datadoghq.com/api",
    "us3": "https://api.us3.datadoghq.com/api",
    "us5": "https://api.us5.datadoghq.com/api",
    "eu": "https://api.datadoghq.eu/api",
    "ap1": "https://api.ap1.datadoghq.com/api",
}

datadog_secret = RegistrySecret(
    name="datadog",
    keys=["DD_APP_KEY", "DD_API_KEY", "DD_REGION"],
)
"""Datadog secret.

Secret
------
- name: `datadog`
- keys:
    - `DD_APP_KEY`
    - `DD_API_KEY`
    - `DD_REGION`

Example Usage
-------------
Environment variables:
>>> os.environ["DD_APP_KEY"]

Expression:
>>> ${{ SECRETS.datadog.DD_APP_KEY }}
"""


@registry.register(
    default_title="List Datadog SIEM alerts",
    description="Fetch Datadog SIEM alerts (signals) and filter by time range.",
    display_group="Datadog",
    namespace="integrations.datadog",
    secrets=[datadog_secret],
)
async def list_datadog_alerts(
    start_time: Annotated[
        datetime,
        Field(..., description="Start time, return alerts created after this time."),
    ],
    end_time: Annotated[
        datetime,
        Field(..., description="End time, return alerts created before this time."),
    ],
    limit: Annotated[
        int, Field(default=1000, description="The maximum number of alerts to return")
    ] = 1000,
) -> list[dict[str, Any]]:
    api_key = os.getenv("DD_API_KEY")
    app_key = os.getenv("DD_APP_KEY")
    region = os.getenv("DD_REGION")

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
    async with httpx.AsyncClient(base_url=api_url, follow_redirects=True) as client:
        response = await client.get(
            "/v2/security_monitoring/signals",
            headers=headers,
            params={
                "filter[from]": start_time.strftime(dt_format),
                "filter[to]": end_time.strftime(dt_format),
                "page[limit]": limit,
            },
        )
        response.raise_for_status()

    return response.json().get("data", [])
