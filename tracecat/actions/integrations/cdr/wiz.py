"""Wiz Cloud Detection and Response (CDR) issues integration.

Authentication method: OAuth 2.0

Requires the following parameters:
- Authentication URL (e.g. https://auth.app.wiz.io/oauth/token)
- Client ID
- Client Secret

Supported APIs:

```python
list_alerts = {
    "endpoint": "https://api.<region>.app.wiz.io/graphql",
    "method": "GraphQL",
    "ocsf_schema": "array[detection_finding]",
    "reference": "https://github.com/criblio/collector-templates/blob/main/collectors/rest/wiz/collector-wiz-issues.json"
}
```
"""

from datetime import datetime
from typing import Any

import httpx

from tracecat.actions.io import retry

QUERY_STRING = """
query IssuesTable($filterBy: IssueFilters, $first: Int, $after: String, $orderBy: IssueOrder) {
    issues: issuesV2(filterBy: $filterBy, first: $first, after: $after, orderBy: $orderBy) {
        nodes {
            id
            control {
                id
                name
                description
                resolutionRecommendation
                securitySubCategories {
                    title
                    category {
                        name
                        framework {
                            name
                        }
                    }
                }
            }
            createdAt
            updatedAt
            sourceRule {
                id
                name
            }
            dueAt
            resolvedAt
            statusChangedAt
            project {
                id
                name
                slug
                businessUnit
                riskProfile {
                    businessImpact
                }
            }
            status
            severity
            type
            entitySnapshot {
                id
                type
                nativeType
                name
                status
                cloudPlatform
                cloudProviderURL
                providerId
                region
                resourceGroupExternalId
                subscriptionExternalId
                subscriptionName
                subscriptionTags
                tags
                externalId
            }
            notes {
                createdAt
                updatedAt
                text
                user {
                    name
                    email
                }
                serviceAccount {
                    name
                }
            }
            serviceTickets {
                externalId
                name
                url
            }
        }
        pageInfo {
            hasNextPage
            endCursor
        }
    }
}
"""


async def _get_access_token(client_id: str, client_secret: str, auth_url: str) -> str:
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "audience": "wiz-api",
        "grant_type": "client_credentials",
    }
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        response = await client.post(auth_url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()["access_token"]


@retry()
async def _query_wiz_alerts(
    access_token: str, api_url: str, variables: dict[str, Any]
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            api_url,
            json={"query": QUERY_STRING, "variables": variables},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()["data"]["issues"]


async def list_wiz_alerts(
    client_id: str,
    client_secret: str,
    auth_url: str,
    api_url: str,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    issues = []
    dt_format = "%Y-%m-%dT%H:%M:%SZ"
    variables = {
        "first": 100,
        "filterBy": {
            "status": ["OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED"],
            "severity": ["INFORMATIONAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
            "type": ["TOXIC_COMBINATION", "THREAT_DETECTION", "CLOUD_CONFIGURATION"],
            "createdAt": {
                "after": start_time.strftime(dt_format),
                "before": end_time.strftime(dt_format),
            },
        },
    }

    # Get OAuth 2.0 access token
    access_token = await _get_access_token(
        client_id, client_secret=client_secret, auth_url=auth_url
    )

    # Get all alerts
    while True:
        response = await _query_wiz_alerts(
            access_token, api_url=api_url, variables=variables
        )
        issues.extend(response.get("nodes", []))
        page_info = response.get("pageInfo")
        if not page_info or not page_info.get("hasNextPage"):
            break
        variables["after"] = page_info.get("endCursor")

    return issues
