from typing import Annotated, Any, Literal
from uuid import UUID

from typing_extensions import Doc

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import (
    CaseCreate,
    CaseCustomFieldRead,
    CaseFieldRead,
    CaseRead,
    CaseReadMinimal,
    CaseUpdate,
    CaseCommentCreate,
    CaseCommentUpdate,
)
from tracecat.cases.service import CasesService, CaseCommentsService
from tracecat.db.engine import get_async_session_context_manager
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
    default_title="Create case",
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
    default_title="Update case",
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
    default_title="Create case comment",
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
    default_title="Update case comment",
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


@registry.register(
    default_title="Get case",
    display_group="Cases",
    description="Get details of a specific case by ID.",
    namespace="core.cases",
)
async def get_case(
    case_id: Annotated[
        str,
        Doc("The ID of the case to retrieve."),
    ],
) -> dict[str, Any]:
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        fields = await service.fields.get_fields(case) or {}
        field_definitions = await service.fields.list_fields()

    final_fields = []
    for defn in field_definitions:
        f = CaseFieldRead.from_sa(defn)
        final_fields.append(
            CaseCustomFieldRead(
                id=f.id,
                type=f.type,
                description=f.description,
                nullable=f.nullable,
                default=f.default,
                reserved=f.reserved,
                value=fields.get(f.id),
            )
        )

    # Convert any UUID to string before serializing
    case_read = CaseRead(
        id=case.id,  # Use UUID directly
        short_id=f"CASE-{case.case_number:04d}",
        created_at=case.created_at,
        updated_at=case.updated_at,
        summary=case.summary,
        status=case.status,
        priority=case.priority,
        severity=case.severity,
        description=case.description,
        fields=final_fields,
    )

    # Use model_dump(mode="json") to ensure UUIDs are converted to strings
    return case_read.model_dump(mode="json")


@registry.register(
    default_title="List cases",
    display_group="Cases",
    description="List all cases.",
    namespace="core.cases",
)
async def list_cases(
    limit: Annotated[
        int | None,
        Doc("Maximum number of cases to return."),
    ] = None,
    order_by: Annotated[
        Literal["created_at", "updated_at", "priority", "severity", "status"] | None,
        Doc("The field to order the cases by."),
    ] = None,
    sort: Annotated[
        Literal["asc", "desc"] | None,
        Doc("The direction to order the cases by."),
    ] = None,
) -> list[dict[str, Any]]:
    async with CasesService.with_session() as service:
        cases = await service.list_cases(limit=limit, order_by=order_by, sort=sort)
    return [
        CaseReadMinimal(
            id=case.id,
            created_at=case.created_at,
            updated_at=case.updated_at,
            short_id=f"CASE-{case.case_number:04d}",
            summary=case.summary,
            status=case.status,
            priority=case.priority,
            severity=case.severity,
        ).model_dump(mode="json")
        for case in cases
    ]


@registry.register(
    default_title="Search cases",
    display_group="Cases",
    description="Search cases based on various criteria.",
    namespace="core.cases",
)
async def search_cases(
    search_term: Annotated[
        str | None,
        Doc("Text to search for in case summary and description."),
    ] = None,
    status: Annotated[
        StatusType | None,
        Doc("Filter by case status."),
    ] = None,
    priority: Annotated[
        PriorityType | None,
        Doc("Filter by case priority."),
    ] = None,
    severity: Annotated[
        SeverityType | None,
        Doc("Filter by case severity."),
    ] = None,
    limit: Annotated[
        int | None,
        Doc("Maximum number of cases to return."),
    ] = None,
    order_by: Annotated[
        Literal["created_at", "updated_at", "priority", "severity", "status"] | None,
        Doc("The field to order the cases by."),
    ] = None,
    sort: Annotated[
        Literal["asc", "desc"] | None,
        Doc("The direction to order the cases by."),
    ] = None,
) -> list[dict[str, Any]]:
    async with CasesService.with_session() as service:
        cases = await service.search_cases(
            search_term=search_term,
            status=CaseStatus(status) if status else None,
            priority=CasePriority(priority) if priority else None,
            severity=CaseSeverity(severity) if severity else None,
            limit=limit,
            order_by=order_by,
            sort=sort,
        )
    return [
        CaseReadMinimal(
            id=case.id,
            created_at=case.created_at,
            updated_at=case.updated_at,
            short_id=f"CASE-{case.case_number:04d}",
            summary=case.summary,
            status=case.status,
            priority=case.priority,
            severity=case.severity,
        ).model_dump(mode="json")
        for case in cases
    ]


@registry.register(
    default_title="List case comments",
    display_group="Cases",
    description="List all comments for a case.",
    namespace="core.cases",
)
async def list_comments(
    case_id: Annotated[
        str,
        Doc("The ID of the case to get comments for."),
    ],
) -> list[dict[str, Any]]:
    async with get_async_session_context_manager() as session:
        case_service = CasesService(session)
        case = await case_service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        comments_service = CaseCommentsService(session)
        comment_user_pairs = await comments_service.list_comments(case)

    result = []
    for comment, user in comment_user_pairs:
        comment_data = comment.model_dump()
        comment_data["user"] = user.model_dump() if user else None
        result.append(comment_data)

    return result
