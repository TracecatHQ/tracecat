"""Microsoft Graph alerts integration.

Microsoft Graph is a unified API endpoint for accessing data across multiple Microsoft services.

The alerts endpoint returns security alerts across multiple `serviceSource`:

- `microsoftDefenderForEndpoint`: Microsoft Defender for Endpoint.
- `microsoftDefenderForIdentity`: Microsoft Defender for Identity.
- `microsoftDefenderForCloudApps`: Microsoft Defender for Cloud Apps.
- `microsoftDefenderForOffice365`: Microsoft Defender For Office365.
- `microsoft365Defender`: Microsoft 365 Defender.
- `azureAdIdentityProtection`: Microsoft Entra ID Protection.
- `microsoftAppGovernance`: Microsoft app governance.
- `dataLossPrevention`: Microsoft Purview Data Loss Prevention.
- `microsoftDefenderForCloud`: Microsoft Defender for Cloud.
- `microsoftSentinel`: Microsoft Sentinel.

Authentication method: OAuth 2.0 (app-only access)

References:

- https://learn.microsoft.com/en-us/graph/auth/auth-concepts
- https://learn.microsoft.com/en-us/graph/api/security-list-alerts_v2
- https://learn.microsoft.com/en-us/graph/filter-query-parameter
- https://learn.microsoft.com/en-us/graph/api/resources/security-api-overview#alerts-and-incidents
- https://learn.microsoft.com/en-us/graph/api/resources/security-alert#servicesource-values

Supported APIs:

```python
list_alerts = {
    "endpoint": "/security/alerts_v2",
    "method": "GET",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://learn.microsoft.com/en-us/graph/api/security-list-alerts_v2"
}
```
"""

from datetime import datetime
from typing import Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

TOKEN_ENDPOINT = "/oauth2/v2.0/token"
ALERTS_ENDPOINT = "/security/alerts_v2"


async def list_microsoft_graph_alerts(
    client_id: str,
    client_secret: str,
    tenant_id: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 1000,
    service_source: str | None = None,
) -> dict[str, Any]:
    token_url = f"https://login.microsoftonline.com/{tenant_id}/{TOKEN_ENDPOINT}"
    scope = "https://graph.microsoft.com/.default"

    # We use the app-only access flow to authenticate with Microsoft Graph
    # https://learn.microsoft.com/en-us/graph/auth-v2-service
    async with AsyncOAuth2Client(client_id, client_secret, scope=scope) as client:
        token = await client.fetch_token(
            token_url=token_url,
            grant_type="client_credentials",
        )
        access_token = token["access_token"]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    params = {
        "$top": limit,
        "$filter": f"createdDateTime ge {f'{start_time.isoformat()}Z'} and createdDateTime le {f'{end_time.isoformat()}Z'}",
    }
    if service_source:
        params["serviceSource"] = service_source

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://graph.microsoft.com/v1.0/security/alerts_v2",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()
