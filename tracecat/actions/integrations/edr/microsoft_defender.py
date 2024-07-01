"""Microsoft Defender for Endpoint integration.

Requires secret named `microsoft_defender_endpoint` with the following keys:
- `MICROSOFT_GRAPH_CLIENT_ID`
- `MICROSOFT_GRAPH_CLIENT_SECRET`
- `MICROSOFT_GRAPH_TENANT_ID`
"""

import os
from datetime import datetime
from typing import Annotated

from tracecat.actions.integrations.common import list_microsoft_graph_alerts
from tracecat.registry import Field, RegistrySecret, registry

MICROSOFT_GRAPH_SERVICE_SOURCE = "microsoftDefenderForEndpoint"

microsoft_defender_endpoint_secret = RegistrySecret(
    name="microsoft_defender_endpoint",
    keys=[
        "MICROSOFT_GRAPH_CLIENT_ID",
        "MICROSOFT_GRAPH_CLIENT_SECRET",
        "MICROSOFT_GRAPH_TENANT_ID",
    ],
)
"""Microsoft Defender for Endpoint secret.

- name: `microsoft_defender_endpoint`
- keys:
    - `MICROSOFT_GRAPH_CLIENT_ID`
    - `MICROSOFT_GRAPH_CLIENT_SECRET`
    - `MICROSOFT_GRAPH_TENANT_ID`
"""


@registry.register(
    # Example:
    # namespace="microsoft_defender",
    # default_title="List Microsoft Defender for Endpoints alerts",
    # description="Fetch all Sentinel One alerts.",
    # display_group="Endpoint Detection & Response",
    # namespace="integrations.sentinel_one",
    # secrets=[sentinel_one_secret],
    default_title="List Microsoft Defender for Endpoint alerts",
    description="Fetch all Microsoft Defender for Endpoint alerts and filter by time range.",
    display_group="Microsoft Defender",
    namespace="integrations.microsoft_defender",
    secrets=[microsoft_defender_endpoint_secret],
)
async def list_defender_endpoint_alerts(
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
