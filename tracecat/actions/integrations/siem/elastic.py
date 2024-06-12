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
from typing import Any

import httpx


async def list_elastic_alerts(
    api_key: str,
    api_url: str,
    start_date: datetime,
    end_date: datetime,
    # TODO: Missing pagination, we assume that the limit is enough for now
    limit: int = 1000,  # Technically, the limit is 10000, but we set it to 1000 for now
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
