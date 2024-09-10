"""Elastic Security integration.

Authentication method: Token-based (with username and password to generate the token in the UI)

Required resource: secret named `elastic` with the following keys:
- `ELASTIC_API_KEY`: Elastic Security API key
- `ELASTIC_API_URL`: Elastic Security API URL

Supported APIs:

```python
list_alerts = {
    "endpoint": "<kibana host>:<port>/api/detection_engine/signals/search",
    "method": "POST",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://www.elastic.co/guide/en/security/current/signals-api-overview.html#_get_alerts,
}
update_alerts = {
    "endpoint": "<kibana host>:<port>//api/detection_engine/signals/status",
    "method": "POST",
    "reference": "https://www.elastic.co/guide/en/security/current/signals-api-overview.html#_set_alert_status",
}
```
"""

import os
from datetime import datetime
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, RegistrySecret, registry

elastic_secret = RegistrySecret(
    name="elastic",
    keys=["ELASTIC_API_KEY", "ELASTIC_API_URL"],
)
"""Elastic secret.

Secret
------
- name: `elastic`
- keys:
    - `ELASTIC_API_KEY`
    - `ELASTIC_API_URL`

Example Usage
-------------
Environment variable:
>>> os.environ["ELASTIC_API_KEY"]

Expression:
>>> ${{ SECRETS.elastic.ELASTIC_API_KEY }}
"""


@registry.register(
    default_title="List Elastic Security alerts",
    description="Fetch all alerts from Elastic Security and filter by time range.",
    display_group="Elastic",
    namespace="integrations.elastic",
    secrets=[elastic_secret],
)
async def list_elastic_alerts(
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
    api_key = os.getenv("ELASTIC_API_KEY")
    api_url = os.getenv("ELASTIC_API_URL")

    url = f"{api_url}/api/detection_engine/signals/search"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"ApiKey {api_key}",
        "kbn-xsrf": "kibana",
    }
    query = {
        "size": limit,
        "query": {
            "bool": {
                "filter": [
                    {
                        "range": {
                            "@timestamp": {
                                "gte": start_time.isoformat(),
                                "lte": end_time.isoformat(),
                            }
                        }
                    },
                    {"match": {"signal.status": "open"}},
                ],
                "must_not": [{"exists": {"field": "kibana.alert.building_block_type"}}],
            }
        },
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=query)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()


@registry.register(
    default_title="Update Elastic Security Alert Status",
    description="Updates the status of Elastic Security Alerts.",
    display_group="Elastic",
    namespace="integrations.elastic",
    secrets=[elastic_secret],
)
async def update_elastic_alert_status(
    alert_input: Annotated[
        list[str] | dict[str, Any],
        Field(..., description="Either a list of of Alert IDs OR an Elastic query."),
    ],
    status: Annotated[
        str,
        Field(
            ...,
            description="The desired status for the alert ('open', 'acknowledged', 'closed')",
        ),
    ],
) -> dict[str, Any]:
    api_key = os.getenv("ELASTIC_API_KEY")
    api_url = os.getenv("ELASTIC_API_URL")
    url = f"{api_url}/api/detection_engine/signals/status"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"ApiKey {api_key}",
        "kbn-xsrf": "kibana",
    }

    payload = {"status": status}

    if isinstance(alert_input, list):
        payload["signal_ids"] = alert_input  # Add signal_ids if it's a list
    else:
        payload.update(alert_input)  # Unpack the query dictionary

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
