"""Microsoft Defender for Cloud integration.

Requires secret named `microsoft_defender_cloud` with the following keys:
- `MICROSOFT_GRAPH_CLIENT_ID`
- `MICROSOFT_GRAPH_CLIENT_SECRET`
- `MICROSOFT_GRAPH_TENANT_ID`
"""

import os
from datetime import datetime
from typing import Annotated

from tracecat.actions.integrations.common import list_microsoft_graph_alerts
from tracecat.registry import Field, registry

MICROSOFT_GRAPH_SERVICE_SOURCE = "microsoftDefenderForCloud"


@registry.register(
    default_title="List Microsoft Defender for Cloud alerts",
    description="Fetch Microsoft Defender for Cloud alerts and filter by time range.",
    display_group="Microsoft Defender",
    namespace="integrations.microsoft_defender",
    secrets=["microsoft_defender_cloud"],
)
async def list_defender_cloud_alerts(
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
):
    client_id = os.getenv("MICROSOFT_GRAPH_CLIENT_ID")
    client_secret = os.getenv("MICROSOFT_GRAPH_CLIENT_SECRET")
    tenant_id = os.getenv("MICROSOFT_GRAPH_TENANT_ID")
    return await list_microsoft_graph_alerts(
        client_id=client_id,
        client_secret=client_secret,
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        service_source=MICROSOFT_GRAPH_SERVICE_SOURCE,
    )
