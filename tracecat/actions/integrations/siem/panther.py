"""Panther integration for Tracecat.

Authentication method: Token-based

Requires: a secret named `panther` with key `PANTHER_API_TOKEN`
"""

import os
from datetime import datetime
from typing import Annotated, Any

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport

from tracecat.registry import Field, registry


def create_panther_client():
    PANTHER_API_URL = os.getenv("PANTHER_API_URL")
    PANTHER_API_KEY = os.getenv("PANTHER_API_KEY")
    transport = AIOHTTPTransport(
        url=PANTHER_API_URL, headers={"X-API-Key": PANTHER_API_KEY}
    )
    client = Client(transport=transport, fetch_schema_from_transport=True)
    return client


@registry.register(
    default_title="List Panther alerts",
    description="Fetch all Panther alerts and filter by time range.",
    display_group="Panther",
    namespace="integrations.panther",
    secrets=["panther"],
)
async def list_panther_alerts(
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
    query = gql(
        """
        query FindAlerts($input: AlertsInput!) {
          alerts(input: $input) {
            edges {
              node {
                id
                title
                severity
                status
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
        """
    )

    all_alerts = []
    has_more = True
    cursor = None

    client = create_panther_client()
    while has_more:
        query_data = await client.execute_async(
            query,
            variable_values={
                "input": {
                    "createdAtAfter": start_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "createdAtBefore": end_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "cursor": cursor,
                    "limit": limit,
                }
            },
        )

        all_alerts.extend([edge["node"] for edge in query_data["alerts"]["edges"]])
        has_more = query_data["alerts"]["pageInfo"]["hasNextPage"]
        cursor = query_data["alerts"]["pageInfo"]["endCursor"]

    return all_alerts
