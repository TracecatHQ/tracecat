from typing import Annotated, Any
from uuid import UUID

from tracecat_registry import registry
from typing_extensions import Doc

from tracecat.cases.durations.service import CaseDurationService
from tracecat.cases.service import CasesService
from tracecat.db.engine import get_async_session_context_manager


@registry.register(
    default_title="List case durations",
    display_group="Cases",
    description="List case durations as flat records for analytics and data visualization.",
    namespace="core.cases",
)
async def list_case_durations(
    case_ids: Annotated[
        list[str],
        Doc("List of case IDs to list durations for."),
    ],
) -> list[dict[str, Any]]:
    """List case durations as flat records for the provided case IDs."""
    if not case_ids:
        return []

    # Validate and convert case IDs
    case_uuids: list[UUID] = []
    for case_id in case_ids:
        try:
            case_uuids.append(UUID(case_id))
        except ValueError as err:
            raise ValueError(f"Invalid case ID format: {case_id}") from err

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

        # Get duration records
        records = await duration_service.list_records(cases)

    return [record.model_dump(mode="json") for record in records]
