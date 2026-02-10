import uuid
from datetime import datetime
from typing import Annotated, Literal

from asyncpg import DuplicateColumnError
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import DBAPIError, NoResultFound, ProgrammingError
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.auth.schemas import UserRead
from tracecat.auth.types import Role
from tracecat.auth.users import search_users
from tracecat.authz.controls import require_scope
from tracecat.cases.dropdowns.service import CaseDropdownValuesService
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import (
    AssigneeChangedEventRead,
    CaseCommentCreate,
    CaseCommentRead,
    CaseCommentUpdate,
    CaseCreate,
    CaseEventRead,
    CaseEventsWithUsers,
    CaseFieldCreate,
    CaseFieldRead,
    CaseFieldReadMinimal,
    CaseFieldUpdate,
    CaseRead,
    CaseReadMinimal,
    CaseTaskCreate,
    CaseTaskRead,
    CaseTaskUpdate,
    CaseUpdate,
    TaskAssigneeChangedEventRead,
)
from tracecat.cases.service import (
    CaseCommentsService,
    CaseFieldsService,
    CasesService,
    CaseTasksService,
)
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.pagination import (
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.tiers.enums import Entitlement

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
    ),
]


# Case Management


@cases_router.get("")
@require_scope("case:read")
async def list_cases(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum items per page",
    ),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
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
    """List cases with default filtering and sorting options."""
    service = CasesService(session, role)

    try:
        cases = await service.list_cases(
            limit=limit,
            cursor=cursor,
            reverse=reverse,
            order_by=order_by,
            sort=sort,
        )
    except ValueError as e:
        logger.warning(f"Invalid request for list cases: {e}")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
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


@cases_router.get("/search")
@require_scope("case:read")
async def search_cases(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum items per page",
    ),
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
    tags: list[str] | None = Query(
        None, description="Filter by tag IDs or slugs (AND logic)"
    ),
    dropdown: list[str] | None = Query(
        None,
        description="Filter by dropdown values. Format: definition_ref:option_ref (AND across definitions, OR within)",
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
    assignee_id: list[str] | None = Query(
        None, description="Filter by assignee ID or 'unassigned'"
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
    """Search cases with cursor-based pagination, filtering, and sorting."""
    service = CasesService(session, role)

    # Convert tag identifiers to IDs
    tag_ids = []
    if tags:
        tags_service = CaseTagsService(session, role)
        for tag_identifier in tags:
            try:
                tag = await tags_service.get_tag_by_ref_or_id(tag_identifier)
                tag_ids.append(tag.id)
            except NoResultFound:
                # Skip tags that do not exist in the workspace
                continue

    pagination_params = CursorPaginationParams(
        limit=limit,
        cursor=cursor,
        reverse=reverse,
    )

    # Parse assignee_id - handle special "unassigned" value
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

    # Parse dropdown filters: "definition_ref:option_ref" -> {def_ref: [opt_refs]}
    parsed_dropdown_filters: dict[str, list[str]] | None = None
    if dropdown:
        parsed_dropdown_filters = {}
        for entry in dropdown:
            if ":" not in entry:
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail=f"Invalid dropdown filter format: {entry!r}. Expected 'definition_ref:option_ref'.",
                )
            def_ref, opt_ref = entry.split(":", 1)
            parsed_dropdown_filters.setdefault(def_ref, []).append(opt_ref)

    try:
        cases = await service.search_cases(
            pagination_params,
            search_term=search_term,
            status=status,
            priority=priority,
            severity=severity,
            assignee_ids=parsed_assignee_ids or None,
            include_unassigned=include_unassigned,
            tag_ids=tag_ids if tag_ids else None,
            dropdown_filters=parsed_dropdown_filters,
            start_time=start_time,
            end_time=end_time,
            updated_after=updated_after,
            updated_before=updated_before,
            order_by=order_by,
            sort=sort,
        )
    except ValueError as e:
        logger.warning(f"Invalid request for search cases: {e}")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to search cases: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve cases",
        ) from e
    return cases


@cases_router.get("/{case_id}")
@require_scope("case:read")
async def get_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> CaseRead:
    """Get a specific case."""
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
    final_fields = []
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

    # Tags are already loaded via selectinload
    tag_reads = [
        CaseTagRead.model_validate(tag, from_attributes=True) for tag in case.tags
    ]

    # Dropdown values
    dropdown_service = CaseDropdownValuesService(session, role)
    dropdown_reads = []
    if await dropdown_service.has_entitlement(Entitlement.CASE_ADDONS):
        dropdown_reads = await dropdown_service.list_values_for_case(case.id)

    # Match up the fields with the case field definitions
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
        dropdown_values=dropdown_reads,
    )


@cases_router.post("", status_code=HTTP_201_CREATED)
@require_scope("case:create")
async def create_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: CaseCreate,
) -> None:
    """Create a new case."""
    service = CasesService(session, role)
    try:
        await service.create_case(params)
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@cases_router.patch("/{case_id}", status_code=HTTP_204_NO_CONTENT)
@require_scope("case:update")
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
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    try:
        await service.update_case(case, params)
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except DBAPIError as e:
        while (cause := e.__cause__) is not None:
            e = cause
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@cases_router.delete("/{case_id}", status_code=HTTP_204_NO_CONTENT)
@require_scope("case:delete")
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
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    await service.delete_case(case)


# Case Comments
# Support comments as a first class activity type.
# We anticipate having other complex comment functionality in the future.
@cases_router.get("/{case_id}/comments", status_code=HTTP_200_OK)
@require_scope("case:read")
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
            status_code=HTTP_404_NOT_FOUND,
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


@cases_router.post("/{case_id}/comments", status_code=HTTP_201_CREATED)
@require_scope("case:create")
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
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    await comments_svc.create_comment(case, params)


@cases_router.patch(
    "/{case_id}/comments/{comment_id}",
    status_code=HTTP_204_NO_CONTENT,
)
@require_scope("case:update")
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
    await comments_svc.update_comment(comment, params)


@cases_router.delete(
    "/{case_id}/comments/{comment_id}", status_code=HTTP_204_NO_CONTENT
)
@require_scope("case:delete")
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


# Case Fields


@case_fields_router.get("")
@require_scope("case:read")
async def list_fields(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> list[CaseFieldReadMinimal]:
    """List all case fields."""
    service = CaseFieldsService(session, role)
    columns = await service.list_fields()
    field_schema = await service.get_field_schema()
    return [
        CaseFieldReadMinimal.from_sa(column, field_schema=field_schema)
        for column in columns
    ]


@case_fields_router.post("", status_code=HTTP_201_CREATED)
@require_scope("case:create")
async def create_field(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    params: CaseFieldCreate,
) -> None:
    """Create a new case field."""
    service = CaseFieldsService(session, role)
    try:
        await service.create_field(params)
    except ProgrammingError as e:
        # Drill down to the root cause
        while (cause := e.__cause__) is not None:
            e = cause
        if isinstance(e, DuplicateColumnError):
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=f"A field with the name '{params.name}' already exists",
            ) from e
        raise


@case_fields_router.patch("/{field_id}", status_code=HTTP_204_NO_CONTENT)
@require_scope("case:update")
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


@case_fields_router.delete("/{field_id}", status_code=HTTP_204_NO_CONTENT)
@require_scope("case:delete")
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
    status_code=HTTP_200_OK,
    response_model_exclude_none=True,
)
@require_scope("case:read")
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
            status_code=HTTP_404_NOT_FOUND,
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
        if isinstance(root_evt, TaskAssigneeChangedEventRead):
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


# Case Tasks


@cases_router.get(
    "/{case_id}/tasks",
    status_code=HTTP_200_OK,
)
@require_scope("case:read")
async def list_tasks(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[CaseTaskRead]:
    """List all tasks for a case."""
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


@cases_router.post(
    "/{case_id}/tasks",
    status_code=HTTP_201_CREATED,
)
@require_scope("case:create")
async def create_task(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseTaskCreate,
) -> CaseTaskRead:
    """Create a new task for a case."""
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


@cases_router.patch(
    "/{case_id}/tasks/{task_id}",
    status_code=HTTP_200_OK,
)
@require_scope("case:update")
async def update_task(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    task_id: uuid.UUID,
    params: CaseTaskUpdate,
) -> CaseTaskRead:
    """Update a task."""
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


@cases_router.delete(
    "/{case_id}/tasks/{task_id}",
    status_code=HTTP_204_NO_CONTENT,
)
@require_scope("case:delete")
async def delete_task(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    task_id: uuid.UUID,
) -> None:
    """Delete a task."""
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
