"""Datadog SIEM integration.

Authentication method: Token-based

Requires: secret named `datadog` with the following keys:
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
from typing import Annotated, Any

import httpx
from fastapi.exceptions import HTTPException

from tracecat.registry import Field, registry

DD_REGION_TO_API_URL = {
    "us1": "https://api.datadoghq.com/api",
    "us3": "https://api.us3.datadoghq.com/api",
    "us5": "https://api.us5.datadoghq.com/api",
    "eu": "https://api.datadoghq.eu/api",
    "ap1": "https://api.ap1.datadoghq.com/api",
}


@registry.register(
    default_title="List Datadog SIEM alerts",
    description="List Datadog SIEM alerts (signals)",
    display_group="SIEM",
    namespace="integrations.datadog.siem.list_datadog_alerts",
    secrets=["datadog"],
)
async def list_datadog_alerts(
    app_key: Annotated[
        str, Field(..., description="The application key for Datadog API")
    ],
    api_key: Annotated[str, Field(..., description="The API key for Datadog API")],
    region: Annotated[
        str, Field(..., description="The Datadog region (e.g., us1, us3, us5, eu, ap1)")
    ],
    start_time: Annotated[
        datetime, Field(..., description="The start time for the alerts")
    ],
    end_time: Annotated[
        datetime, Field(..., description="The end time for the alerts")
    ],
    limit: Annotated[
        int, Field(default=1000, description="The maximum number of alerts to return")
    ] = 1000,
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
    async with httpx.AsyncClient(base_url=api_url, allow_redirects=True) as client:
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

    return response.json()
