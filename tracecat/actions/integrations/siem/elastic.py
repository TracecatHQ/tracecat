"""Elastic Security integration.

Authentication method: Token-based (with username and password to generate the token in the UI)

Required resource: Elastic Security

Supported APIs:

```python
list_alerts = {
    "endpoint": "<kibana host>:<port>/api/detection_engine/signals/search",
    "method": "POST",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://www.elastic.co/guide/en/security/current/signals-api-overview.html#_get_alerts,
}
```
"""

from datetime import datetime
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, registry


@registry.register(
    description="Fetch all alerts from Elastic Security (SIEM).",
    namespace="elastic_security",
)
async def list_elastic_alerts(
    api_key: Annotated[str, Field(..., description="The API key for Elastic Security")],
    api_url: Annotated[
        str, Field(..., description="The base URL for the Elastic Security API")
    ],
    start_date: Annotated[
        datetime, Field(..., description="The start date for the alerts")
    ],
    end_date: Annotated[
        datetime, Field(..., description="The end date for the alerts")
    ],
    limit: Annotated[
        int, Field(default=1000, description="The maximum number of alerts to return")
    ] = 1000,
) -> list[dict[str, Any]]:
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
                                "gte": start_date.isoformat(),
                                "lte": end_date.isoformat(),
                            }
                        }
                    },
                    {"match": {"signal.status": "open"}},
                ]
            }
        },
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=query)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()
