"""Crowdstrike integration.

Authentication method: Direct Authentication

Requires a `crowdstrike` secret with:
- `CROWDSTRIKE_CLIENT_ID`
- `CROWDSTRIKE_CLIENT_SECRET`

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
import os
from typing import Annotated, Any, Literal

from falconpy import Alerts, Detects

from tracecat.registry import Field, registry

TOKEN_ENDPOINT = "/oauth2/token"
ALERTS_ENDPOINT = "/alerts/queries/alerts/v2"
DETECTS_ENDPOINT = "/detects/queries/detects/v1"


AlertStatus = Literal[
    "ignored", "new", "in_progress", "true_positive", "false_positive"
]
DetectStatus = Literal["ignored", "new", "in_progress", "resolved", "false_positive"]


def get_crowdstrike_credentials():
    client_id = os.getenv("CROWDSTRIKE_CLIENT_ID")
    client_secret = os.getenv("CROWDSTRIKE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("Missing CROWDSTRIKE_CLIENT_ID or CROWDSTRIKE_CLIENT")
    return {"client_id": client_id, "client_secret": client_secret}


@registry.register(
    default_title="List Crowdstrike alerts",
    description="Fetch all Crowdstrike alerts from Falcon SIEM.",
    display_group="EDR",
    namespace="integrations.crowdstrike.alerts",
    secrets=["crowdstrike"],
)
async def list_crowdstrike_alerts(
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
    falcon = Alerts(**get_crowdstrike_credentials())
    response = falcon.query_alerts_v2(
        limit=limit,
        filter=f"last_updated_timestamp:>='{start_time.isoformat()}' last_updated_timestamp:<='{end_time.isoformat()}'",
    )
    return response


@registry.register(
    default_title="List Crowdstrike detects",
    description="Fetch all Crowdstrike detections from Falcon SIEM.",
    display_group="EDR",
    namespace="integrations.crowdstrike.detections",
    secrets=["crowdstrike"],
)
async def list_crowdstrike_detects(
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
    falcon = Detects(**get_crowdstrike_credentials())
    response = falcon.query_detects(
        limit=limit,
        filter=f"date_updated:>='{start_time.isoformat()}' date_updated:<='{end_time.isoformat()}'",
    )
    return response


@registry.register(
    default_title="Update Crowdstrike alert status",
    description="Update the status of Crowdstrike alerts.",
    display_group="EDR",
    namespace="integrations.crowdstrike.alerts",
    secrets=["crowdstrike"],
)
async def update_crowdstrike_alert_status(
    alert_ids: Annotated[
        list[str], Field(..., description="List of alert IDs to update")
    ],
    status: Annotated[
        AlertStatus, Field(..., description="The new status for the alerts")
    ],
) -> dict[str, Any]:
    falcon = Alerts(**get_crowdstrike_credentials())

    # Perform the action to update the alert status
    response = falcon.update_alerts_v3(
        composite_ids=alert_ids,
        update_status=status,
    )

    return response


@registry.register(
    default_title="Update Crowdstrike detect status",
    description="Update the status of Crowdstrike detects.",
    display_group="EDR",
    namespace="integrations.crowdstrike.detections",
    secrets=["crowdstrike"],
)
async def update_crowdstrike_detect_status(
    detection_ids: Annotated[
        list[str], Field(..., description="List of detect IDs to update")
    ],
    status: Annotated[
        DetectStatus, Field(..., description="The new status for the detects")
    ],
) -> dict[str, Any]:
    falcon = Detects(**get_crowdstrike_credentials())

    # Perform the action to update the detection status
    response = falcon.update_detects_by_ids(
        ids=detection_ids,
        status=status,
    )

    return response
