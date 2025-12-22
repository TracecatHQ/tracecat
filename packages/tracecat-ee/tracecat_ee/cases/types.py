"""TypedDict definitions for case-related EE UDF return types."""

from datetime import datetime
from typing import TypedDict


class CaseDurationMetric(TypedDict):
    """Case duration metric data point."""

    timestamp: datetime
    metric_name: str
    value: float
    duration_name: str
    duration_slug: str
    case_priority: str
    case_severity: str
    case_status: str
    case_id: str
    case_short_id: str
