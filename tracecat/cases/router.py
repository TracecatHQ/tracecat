import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import DBAPIError

from tracecat.auth.credentials import RoleACL
from tracecat.auth.models import UserRead
from tracecat.auth.users import search_users
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import (
    AssigneeChangedEventRead,
    CaseCommentCreate,
    CaseCommentRead,
    CaseCommentUpdate,
    CaseCreate,
    CaseCustomFieldRead,
    CaseEventRead,
    CaseEventsWithUsers,
    CaseFieldCreate,
    CaseFieldRead,
    CaseFieldUpdate,
    CaseRead,
    CaseReadMinimal,
    CaseUpdate,
)
from tracecat.cases.service import (
    CaseCommentsService,
    CaseFieldsService,
    CasesService,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.pagination import (
    CursorPaginatedResponse,
    CursorPaginationParams,
)

cases_router = APIRouter(prefix="/cases", tags=["cases"])
case_fields_router = APIRouter(prefix="/case-fields", tags=["cases"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]
WorkspaceAdminUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=AccessLevel.ADMIN,
    ),
]


# Case Management


@cases_router.get("")
async def list_cases(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    limit: int = Query(20, ge=1, le=100, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
) -> CursorPaginatedResponse[CaseReadMinimal]:
    """List cases with cursor-based pagination."""
    service = CasesService(session, role)
    pagination_params = CursorPaginationParams(
        limit=limit,
        cursor=cursor,
        reverse=reverse,
    )
    try:
        cases = await service.list_cases_paginated(pagination_params)
    except Exception as e:
        logger.error(f"Failed to list cases: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve cases") from e
    return cases


@cases_router.get("/search")
async def search_cases(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    search_term: str | None = Query(
        None, description="Text to search for in case summary and description"
    ),
    status: CaseStatus | None = Query(None, description="Filter by case status"),
    priority: CasePriority | None = Query(None, description="Filter by case priority"),
    severity: CaseSeverity | None = Query(None, description="Filter by case severity"),
    limit: int | None = Query(None, description="Maximum number of cases to return"),
    order_by: Literal["created_at", "updated_at", "priority", "severity", "status"]
    | None = Query(None, description="Field to order the cases by"),
    sort: Literal["asc", "desc"] | None = Query(
        None, description="Direction to sort (asc or desc)"
    ),
) -> list[CaseReadMinimal]:
    """Search cases based on various criteria."""
    service = CasesService(session, role)
    cases = await service.search_cases(
        search_term=search_term,
        status=status,
        priority=priority,
        severity=severity,
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
            assignee=UserRead.model_validate(case.assignee, from_attributes=True)
            if case.assignee
            else None,
        )
        for case in cases
    ]


@cases_router.get("/{case_id}")
async def get_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> CaseRead:
    """Get a specific case."""
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
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

    # Match up the fields with the case field definitions
    return CaseRead(
        id=case.id,
        short_id=f"CASE-{case.case_number:04d}",
        created_at=case.created_at,
        updated_at=case.updated_at,
        summary=case.summary,
        status=case.status,
        priority=case.priority,
        severity=case.severity,
        description=case.description,
        assignee=UserRead.model_validate(case.assignee, from_attributes=True)
        if case.assignee
        else None,
        fields=final_fields,
    )


@cases_router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: CaseCreate,
) -> None:
    """Create a new case."""
    service = CasesService(session, role)
    await service.create_case(params)


@cases_router.patch("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: CaseUpdate,
    case_id: uuid.UUID,
) -> None:
    """Update a case."""
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    try:
        await service.update_case(case, params)
    except DBAPIError as e:
        while (cause := e.__cause__) is not None:
            e = cause
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@cases_router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> None:
    """Delete a case."""
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    await service.delete_case(case)


# Case Comments
# Support comments as a first class activity type.
# We anticipate having other complex comment functionality in the future.
@cases_router.get("/{case_id}/comments", status_code=status.HTTP_200_OK)
async def list_comments(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[CaseCommentRead]:
    """List all comments for a case."""
    # Get the case first
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    # Execute join query directly in the endpoint
    comments_svc = CaseCommentsService(session, role)
    res = []
    for comment, user in await comments_svc.list_comments(case):
        comment_data = CaseCommentRead.model_validate(comment, from_attributes=True)
        if user:
            comment_data.user = UserRead.model_validate(user, from_attributes=True)
        res.append(comment_data)
    return res


@cases_router.post("/{case_id}/comments", status_code=status.HTTP_201_CREATED)
async def create_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseCommentCreate,
) -> None:
    """Create a new comment on a case."""
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    await comments_svc.create_comment(case, params)


@cases_router.patch(
    "/{case_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
    params: CaseCommentUpdate,
) -> None:
    """Update an existing comment."""
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.get_comment(comment_id)
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    await comments_svc.update_comment(comment, params)


@cases_router.delete(
    "/{case_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
) -> None:
    """Delete a comment."""
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.get_comment(comment_id)
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    await comments_svc.delete_comment(comment)


# Case Fields


@case_fields_router.get("")
async def list_fields(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> list[CaseFieldRead]:
    """List all case fields."""
    service = CaseFieldsService(session, role)
    columns = await service.list_fields()
    return [CaseFieldRead.from_sa(column) for column in columns]


@case_fields_router.post("", status_code=status.HTTP_201_CREATED)
async def create_field(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    params: CaseFieldCreate,
) -> None:
    """Create a new case field."""
    service = CaseFieldsService(session, role)
    await service.create_field(params)


@case_fields_router.patch("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_field(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    field_id: str,
    params: CaseFieldUpdate,
) -> None:
    """Update a case field."""
    service = CaseFieldsService(session, role)
    await service.update_field(field_id, params)


@case_fields_router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    field_id: str,
) -> None:
    """Delete a case field."""
    service = CaseFieldsService(session, role)
    await service.delete_field(field_id)


# Case Events


@cases_router.get(
    "/{case_id}/events",
    status_code=status.HTTP_200_OK,
    response_model_exclude_none=True,
)
async def list_events_with_users(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> CaseEventsWithUsers:
    """List all users for a case."""
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    db_events = await service.events.list_events(case)
    # Get user ids
    user_ids: set[uuid.UUID] = set()
    events: list[CaseEventRead] = []

    for db_evt in db_events:
        evt = CaseEventRead.model_validate(
            {
                "type": db_evt.type,
                "user_id": db_evt.user_id,
                "created_at": db_evt.created_at,
                **db_evt.data,
            }
        )
        root_evt = evt.root
        if isinstance(root_evt, AssigneeChangedEventRead):
            if root_evt.old is not None:
                user_ids.add(root_evt.old)
            if root_evt.new is not None:
                user_ids.add(root_evt.new)
        if root_evt.user_id is not None:
            user_ids.add(root_evt.user_id)
        events.append(evt)

    # Get users
    users = (
        [
            UserRead.model_validate(u, from_attributes=True)
            for u in await search_users(session=session, user_ids=user_ids)
        ]
        if user_ids
        else []
    )

    return CaseEventsWithUsers(events=events, users=users)
