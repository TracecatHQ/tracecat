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

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

TOKEN_ENDPOINT = "/oauth2/token"
ALERTS_ENDPOINT = "/alerts/queries/alerts/v2"
DETECTS_ENDPOINT = "/detects/queries/detects/v1"


async def list_crowdstrike_alerts(
    base_url: str,
    client_id: str,
    client_secret: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 9999,
):
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


async def list_crowdstrike_detections(
    base_url: str,
    client_id: str,
    client_secret: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 9999,
):
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
