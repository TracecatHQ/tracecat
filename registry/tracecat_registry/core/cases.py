from typing import Annotated, Any

from typing_extensions import Doc

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseCreate
from tracecat.cases.service import CasesService
from tracecat_registry import registry


@registry.register(
    default_title="Create Case",
    display_group="Cases",
    description="Create a new case.",
    namespace="core.cases",
)
async def create(
    summary: Annotated[
        str,
        Doc("The summary of the case."),
    ],
    description: Annotated[
        str,
        Doc("The description of the case."),
    ],
    priority: Annotated[
        CasePriority,
        Doc("The priority of the case."),
    ],
    severity: Annotated[
        CaseSeverity,
        Doc("The severity of the case."),
    ],
    status: Annotated[
        CaseStatus,
        Doc("The status of the case."),
    ],
    fields: Annotated[
        dict[str, Any] | None,
        Doc("Custom fields for the case."),
    ] = None,
) -> dict[str, Any]:
    async with CasesService.with_session() as service:
        case = await service.create_case(
            CaseCreate(
                summary=summary,
                description=description,
                priority=priority,
                severity=severity,
                status=status,
                fields=fields,
            )
        )
    return case.model_dump()
