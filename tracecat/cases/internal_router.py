from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import DBAPIError, NoResultFound, ProgrammingError
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.schemas import UserRead
from tracecat.auth.users import search_users
from tracecat.cases.durations.schemas import CaseDurationMetric
from tracecat.cases.durations.service import CaseDurationService
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import (
    AssigneeChangedEventRead,
    CaseCommentCreate,
    CaseCommentRead,
    CaseCommentUpdate,
    CaseCreate,
    CaseEventRead,
    CaseEventsWithUsers,
    CaseFieldRead,
    CaseFieldReadMinimal,
    CaseRead,
    CaseReadMinimal,
    CaseTaskCreate,
    CaseTaskRead,
    CaseTaskUpdate,
    CaseUpdate,
    InternalCaseCommentData,
    InternalCaseData,
    TaskAssigneeChangedEventRead,
)
from tracecat.cases.service import (
    CaseCommentsService,
    CasesService,
    CaseTasksService,
)
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.cases.tags.service import CaseTagsService
from tracecat.core.schemas import Schema
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.feature_flags import is_feature_enabled
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(
    prefix="/internal/cases", tags=["internal-cases"], include_in_schema=False
)

# Sub-routers for feature-gated routes (conditionally included at end of file)
task_router = APIRouter()
duration_router = APIRouter()


@router.get("")
async def list_cases(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    limit: int = Query(20, ge=1, le=100, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
    search_term: str | None = Query(
        None,
        description="Text to search for in case summary, description, or short ID",
    ),
    status: list[CaseStatus] | None = Query(None, description="Filter by case status"),
    priority: list[CasePriority] | None = Query(
        None, description="Filter by case priority"
    ),
    severity: list[CaseSeverity] | None = Query(
        None, description="Filter by case severity"
    ),
    assignee_id: list[str] | None = Query(
        None, description="Filter by assignee ID or 'unassigned'"
    ),
    tags: list[str] | None = Query(
        None, description="Filter by tag IDs or slugs (AND logic)"
    ),
    order_by: Literal[
        "created_at", "updated_at", "priority", "severity", "status", "tasks"
    ]
    | None = Query(
        None,
        description="Column name to order by (e.g. created_at, updated_at, priority, severity, status, tasks). Default: created_at",
    ),
    sort: Literal["asc", "desc"] | None = Query(
        None, description="Direction to sort (asc or desc)"
    ),
) -> CursorPaginatedResponse[CaseReadMinimal]:
    service = CasesService(session, role)

    tag_ids: list[uuid.UUID] = []
    if tags:
        tags_service = CaseTagsService(session, role)
        for tag_identifier in tags:
            try:
                tag = await tags_service.get_tag_by_ref_or_id(tag_identifier)
                tag_ids.append(tag.id)
            except NoResultFound:
                continue

    pagination_params = CursorPaginationParams(
        limit=limit,
        cursor=cursor,
        reverse=reverse,
    )

    parsed_assignee_ids: list[uuid.UUID] = []
    include_unassigned = False
    if assignee_id:
        for identifier in assignee_id:
            if identifier == "unassigned":
                include_unassigned = True
                continue
            try:
                parsed_assignee_ids.append(uuid.UUID(identifier))
            except ValueError as e:
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail=f"Invalid assignee_id: {identifier}",
                ) from e

    try:
        cases = await service.list_cases_paginated(
            pagination_params,
            search_term=search_term,
            status=status,
            priority=priority,
            severity=severity,
            assignee_ids=parsed_assignee_ids or None,
            include_unassigned=include_unassigned,
            tag_ids=tag_ids if tag_ids else None,
            order_by=order_by,
            sort=sort,
        )
    except ValueError as e:
        logger.warning(f"Invalid request for list cases: {e}")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Invalid request for list cases",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list cases: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve cases",
        ) from e
    return cases


@router.get("/search")
async def search_cases(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    search_term: str | None = Query(
        None,
        description="Text to search for in case summary, description, or short ID",
    ),
    status: list[CaseStatus] | None = Query(None, description="Filter by case status"),
    priority: list[CasePriority] | None = Query(
        None, description="Filter by case priority"
    ),
    severity: list[CaseSeverity] | None = Query(
        None, description="Filter by case severity"
    ),
    tags: list[str] | None = Query(
        None, description="Filter by tag IDs or slugs (AND logic)"
    ),
    limit: int | None = Query(None, description="Maximum number of cases to return"),
    order_by: Literal["created_at", "updated_at", "priority", "severity", "status"]
    | None = Query(
        None,
        description="Column name to order by (e.g. created_at, updated_at, priority, severity, status). Default: created_at",
    ),
    sort: Literal["asc", "desc"] | None = Query(
        None, description="Direction to sort (asc or desc)"
    ),
    start_time: datetime | None = Query(
        None, description="Return cases created at or after this timestamp"
    ),
    end_time: datetime | None = Query(
        None, description="Return cases created at or before this timestamp"
    ),
    updated_after: datetime | None = Query(
        None, description="Return cases updated at or after this timestamp"
    ),
    updated_before: datetime | None = Query(
        None, description="Return cases updated at or before this timestamp"
    ),
) -> list[CaseReadMinimal]:
    service = CasesService(session, role)

    tag_ids: list[uuid.UUID] = []
    if tags:
        tags_service = CaseTagsService(session, role)
        for tag_identifier in tags:
            try:
                tag = await tags_service.get_tag_by_ref_or_id(tag_identifier)
                tag_ids.append(tag.id)
            except NoResultFound:
                continue

    try:
        cases = await service.search_cases(
            search_term=search_term,
            status=status,
            priority=priority,
            severity=severity,
            tag_ids=tag_ids,
            limit=limit,
            order_by=order_by,
            sort=sort,
            start_time=start_time,
            end_time=end_time,
            updated_after=updated_after,
            updated_before=updated_before,
        )
    except ProgrammingError as exc:
        logger.exception(
            "Failed to search cases due to invalid filter parameters", exc_info=exc
        )
        await session.rollback()
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Invalid filter parameters supplied for case search",
        ) from exc

    task_counts = await service.get_task_counts([case.id for case in cases])

    case_responses: list[CaseReadMinimal] = []
    for case in cases:
        tag_reads = [
            CaseTagRead.model_validate(tag, from_attributes=True) for tag in case.tags
        ]

        case_responses.append(
            CaseReadMinimal(
                id=case.id,
                created_at=case.created_at,
                updated_at=case.updated_at,
                short_id=case.short_id,
                summary=case.summary,
                status=case.status,
                priority=case.priority,
                severity=case.severity,
                assignee=UserRead.model_validate(case.assignee, from_attributes=True)
                if case.assignee
                else None,
                tags=tag_reads,
                num_tasks_completed=task_counts[case.id]["completed"],
                num_tasks_total=task_counts[case.id]["total"],
            )
        )

    return case_responses


@router.get("/{case_id}")
async def get_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> CaseRead:
    service = CasesService(session, role)
    case = await service.get_case(case_id, track_view=True)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    fields = await service.fields.get_fields(case) or {}
    field_definitions = await service.fields.list_fields()
    field_schema = await service.fields.get_field_schema()
    final_fields: list[CaseFieldRead] = []
    for defn in field_definitions:
        f = CaseFieldReadMinimal.from_sa(defn, field_schema=field_schema)
        final_fields.append(
            CaseFieldRead(
                id=f.id,
                type=f.type,
                description=f.description,
                nullable=f.nullable,
                default=f.default,
                reserved=f.reserved,
                options=f.options,
                value=fields.get(f.id),
            )
        )

    tag_reads = [
        CaseTagRead.model_validate(tag, from_attributes=True) for tag in case.tags
    ]

    return CaseRead(
        id=case.id,
        short_id=case.short_id,
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
        payload=case.payload,
        tags=tag_reads,
    )


@router.post("", status_code=HTTP_201_CREATED)
async def create_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseCreate,
) -> CaseRead:
    service = CasesService(session, role)
    try:
        case = await service.create_case(params)
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    fields = await service.fields.get_fields(case) or {}
    field_definitions = await service.fields.list_fields()
    field_schema = await service.fields.get_field_schema()
    final_fields: list[CaseFieldRead] = []
    for defn in field_definitions:
        f = CaseFieldReadMinimal.from_sa(defn, field_schema=field_schema)
        final_fields.append(
            CaseFieldRead(
                id=f.id,
                type=f.type,
                description=f.description,
                nullable=f.nullable,
                default=f.default,
                reserved=f.reserved,
                options=f.options,
                value=fields.get(f.id),
            )
        )

    tag_reads = [
        CaseTagRead.model_validate(tag, from_attributes=True) for tag in case.tags
    ]

    return CaseRead(
        id=case.id,
        short_id=case.short_id,
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
        payload=case.payload,
        tags=tag_reads,
    )


@router.patch("/{case_id}", status_code=HTTP_200_OK)
async def update_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseUpdate,
    case_id: uuid.UUID,
) -> CaseRead:
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Case not found",
        )
    try:
        updated_case = await service.update_case(case, params)
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except DBAPIError as e:
        logger.exception("Database error occurred during case operation")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database operation failed",
        ) from e

    fields = await service.fields.get_fields(updated_case) or {}
    field_definitions = await service.fields.list_fields()
    field_schema = await service.fields.get_field_schema()
    final_fields: list[CaseFieldRead] = []
    for defn in field_definitions:
        f = CaseFieldReadMinimal.from_sa(defn, field_schema=field_schema)
        final_fields.append(
            CaseFieldRead(
                id=f.id,
                type=f.type,
                description=f.description,
                nullable=f.nullable,
                default=f.default,
                reserved=f.reserved,
                options=f.options,
                value=fields.get(f.id),
            )
        )

    tag_reads = [
        CaseTagRead.model_validate(tag, from_attributes=True)
        for tag in updated_case.tags
    ]

    return CaseRead(
        id=updated_case.id,
        short_id=updated_case.short_id,
        created_at=updated_case.created_at,
        updated_at=updated_case.updated_at,
        summary=updated_case.summary,
        status=updated_case.status,
        priority=updated_case.priority,
        severity=updated_case.severity,
        description=updated_case.description,
        assignee=UserRead.model_validate(updated_case.assignee, from_attributes=True)
        if updated_case.assignee
        else None,
        fields=final_fields,
        payload=updated_case.payload,
        tags=tag_reads,
    )


@router.delete("/{case_id}", status_code=HTTP_200_OK)
async def delete_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> None:
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Case not found",
        )
    await service.delete_case(case)


@router.get("/{case_id}/comments", status_code=HTTP_200_OK)
async def list_comments(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[CaseCommentRead]:
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    res: list[CaseCommentRead] = []
    for comment, user in await comments_svc.list_comments(case):
        comment_data = CaseCommentRead.model_validate(comment, from_attributes=True)
        if user:
            comment_data.user = UserRead.model_validate(user, from_attributes=True)
        res.append(comment_data)
    return res


@router.post("/{case_id}/comments", status_code=HTTP_201_CREATED)
async def create_comment(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseCommentCreate,
) -> CaseCommentRead:
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.create_comment(case, params)
    return CaseCommentRead.model_validate(comment, from_attributes=True)


@router.patch(
    "/{case_id}/comments/{comment_id}",
    status_code=HTTP_200_OK,
)
async def update_comment(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
    params: CaseCommentUpdate,
) -> CaseCommentRead:
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.get_comment(comment_id)
    if comment is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    updated_comment = await comments_svc.update_comment(comment, params)
    return CaseCommentRead.model_validate(updated_comment, from_attributes=True)


# Separate router for comment operations that don't require case_id in path
comments_router = APIRouter(
    prefix="/internal/comments", tags=["internal-comments"], include_in_schema=False
)


@comments_router.patch(
    "/{comment_id}",
    status_code=HTTP_200_OK,
)
async def update_comment_by_id(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    comment_id: uuid.UUID,
    params: CaseCommentUpdate,
) -> CaseCommentRead:
    """Update a comment by ID without requiring case_id in the path."""
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.get_comment(comment_id)
    if comment is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    updated_comment = await comments_svc.update_comment(comment, params)
    return CaseCommentRead.model_validate(updated_comment, from_attributes=True)


@router.delete("/{case_id}/comments/{comment_id}", status_code=HTTP_204_NO_CONTENT)
async def delete_comment(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
) -> None:
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.get_comment(comment_id)
    if comment is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    await comments_svc.delete_comment(comment)


@router.get(
    "/{case_id}/events",
    status_code=HTTP_200_OK,
    response_model_exclude_none=True,
)
async def list_events_with_users(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> CaseEventsWithUsers:
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    db_events = await service.events.list_events(case)
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
        if isinstance(root_evt, TaskAssigneeChangedEventRead):
            if root_evt.old is not None:
                user_ids.add(root_evt.old)
            if root_evt.new is not None:
                user_ids.add(root_evt.new)
        if root_evt.user_id is not None:
            user_ids.add(root_evt.user_id)
        events.append(evt)

    users = (
        [
            UserRead.model_validate(u, from_attributes=True)
            for u in await search_users(session=session, user_ids=user_ids)
        ]
        if user_ids
        else []
    )

    return CaseEventsWithUsers(events=events, users=users)


class CaseMetricsRequest(Schema):
    """Request body for case metrics."""

    case_ids: list[uuid.UUID]


@duration_router.post("/metrics", status_code=HTTP_200_OK)
async def get_case_metrics(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseMetricsRequest,
) -> list[CaseDurationMetric]:
    """Get case metrics as time-series for the provided case IDs."""
    if not params.case_ids:
        return []

    cases_service = CasesService(session, role)
    duration_service = CaseDurationService(session, role)

    cases = []
    for case_id in params.case_ids:
        case = await cases_service.get_case(case_id)
        if case is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Case with ID {case_id} not found",
            )
        cases.append(case)

    return await duration_service.compute_time_series(cases)


@task_router.get("/{case_id}/tasks", status_code=HTTP_200_OK)
async def list_tasks(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[CaseTaskRead]:
    service = CaseTasksService(session, role)
    tasks = await service.list_tasks(case_id)
    return [
        CaseTaskRead(
            id=task.id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            case_id=task.case_id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            assignee=UserRead.model_validate(task.assignee, from_attributes=True)
            if task.assignee
            else None,
            workflow_id=WorkflowUUID.new(task.workflow_id).short()
            if task.workflow_id
            else None,
            default_trigger_values=task.default_trigger_values,
        )
        for task in tasks
    ]


@task_router.post("/{case_id}/tasks", status_code=HTTP_201_CREATED)
async def create_task(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseTaskCreate,
) -> CaseTaskRead:
    service = CaseTasksService(session, role)
    try:
        task = await service.create_task(case_id, params)
        return CaseTaskRead(
            id=task.id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            case_id=task.case_id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            assignee=UserRead.model_validate(task.assignee, from_attributes=True)
            if task.assignee
            else None,
            workflow_id=WorkflowUUID.new(task.workflow_id).short()
            if task.workflow_id
            else None,
            default_trigger_values=task.default_trigger_values,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Case not found",
        ) from e
    except Exception as e:
        logger.exception("Failed to create task")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Failed to create task",
        ) from e


@task_router.patch("/{case_id}/tasks/{task_id}", status_code=HTTP_200_OK)
async def update_task(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    task_id: uuid.UUID,
    params: CaseTaskUpdate,
) -> CaseTaskRead:
    service = CaseTasksService(session, role)
    try:
        existing = await service.get_task(task_id)
        if existing.case_id != case_id:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Task not found")
        task = await service.update_task(task_id, params)
        return CaseTaskRead(
            id=task.id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            case_id=task.case_id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            assignee=UserRead.model_validate(task.assignee, from_attributes=True)
            if task.assignee
            else None,
            workflow_id=WorkflowUUID.new(task.workflow_id).short()
            if task.workflow_id
            else None,
            default_trigger_values=task.default_trigger_values,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Task not found",
        ) from e
    except Exception as e:
        logger.exception(f"Failed to update task: {e}")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Failed to update task",
        ) from e


@task_router.delete("/{case_id}/tasks/{task_id}", status_code=HTTP_204_NO_CONTENT)
async def delete_task(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    task_id: uuid.UUID,
) -> None:
    service = CaseTasksService(session, role)
    try:
        existing = await service.get_task(task_id)
        if existing.case_id != case_id:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Task not found")
        await service.delete_task(task_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Task not found",
        ) from e
    except Exception as e:
        logger.exception(f"Failed to delete task: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete task",
        ) from e


@task_router.get("/tasks/{task_id}", status_code=HTTP_200_OK)
async def get_task_by_id(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    task_id: uuid.UUID,
) -> CaseTaskRead:
    """Get a task by ID."""
    service = CaseTasksService(session, role)
    try:
        task = await service.get_task(task_id)
        return CaseTaskRead(
            id=task.id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            case_id=task.case_id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            assignee=UserRead.model_validate(task.assignee, from_attributes=True)
            if task.assignee
            else None,
            workflow_id=WorkflowUUID.new(task.workflow_id).short()
            if task.workflow_id
            else None,
            default_trigger_values=task.default_trigger_values,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Task not found",
        ) from e


@task_router.patch("/tasks/{task_id}", status_code=HTTP_200_OK)
async def update_task_by_id(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    task_id: uuid.UUID,
    params: CaseTaskUpdate,
) -> CaseTaskRead:
    """Update a task by ID."""
    service = CaseTasksService(session, role)
    try:
        task = await service.update_task(task_id, params)
        return CaseTaskRead(
            id=task.id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            case_id=task.case_id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            assignee=UserRead.model_validate(task.assignee, from_attributes=True)
            if task.assignee
            else None,
            workflow_id=WorkflowUUID.new(task.workflow_id).short()
            if task.workflow_id
            else None,
            default_trigger_values=task.default_trigger_values,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Task not found",
        ) from e
    except Exception as e:
        logger.exception(f"Failed to update task: {e}")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Failed to update task",
        ) from e


@task_router.delete("/tasks/{task_id}", status_code=HTTP_204_NO_CONTENT)
async def delete_task_by_id(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    task_id: uuid.UUID,
) -> None:
    """Delete a task by ID."""
    service = CaseTasksService(session, role)
    try:
        await service.delete_task(task_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Task not found",
        ) from e
    except Exception as e:
        logger.exception(f"Failed to delete task: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete task",
        ) from e


# =============================================================================
# Extended request schemas for UDF compatibility
# =============================================================================


class CaseCreateWithTags(CaseCreate):
    """Extended case create request with tags support for UDFs."""

    tags: list[str] | None = None


class CaseUpdateWithTags(CaseUpdate):
    """Extended case update request with tags and append support for UDFs."""

    tags: list[str] | None = None
    append_description: bool = False


class AssignUserByEmailRequest(Schema):
    """Request body for assigning a user by email."""

    assignee_email: str


# =============================================================================
# UDF-compatible routes (simple dict responses for SDK calls)
# These use /internal/cases prefix with specific sub-paths
# =============================================================================


@router.post("/simple", status_code=HTTP_201_CREATED)
async def create_case_simple(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseCreateWithTags,
) -> InternalCaseData:
    """Create a case and return a simple dict representation.

    This endpoint is optimized for SDK/UDF use cases where the full CaseRead
    response is not needed.
    """
    service = CasesService(session, role)
    try:
        case = await service.create_case(
            CaseCreate(
                summary=params.summary,
                description=params.description,
                priority=params.priority,
                severity=params.severity,
                status=params.status,
                fields=params.fields,
                assignee_id=params.assignee_id,
                payload=params.payload,
            )
        )

        # Add tags if provided
        if params.tags:
            for tag in params.tags:
                await service.tags.add_case_tag(case.id, tag)
            await session.refresh(case)

    except NoResultFound as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return InternalCaseData.model_validate(case, from_attributes=True)


@router.patch("/{case_id}/simple", status_code=HTTP_200_OK)
async def update_case_simple(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseUpdateWithTags,
    case_id: uuid.UUID,
) -> InternalCaseData:
    """Update a case and return a simple dict representation.

    This endpoint is optimized for SDK/UDF use cases where the full CaseRead
    response is not needed.
    """
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    # Handle description append mode
    update_params: dict[str, Any] = {}
    if params.summary is not None:
        update_params["summary"] = params.summary
    if params.description is not None:
        if params.append_description and case.description:
            update_params["description"] = f"{case.description}\n{params.description}"
        else:
            update_params["description"] = params.description
    if params.priority is not None:
        update_params["priority"] = params.priority
    if params.severity is not None:
        update_params["severity"] = params.severity
    if params.status is not None:
        update_params["status"] = params.status
    if params.fields is not None:
        update_params["fields"] = params.fields
    if params.assignee_id is not None:
        update_params["assignee_id"] = params.assignee_id
    if params.payload is not None:
        update_params["payload"] = params.payload

    try:
        updated_case = await service.update_case(case, CaseUpdate(**update_params))

        # Update tags if provided (replace all existing tags)
        if params.tags is not None:
            existing_tags = await service.tags.list_tags_for_case(case.id)
            for existing_tag in existing_tags:
                await service.tags.remove_case_tag(case.id, existing_tag.ref)
            for tag in params.tags:
                await service.tags.add_case_tag(case.id, tag)
            await session.refresh(updated_case)

    except NoResultFound as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except DBAPIError as e:
        logger.exception("Database error occurred during case operation")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database operation failed",
        ) from e

    return InternalCaseData.model_validate(updated_case, from_attributes=True)


@router.post("/{case_id}/comments/simple", status_code=HTTP_201_CREATED)
async def create_comment_simple(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseCommentCreate,
) -> InternalCaseCommentData:
    """Create a comment and return a simple dict representation."""
    cases_svc = CasesService(session, role)
    case = await cases_svc.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.create_comment(case, params)
    return InternalCaseCommentData.model_validate(comment, from_attributes=True)


@comments_router.patch("/{comment_id}/simple", status_code=HTTP_200_OK)
async def update_comment_simple(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    comment_id: uuid.UUID,
    params: CaseCommentUpdate,
) -> InternalCaseCommentData:
    """Update a comment and return a simple dict representation."""
    comments_svc = CaseCommentsService(session, role)
    comment = await comments_svc.get_comment(comment_id)
    if comment is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    updated_comment = await comments_svc.update_comment(comment, params)
    return InternalCaseCommentData.model_validate(updated_comment, from_attributes=True)


@router.post("/{case_id}/assign", status_code=HTTP_200_OK)
async def assign_user_to_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    assignee_id: uuid.UUID,
) -> InternalCaseData:
    """Assign a user to a case by user ID.

    Returns a simple dict representation of the updated case.
    """
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    try:
        updated_case = await service.update_case(
            case, CaseUpdate(assignee_id=assignee_id)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return InternalCaseData.model_validate(updated_case, from_attributes=True)


@router.post("/{case_id}/assign-by-email", status_code=HTTP_200_OK)
async def assign_user_by_email_to_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: AssignUserByEmailRequest,
) -> InternalCaseData:
    """Assign a user to a case by email address.

    Returns a simple dict representation of the updated case.
    """
    from tracecat.auth.users import lookup_user_by_email

    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    # Look up user by email
    user = await lookup_user_by_email(session=session, email=params.assignee_email)
    if not user:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"User with email {params.assignee_email} not found",
        )

    try:
        updated_case = await service.update_case(case, CaseUpdate(assignee_id=user.id))
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return InternalCaseData.model_validate(updated_case, from_attributes=True)


# =============================================================================
# Conditionally include feature-gated sub-routers
# =============================================================================

if is_feature_enabled(FeatureFlag.CASE_TASKS):
    router.include_router(task_router)

if is_feature_enabled(FeatureFlag.CASE_DURATIONS):
    router.include_router(duration_router)
