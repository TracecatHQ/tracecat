from typing import Annotated, Any, Literal
from uuid import UUID

from typing_extensions import Doc

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import (
    CaseCreate,
    CaseUpdate,
    CaseCommentCreate,
    CaseCommentUpdate,
)
from tracecat.cases.service import CasesService, CaseCommentsService
from tracecat_registry import registry

PriorityType = Literal[
    "unknown",
    "low",
    "medium",
    "high",
    "critical",
    "other",
]

SeverityType = Literal[
    "unknown",
    "informational",
    "low",
    "medium",
    "high",
    "critical",
    "fatal",
    "other",
]

StatusType = Literal[
    "unknown",
    "new",
    "in_progress",
    "on_hold",
    "resolved",
    "closed",
    "other",
]


@registry.register(
    default_title="Create Case",
    display_group="Cases",
    description="Create a new case.",
    namespace="core.cases",
)
async def create_case(
    summary: Annotated[
        str,
        Doc("The summary of the case."),
    ],
    description: Annotated[
        str,
        Doc("The description of the case."),
    ],
    priority: Annotated[
        PriorityType,
        Doc("The priority of the case."),
    ] = "unknown",
    severity: Annotated[
        SeverityType,
        Doc("The severity of the case."),
    ] = "unknown",
    status: Annotated[
        StatusType,
        Doc("The status of the case."),
    ] = "unknown",
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
                priority=CasePriority(priority),
                severity=CaseSeverity(severity),
                status=CaseStatus(status),
                fields=fields,
            )
        )
    return case.model_dump()


@registry.register(
    default_title="Update Case",
    display_group="Cases",
    description="Update an existing case.",
    namespace="core.cases",
)
async def update_case(
    case_id: Annotated[
        str,
        Doc("The ID of the case to update."),
    ],
    summary: Annotated[
        str | None,
        Doc("The updated summary of the case."),
    ] = None,
    description: Annotated[
        str | None,
        Doc("The updated description of the case."),
    ] = None,
    priority: Annotated[
        PriorityType | None,
        Doc("The updated priority of the case."),
    ] = None,
    severity: Annotated[
        SeverityType | None,
        Doc("The updated severity of the case."),
    ] = None,
    status: Annotated[
        StatusType | None,
        Doc("The updated status of the case."),
    ] = None,
    fields: Annotated[
        dict[str, Any] | None,
        Doc("Updated custom fields for the case."),
    ] = None,
) -> dict[str, Any]:
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        params = {}
        if summary is not None:
            params["summary"] = summary
        if description is not None:
            params["description"] = description
        if priority is not None:
            params["priority"] = CasePriority(priority)
        if severity is not None:
            params["severity"] = CaseSeverity(severity)
        if status is not None:
            params["status"] = CaseStatus(status)
        if fields is not None:
            # Empty dict or None means fields are not updated
            # You must explicitly set fields to None to remove their values
            # If we don't pass fields, the service will not try to update the fields
            params["fields"] = fields
        updated_case = await service.update_case(case, CaseUpdate(**params))
    return updated_case.model_dump()


@registry.register(
    default_title="Create Case Comment",
    display_group="Cases",
    description="Add a comment to an existing case.",
    namespace="core.cases",
)
async def create_comment(
    case_id: Annotated[
        str,
        Doc("The ID of the case to comment on."),
    ],
    content: Annotated[
        str,
        Doc("The comment content."),
    ],
    parent_id: Annotated[
        str | None,
        Doc("The ID of the parent comment if this is a reply."),
    ] = None,
) -> dict[str, Any]:
    async with CasesService.with_session() as case_service:
        case = await case_service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        async with CaseCommentsService.with_session() as comment_service:
            comment = await comment_service.create_comment(
                case,
                CaseCommentCreate(
                    content=content,
                    parent_id=UUID(parent_id) if parent_id else None,
                ),
            )
    return comment.model_dump()


@registry.register(
    default_title="Update Case Comment",
    display_group="Cases",
    description="Update an existing case comment.",
    namespace="core.cases",
)
async def update_comment(
    comment_id: Annotated[
        str,
        Doc("The ID of the comment to update."),
    ],
    content: Annotated[
        str | None,
        Doc("The updated comment content."),
    ] = None,
    parent_id: Annotated[
        str | None,
        Doc("The updated parent comment ID."),
    ] = None,
) -> dict[str, Any]:
    async with CaseCommentsService.with_session() as service:
        comment = await service.get_comment(UUID(comment_id))
        if not comment:
            raise ValueError(f"Comment with ID {comment_id} not found")

        params = {}
        if content is not None:
            params["content"] = content
        if parent_id is not None:
            params["parent_id"] = UUID(parent_id)
        updated_comment = await service.update_comment(
            comment, CaseCommentUpdate(**params)
        )
    return updated_comment.model_dump()
