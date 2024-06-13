from datetime import datetime
from typing import Annotated

from tracecat.actions.integrations.common import list_microsoft_graph_alerts
from tracecat.registry import Field, registry

MICROSOFT_GRAPH_SERVICE_SOURCE = "microsoftDefenderForCloud"


@registry.register(
    description="Fetch Microsoft Defender for Cloud alerts.",
    namespace="integrations.cdr.microsoft.defender",
    default_title="List Microsoft Defender Cloud Alerts",
    display_group="Cloud D&R",
)
async def list_defender_cloud_alerts(
    client_id: Annotated[
        str, Field(..., description="The client ID for Microsoft Graph API")
    ],
    client_secret: Annotated[
        str, Field(..., description="The client secret for Microsoft Graph API")
    ],
    tenant_id: Annotated[
        str, Field(..., description="The tenant ID for Microsoft Graph API")
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
):
    return await list_microsoft_graph_alerts(
        client_id=client_id,
        client_secret=client_secret,
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        service_source=MICROSOFT_GRAPH_SERVICE_SOURCE,
    )
