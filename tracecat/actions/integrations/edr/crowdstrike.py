"""Crowdstrike integration.

Authentication method: Direct Authentication (`client_id` and `client_secret`)

References:

- https://falconpy.io/Service-Collections
- https://www.falconpy.io/Usage/Authenticating-to-the-API.html

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

from falconpy import Alerts, Detects

from tracecat.registry import Field, registry

TOKEN_ENDPOINT = "/oauth2/token"
ALERTS_ENDPOINT = "/alerts/queries/alerts/v2"
DETECTS_ENDPOINT = "/detects/queries/detects/v1"


@registry.register(
    default_title="List Crowdstrike alerts",
    description="Fetch all Crowdstrike alerts from Falcon SIEM.",
    display_group="EDR",
    namespace="integrations.crowdstrike.alerts",
    secrets=["crowdstrike"],
)
async def list_crowdstrike_alerts(
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
    falcon = Alerts(client_id=client_id, client_secret=client_secret)
    response = falcon.query_alerts_v2(
        limit=limit,
        filter=f"last_updated_timestamp:>='{start_time.isoformat()}' last_updated_timestamp:<='{end_time.isoformat()}'",
    )
    return response


@registry.register(
    default_title="List Crowdstrike detections",
    description="Fetch all Crowdstrike detections from Falcon SIEM.",
    display_group="EDR",
    namespace="integrations.crowdstrike.detections",
    secrets=["crowdstrike"],
)
async def list_crowdstrike_detections(
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
    falcon = Detects(client_id=client_id, client_secret=client_secret)
    response = falcon.query_detects(
        limit=limit,
        filter=f"date_updated:>='{start_time.isoformat()}' date_updated:<='{end_time.isoformat()}'",
    )
    return response
