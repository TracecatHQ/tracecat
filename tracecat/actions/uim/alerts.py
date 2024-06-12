"""Unified API for alerts.

Supported Capabilities:
- `list_alerts`: `start_time`, `end_time`, and `vendor` required.

Expected OCSF schemas:
- Detection Finding (2004)
- Vulnerability Finding (2002)
"""

import os
from datetime import datetime
from typing import Literal

from tracecat.actions.integrations import get_capability


async def list_alerts(
    category: Literal["cdr", "cspm", "edr", "siem"],
    start_time: datetime,
    end_time: datetime,
    vendor: str,
) -> list[dict]:
    secret = os.getenv(vendor)
    list_vendor_alerts = get_capability(
        category=category, capability="list_alerts", vendor=vendor
    )
    alerts = await list_vendor_alerts(
        start_time=start_time, end_time=end_time, **secret
    )
    return alerts
