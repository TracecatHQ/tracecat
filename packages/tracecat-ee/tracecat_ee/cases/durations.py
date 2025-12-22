from typing import Annotated, cast
from uuid import UUID

from tracecat_registry import config, registry
from tracecat_registry.context import get_context
from typing_extensions import Doc

from tracecat_ee.cases.types import CaseDurationMetric


@registry.register(
    default_title="Get case metrics",
    display_group="Cases",
    description="Get case metrics as time-series.",
    namespace="core.cases",
)
async def get_case_metrics(
    case_ids: Annotated[
        list[str],
        Doc("List of case IDs to get case metrics for."),
    ],
) -> list[CaseDurationMetric]:
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

    if config.flags.registry_client:
        return cast(
            list[CaseDurationMetric],
            await get_context().cases.get_case_metrics(case_ids),
        )

    # Validate and convert case IDs
    case_uuids: list[UUID] = []
    for case_id in case_ids:
        try:
            case_uuids.append(UUID(case_id))
        except ValueError as err:
            raise ValueError(f"Invalid case ID format: {case_id}") from err

    from tracecat.cases.durations.service import CaseDurationService
    from tracecat.cases.service import CasesService
    from tracecat.db.engine import get_async_session_context_manager

    async with get_async_session_context_manager() as session:
        cases_service = CasesService(session)
        duration_service = CaseDurationService(session)

        # Fetch all cases
        cases = []
        for case_uuid in case_uuids:
            case = await cases_service.get_case(case_uuid)
            if case is None:
                raise ValueError(f"Case with ID {case_uuid} not found")
            cases.append(case)

        # Get duration time-series metrics
        metrics = await duration_service.compute_time_series(cases)

    return cast(
        list[CaseDurationMetric],
        [metric.model_dump(mode="json") for metric in metrics],
    )
