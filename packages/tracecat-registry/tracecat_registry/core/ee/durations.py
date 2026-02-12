"""SDK-only case duration UDFs.

These UDFs are always registered but route to internal endpoints that are
gated by entitlements on the server side. If the entitlement is not enabled,
the server will return 404.
"""

from typing import Annotated

from typing_extensions import Doc

from tracecat_registry import registry, types
from tracecat_registry.context import get_context


@registry.register(
    default_title="Get case metrics",
    display_group="Cases",
    description="Get case metrics as time-series.",
    namespace="core.cases",
    required_entitlements=["case_addons"],
)
async def get_case_metrics(
    case_ids: Annotated[
        list[str],
        Doc("List of case IDs to get case metrics for."),
    ],
) -> list[types.CaseDurationMetric]:
    """Get case metrics as OTEL-aligned time-series for the provided case IDs.

    Returns a list of time-series metrics with the following fields:
    - timestamp: When the duration was measured (ISO 8601)
    - metric_name: "case_duration_seconds"
    - value: Duration in seconds
    - duration_name: Human-readable name (e.g., "Time to Resolve")
    - duration_slug: Slugified name for filtering (e.g., "time_to_resolve")
    - case_priority, case_severity, case_status: Dimensions for groupby
    - case_id, case_short_id: Identifiers for drill-down
    """
    if not case_ids:
        return []

    return await get_context().cases.get_case_metrics(case_ids)
