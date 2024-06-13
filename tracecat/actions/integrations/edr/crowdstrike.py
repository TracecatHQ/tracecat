"""Crowdstrike integration.

Authentication method: OAuth 2.0

References:

- https://falconpy.io/Service-Collections/Alerts.html
- https://www.crowdstrike.com/blog/tech-center/get-access-falcon-apis/

Supported APIs:

```python
list_alerts = {
    "endpoint": "/alerts/queries/alerts/v2",
    "method": "GET",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://falconpy.io/Service-Collections/Alerts.html#getqueriesalertsv2"
}

list_detections = {
    "endpoint": "/detects/queries/detects/v1",
    "method": "GET",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://falconpy.io/Service-Collections/Detects.html#querydetects"
}
```
"""

import datetime
from typing import Annotated, Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from tracecat.registry import Field, registry

TOKEN_ENDPOINT = "/oauth2/token"
ALERTS_ENDPOINT = "/alerts/queries/alerts/v2"
DETECTS_ENDPOINT = "/detects/queries/detects/v1"


@registry.register(
    description="Fetch all Crowdstrike alerts from Falcon SIEM.",
    namespace="crowdstrike",
)
async def list_crowdstrike_alerts(
    base_url: Annotated[
        str, Field(..., description="The base URL for the CrowdStrike API")
    ],
    client_id: Annotated[
        str, Field(..., description="The client ID for CrowdStrike API")
    ],
    client_secret: Annotated[
        str, Field(..., description="The client secret for CrowdStrike API")
    ],
    start_time: Annotated[
        datetime.datetime, Field(..., description="The start time for the alerts")
    ],
    end_time: Annotated[
        datetime.datetime, Field(..., description="The end time for the alerts")
    ],
    limit: Annotated[
        int, Field(default=9999, description="The maximum number of alerts to return")
    ] = 9999,
) -> list[dict[str, Any]]:
    async with AsyncOAuth2Client(
        client_id=client_id, client_secret=client_secret
    ) as client:
        token_response = await client.fetch_token(
            url=f"{base_url}/{TOKEN_ENDPOINT}", grant_type="client_credentials"
        )
        access_token = token_response["access_token"]
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Tracecat",
        }
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{base_url}/{ALERTS_ENDPOINT}",
                headers=headers,
                params={
                    "limit": limit,
                    "filter": f"last_updated_timestamp:>='{start_time.isoformat()}' last_updated_timestamp:<='{end_time.isoformat()}'",
                },
            )
            response.raise_for_status()
            return response.json()


@registry.register(
    description="Fetch all Crowdstrike detections from Falcon SIEM.",
    namespace="crowdstrike",
)
async def list_crowdstrike_detections(
    base_url: Annotated[
        str, Field(..., description="The base URL for the CrowdStrike API")
    ],
    client_id: Annotated[
        str, Field(..., description="The client ID for CrowdStrike API")
    ],
    client_secret: Annotated[
        str, Field(..., description="The client secret for CrowdStrike API")
    ],
    start_time: Annotated[
        datetime.datetime, Field(..., description="The start time for the detections")
    ],
    end_time: Annotated[
        datetime.datetime, Field(..., description="The end time for the detections")
    ],
    limit: Annotated[
        int,
        Field(default=9999, description="The maximum number of detections to return"),
    ] = 9999,
) -> list[dict[str, Any]]:
    async with AsyncOAuth2Client(
        client_id=client_id, client_secret=client_secret
    ) as client:
        token_response = await client.fetch_token(
            url=f"{base_url}/{TOKEN_ENDPOINT}", grant_type="client_credentials"
        )
        access_token = token_response["access_token"]
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Tracecat",
        }
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{base_url}/{DETECTS_ENDPOINT}",
                headers=headers,
                params={
                    "limit": limit,
                    "filter": f"date_updated:>='{start_time.isoformat()}' date_updated:<='{end_time.isoformat()}'",
                },
            )
            response.raise_for_status()
            return response.json()
