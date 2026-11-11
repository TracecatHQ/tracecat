from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, NoResultFound
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.schemas import UserRead
from tracecat.auth.users import search_users
from tracecat.authz.controls import require_scope
from tracecat.cases.dependencies import ExecutorCaseIDPath, PayloadPathsQuery
from tracecat.cases.dropdowns.schemas import CaseDropdownValueRead
from tracecat.cases.dropdowns.service import CaseDropdownValuesService
from tracecat.cases.durations.schemas import CaseDurationMetric
from tracecat.cases.durations.service import CaseDurationService
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.event_schemas import (
    AssigneeChangedEventRead,
    CaseEventRead,
    CaseEventsWithUsers,
    TaskAssigneeChangedEventRead,
)
from tracecat.cases.filters import parse_assignee_filter, resolve_tag_ids
from tracecat.cases.hierarchy import (
    TOP_LEVEL,
    CaseHierarchy,
    CaseHierarchyService,
)
from tracecat.cases.rows.schemas import CaseTableRowRead
from tracecat.cases.rows.service import CaseTableRowsService
from tracecat.cases.schemas import (
    CaseAggregateRequest,
    CaseAggregateResponse,
    CaseCommentCreate,
    CaseCommentRead,
    CaseCommentThreadRead,
    CaseCommentUpdate,
    CaseCreate,
    CaseFieldRead,
    CaseFieldReadMinimal,
    CaseHierarchyFilter,
    CaseRead,
    CaseReadMinimal,
    CaseSearchAggregateRead,
    CaseTaskCreate,
    CaseTaskRead,
    CaseTaskUpdate,
    CaseUpdate,
    InternalCaseCommentData,
    InternalCaseData,
)
from tracecat.cases.search_schemas import CaseQuery, CaseSearchOrder, CaseSearchRequest
from tracecat.cases.service import (
    CaseCommentsService,
    CasesService,
    CaseTasksService,
)
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.core.schemas import Schema
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.errors import is_data_error
from tracecat.db.models import Case
from tracecat.dsl.common import DSLInput
from tracecat.dsl.validation import (
    format_input_schema_validation_error,
    normalize_trigger_inputs,
)
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams
from tracecat.query.errors import TracecatQueryOverflowError, TracecatQueryTimeoutError
from tracecat.query.lexical_types import LexicalSearchError, SearchMode
from tracecat.registry.lock.types import RegistryLock
from tracecat.tiers.enums import Entitlement
from tracecat.validation.schemas import ValidationDetail
from tracecat.workflow.executions.correlation import build_case_task_correlation_id
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService

router = APIRouter(
    prefix="/internal/cases", tags=["internal-cases"], include_in_schema=False
)

# Sub-routers for feature-gated routes (service-layer entitlement checks)
task_router = APIRouter()
duration_router = APIRouter()


def _raise_comment_http_error(
    exc: TracecatValidationError | TracecatAuthorizationError,
) -> NoReturn:
    if isinstance(exc, TracecatValidationError):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _list_case_dropdown_values(
    *,
    session: AsyncDBSession,
    role: ExecutorWorkspaceRole,
    case_id: uuid.UUID,
) -> list[CaseDropdownValueRead]:
    dropdown_service = CaseDropdownValuesService(session, role)
    if not await dropdown_service.has_entitlement(Entitlement.CASE_ADDONS):
        return []
    return await dropdown_service.list_values_for_case(case_id)


async def _read_case_hierarchy(
    *,
    session: AsyncDBSession,
    role: ExecutorWorkspaceRole,
    case: Case,
) -> CaseHierarchy:
    """Return the case's parent summary and sub-case count."""
    hierarchies = await CaseHierarchyService(session, role).get_hierarchy([case])
    return hierarchies.get(case.id, TOP_LEVEL)


async def _list_case_rows(
    *,
    session: AsyncDBSession,
    role: ExecutorWorkspaceRole,
    case_id: uuid.UUID,
    include_rows: bool,
) -> list[CaseTableRowRead]:
    if not include_rows:
        return []
    rows_service = CaseTableRowsService(session, role)
    rows_by_case = await rows_service.hydrate_case_rows(
        case_ids=[case_id],
        include_row_data=True,
    )
    return rows_by_case.get(case_id, [])


@router.post(
    "/aggregate",
    response_model=CaseAggregateResponse,
    description=(
        "Filter and aggregate workspace cases in PostgreSQL. BIGINT/NUMERIC sums, "
        "means, medians, and NUMERIC min/max are widened to float8 JSON numbers. "
        "NUMERIC group keys remain exact decimal strings. TEXT/SELECT group keys "
        "use their first 256 characters, so values sharing that prefix collapse "
        "into one group. Missing values form a null group."
    ),
)
@require_scope("case:read")
async def aggregate_cases(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseAggregateRequest,
) -> CaseAggregateResponse:
    """Filter, group, and aggregate cases in the authenticated workspace."""
    service = CasesService(session, role=role)
    try:
        return await service.aggregate_cases(params)
    except TracecatQueryOverflowError:
        raise
    except (ValueError, TracecatValidationError) as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("")
@require_scope("case:read")
async def list_cases(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    payload_paths: PayloadPathsQuery,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum items per page",
    ),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
    order_by: Literal[
        "created_at",
        "updated_at",
        "priority",
        "severity",
        "status",
        "tasks",
        "assignee",
        "tags",
    ]
    | None = Query(
        None,
        description="Column name to order by (e.g. created_at, updated_at, priority, severity, status, tasks, assignee, tags). Default: created_at",
    ),
    sort: Literal["asc", "desc"] | None = Query(
        None, description="Direction to sort (asc or desc)"
    ),
    include_rows: bool = Query(False, description="Include linked table rows"),
    field_ids: list[str] | None = Query(
        None, description="Include only the requested custom field IDs"
    ),
    include_durations: bool = Query(False, description="Include case duration values"),
    include_payload: bool = Query(False, description="Include case payload"),
    payload_fields: list[str] | None = Query(
        None,
        description=(
            "Return only these literal top-level payload keys, automatically including "
            "the subset regardless of include_payload. Dots are literal; nested values "
            "are returned whole. Missing keys are omitted and null values preserved. "
            "An empty list acts as omitted; an empty string selects the literal empty key."
        ),
    ),
    include_count: bool = Query(True, description="Include exact total_estimate"),
    hierarchy: CaseHierarchyFilter = Query("all"),
) -> CursorPaginatedResponse[CaseReadMinimal]:
    service = CasesService(session, role)

    try:
        cases = await service.list_cases(
            limit=limit,
            cursor=cursor,
            reverse=reverse,
            order_by=order_by,
            sort=sort,
            include_durations=include_durations,
            include_payload=include_payload,
            payload_fields=payload_fields,
            payload_paths=payload_paths,
            include_count=include_count,
            hierarchy=hierarchy,
            include_rows=include_rows,
            field_ids=field_ids,
        )
    except (ValueError, TracecatValidationError) as e:
        logger.warning(f"Invalid request for list cases: {e}")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except (HTTPException, EntitlementRequired):
        raise
    except Exception as e:
        logger.error(f"Failed to list cases: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve cases",
        ) from e
    return cases


@router.post("/search")
@require_scope("case:read")
async def query_cases(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession, params: CaseSearchRequest
) -> CursorPaginatedResponse[CaseReadMinimal]:
    """Search with typed AND/OR/NOT filters and independent result details."""
    try:
        return await CasesService(session, role).query_cases(params)
    except LexicalSearchError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    except (ValueError, TracecatValidationError) as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/search/aggregate")
@require_scope("case:read")
async def query_case_aggregates(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession, params: CaseQuery
) -> CaseSearchAggregateRead:
    """Count the same typed matching conditions as search."""
    try:
        return await CasesService(session, role).query_case_aggregates(params)
    except LexicalSearchError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    except (ValueError, TracecatValidationError) as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/search")
@require_scope("case:read")
async def search_cases(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    payload_paths: PayloadPathsQuery,
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
        description="Text to search for in case summary, description, short ID, or case UUID",
    ),
    search_mode: SearchMode | None = Query(
        None,
        description="Explicit full_text or literal; omitted mode preserves legacy search.",
    ),
    search_columns: list[Literal["summary", "description"]] | None = Query(
        None, description="Selected text fields; omitted searches both."
    ),
    short_id: str | None = Query(
        None,
        description="Search by exact case short ID (e.g. 42 or CASE-0042)",
    ),
    case_id: uuid.UUID | None = Query(None, description="Filter by exact case UUID"),
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
        None,
        description="Filter by tag IDs or slugs (matches cases with any of the tags)",
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
    order_by: CaseSearchOrder | None = Query(
        None,
        description="Full-text defaults to relevance; legacy/literal defaults to created_at. Explicit columns preserve their ordering.",
    ),
    sort: Literal["asc", "desc"] | None = Query(
        None, description="Direction to sort (asc or desc)"
    ),
    include_rows: bool = Query(False, description="Include linked table rows"),
    field_ids: list[str] | None = Query(
        None, description="Include only the requested custom field IDs"
    ),
    include_durations: bool = Query(False, description="Include case duration values"),
    include_payload: bool = Query(False, description="Include case payload"),
    payload_fields: list[str] | None = Query(
        None,
        description=(
            "Return only these literal top-level payload keys, automatically including "
            "the subset regardless of include_payload. Dots are literal; nested values "
            "are returned whole. Missing keys are omitted and null values preserved. "
            "An empty list acts as omitted; an empty string selects the literal empty key."
        ),
    ),
    include_count: bool = Query(True),
    hierarchy: CaseHierarchyFilter = Query("all"),
    parent_id: uuid.UUID | None = Query(None),
) -> CursorPaginatedResponse[CaseReadMinimal]:
    service = CasesService(session, role)

    tag_ids = await resolve_tag_ids(tags=tags, role=role, session=session)

    pagination_params = CursorPaginationParams(
        limit=limit,
        cursor=cursor,
        reverse=reverse,
    )

    parsed_assignee_filters = parse_assignee_filter(assignee_id)

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
            search_mode=search_mode,
            search_columns=search_columns,
            short_id=short_id,
            case_id=case_id,
            include_count=include_count,
            hierarchy=hierarchy,
            parent_id=parent_id,
            status=status,
            priority=priority,
            severity=severity,
            assignee_ids=parsed_assignee_filters["assignee_ids"],
            include_unassigned=parsed_assignee_filters["include_unassigned"],
            tag_ids=tag_ids,
            dropdown_filters=parsed_dropdown_filters,
            start_time=start_time,
            end_time=end_time,
            updated_after=updated_after,
            updated_before=updated_before,
            order_by=order_by,
            sort=sort,
            include_durations=include_durations,
            include_payload=include_payload,
            payload_fields=payload_fields,
            payload_paths=payload_paths,
            include_rows=include_rows,
            field_ids=field_ids,
        )
        return cases

    except LexicalSearchError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    except (ValueError, TracecatValidationError) as e:
        logger.warning(f"Invalid request for search cases: {e}")
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except (HTTPException, EntitlementRequired, TracecatQueryTimeoutError):
        raise
    except Exception as e:
        logger.error(f"Failed to search cases: {e}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve cases",
        ) from e


@router.get("/{case_id}")
@require_scope("case:read")
async def get_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
    include_rows: bool = Query(False, description="Include linked table rows"),
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
                **f.model_dump(),
                value=fields.get(f.id),
            )
        )

    tag_reads = [
        CaseTagRead.model_validate(tag, from_attributes=True) for tag in case.tags
    ]
    dropdown_reads = await _list_case_dropdown_values(
        session=session, role=role, case_id=case.id
    )
    rows = await _list_case_rows(
        session=session,
        role=role,
        case_id=case.id,
        include_rows=include_rows,
    )

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
        rows=rows,
        **(
            await _read_case_hierarchy(session=session, role=role, case=case)
        ).read_fields(),
    )


@router.post("", status_code=HTTP_201_CREATED)
@require_scope("case:create")
async def create_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseCreate,
) -> CaseRead:
    service = CasesService(session, role)
    try:
        case = await service.create_case(params)
    except (ValueError, TracecatValidationError) as e:
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
                **f.model_dump(),
                value=fields.get(f.id),
            )
        )

    tag_reads = [
        CaseTagRead.model_validate(tag, from_attributes=True) for tag in case.tags
    ]
    dropdown_reads = await _list_case_dropdown_values(
        session=session, role=role, case_id=case.id
    )

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
        **(
            await _read_case_hierarchy(session=session, role=role, case=case)
        ).read_fields(),
    )


@router.patch("/{case_id}", status_code=HTTP_200_OK)
@require_scope("case:update")
async def update_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseUpdate,
    case_id: ExecutorCaseIDPath,
    include_rows: bool = Query(False, description="Include linked table rows"),
) -> CaseRead:
    service = CasesService(session, role)
    case = await service.get_case(case_id, for_update=True)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Case not found",
        )
    try:
        updated_case = await service.update_case(case, params)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except DBAPIError as e:
        if is_data_error(e):
            raise
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
                **f.model_dump(),
                value=fields.get(f.id),
            )
        )

    tag_reads = [
        CaseTagRead.model_validate(tag, from_attributes=True)
        for tag in updated_case.tags
    ]
    dropdown_reads = await _list_case_dropdown_values(
        session=session, role=role, case_id=updated_case.id
    )
    rows = await _list_case_rows(
        session=session,
        role=role,
        case_id=updated_case.id,
        include_rows=include_rows,
    )

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
        dropdown_values=dropdown_reads,
        rows=rows,
        **(
            await _read_case_hierarchy(session=session, role=role, case=updated_case)
        ).read_fields(),
    )


@router.delete("/{case_id}", status_code=HTTP_200_OK)
@require_scope("case:delete")
async def delete_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
@require_scope("case:read")
async def list_comments(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
) -> list[CaseCommentRead]:
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    return await comments_svc.list_comments(case)


@router.get("/{case_id}/comments/threads", status_code=HTTP_200_OK)
@require_scope("case:read")
async def list_comment_threads(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
) -> list[CaseCommentThreadRead]:
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    comments_svc = CaseCommentsService(session, role)
    return await comments_svc.list_comment_threads(case)


@router.post("/{case_id}/comments", status_code=HTTP_201_CREATED)
@require_scope("case:update")
async def create_comment(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
    try:
        comment = await comments_svc.create_comment(case, params)
    except (TracecatAuthorizationError, TracecatValidationError) as exc:
        _raise_comment_http_error(exc)
    return await comments_svc.serialize_comment_with_mentions(comment)


@router.patch(
    "/{case_id}/comments/{comment_id}",
    status_code=HTTP_200_OK,
)
@require_scope("case:update")
async def update_comment(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
    comment = await comments_svc.get_comment_in_case(case.id, comment_id)
    if comment is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    try:
        updated_comment = await comments_svc.update_comment(comment, params)
    except (TracecatAuthorizationError, TracecatValidationError) as exc:
        _raise_comment_http_error(exc)
    return await comments_svc.serialize_comment_with_mentions(updated_comment)


# Separate router for comment operations that don't require case_id in path
comments_router = APIRouter(
    prefix="/internal/comments", tags=["internal-comments"], include_in_schema=False
)


@comments_router.patch(
    "/{comment_id}",
    status_code=HTTP_200_OK,
)
@require_scope("case:update")
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
    try:
        updated_comment = await comments_svc.update_comment(comment, params)
    except (TracecatAuthorizationError, TracecatValidationError) as exc:
        _raise_comment_http_error(exc)
    return await comments_svc.serialize_comment_with_mentions(updated_comment)


@comments_router.get(
    "/{comment_id}/thread",
    status_code=HTTP_200_OK,
)
@require_scope("case:read")
async def get_comment_thread(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    comment_id: uuid.UUID,
) -> CaseCommentThreadRead:
    """Get a thread by any comment ID in the thread."""
    comments_svc = CaseCommentsService(session, role)
    thread = await comments_svc.get_comment_thread(comment_id)
    if thread is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    return thread


@router.delete("/{case_id}/comments/{comment_id}", status_code=HTTP_204_NO_CONTENT)
@require_scope("case:delete")
async def delete_comment(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
    comment = await comments_svc.get_comment_in_case(case.id, comment_id)
    if comment is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found",
        )
    try:
        await comments_svc.delete_comment(comment)
    except (TracecatAuthorizationError, TracecatValidationError) as exc:
        _raise_comment_http_error(exc)


@router.get(
    "/{case_id}/events",
    status_code=HTTP_200_OK,
    response_model_exclude_none=True,
)
@require_scope("case:read")
async def list_events_with_users(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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

    case_ids: list[str]


@duration_router.post("/metrics", status_code=HTTP_200_OK)
@require_scope("case:read")
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

    field_definitions = await cases_service.fields.list_fields()
    field_schema = await cases_service.fields.get_field_schema()
    field_templates = [
        CaseFieldReadMinimal.from_sa(defn, field_schema=field_schema)
        for defn in field_definitions
    ]

    cases = []
    case_context: dict[
        str, tuple[list[dict[str, Any]], list[CaseTagRead], list[CaseDropdownValueRead]]
    ] = {}
    for case_ref in params.case_ids:
        try:
            case_id = await cases_service.resolve_case_id(case_ref)
        except TracecatValidationError as exc:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        except TracecatNotFoundError as exc:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        case = await cases_service.get_case(case_id)
        if case is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Case with ID {case_id} not found",
            )

        fields = await cases_service.fields.get_fields(case) or {}
        field_reads = [
            CaseFieldRead(
                **f.model_dump(),
                value=fields.get(f.id),
            )
            for f in field_templates
        ]
        field_dicts = [field.model_dump() for field in field_reads]
        tag_reads = [
            CaseTagRead.model_validate(tag, from_attributes=True) for tag in case.tags
        ]
        dropdown_reads = await _list_case_dropdown_values(
            session=session, role=role, case_id=case.id
        )

        case_context[str(case.id)] = (field_dicts, tag_reads, dropdown_reads)
        cases.append(case)

    metrics = await duration_service.compute_time_series(cases)
    enriched_metrics: list[CaseDurationMetric] = []
    for metric in metrics:
        context = case_context.get(metric.case_id)
        if context is None:
            enriched_metrics.append(metric)
            continue
        fields, tags, dropdown_values = context
        enriched_metrics.append(
            metric.model_copy(
                update={
                    "fields": fields,
                    "tags": tags,
                    "dropdown_values": dropdown_values,
                }
            )
        )

    return enriched_metrics


@task_router.get("/{case_id}/tasks", status_code=HTTP_200_OK)
@require_scope("case:read")
async def list_tasks(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
@require_scope("case:create")
async def create_task(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
@require_scope("case:update")
async def update_task(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
@require_scope("case:delete")
async def delete_task(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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


class CaseTaskRunRequest(Schema):
    """Request body for running a case task's workflow."""

    inputs: dict[str, Any] | None = None


class CaseTaskRunResponse(Schema):
    """A started case task workflow run."""

    task_id: uuid.UUID
    workflow_id: str
    workflow_execution_id: str
    message: str


@task_router.post("/{case_id}/tasks/{task_id}/run", status_code=HTTP_201_CREATED)
@require_scope("case:read", "workflow:execute")
async def run_task(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
    task_id: uuid.UUID,
    params: CaseTaskRunRequest,
) -> CaseTaskRunResponse:
    """Run a case task's workflow, linked to the task like a run from the UI."""
    service = CaseTasksService(session, role)
    try:
        task = await service.get_task(task_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Task not found"
        ) from e
    if task.case_id != case_id:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Task not found")
    if task.workflow_id is None:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Task has no associated workflow. Set a workflow on the task first.",
        )

    wf_id = WorkflowUUID.new(task.workflow_id)
    defn = await WorkflowDefinitionsService(
        session, role=role
    ).get_definition_by_workflow_id(wf_id, load_relationships=False)
    if defn is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Workflow {wf_id.short()} has no published definition. "
            "Publish the workflow first.",
        )
    dsl = DSLInput(**defn.content)

    inputs: dict[str, Any] = {
        **(task.default_trigger_values or {}),
        **(params.inputs or {}),
        "case_id": str(case_id),
        "task_id": str(task_id),
    }
    if expects := dsl.entrypoint.expects:
        try:
            normalize_trigger_inputs(expects, inputs)
        except ValidationError as e:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=format_input_schema_validation_error(
                    ValidationDetail.list_from_pydantic(e)
                ),
            ) from e

    exec_service = await WorkflowExecutionsService.connect(role=role)
    try:
        response = await exec_service.create_workflow_execution_wait_for_start(
            dsl=dsl,
            wf_id=wf_id,
            payload=inputs,
            registry_lock=(
                RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None
            ),
            correlation_id=build_case_task_correlation_id(task_id),
        )
    except TracecatValidationError as e:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(e)) from e
    logger.info(
        "Case task workflow run started via internal API",
        task_id=task_id,
        workflow_id=wf_id,
        workflow_execution_id=response["wf_exec_id"],
    )
    return CaseTaskRunResponse(
        task_id=task_id,
        workflow_id=WorkflowUUID.new(response["wf_id"]).short(),
        workflow_execution_id=response["wf_exec_id"],
        message=response["message"],
    )


@task_router.get("/tasks/{task_id}", status_code=HTTP_200_OK)
@require_scope("case:read")
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
@require_scope("case:update")
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
@require_scope("case:delete")
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
    create_missing_tags: bool = False


class CaseUpdateWithTags(CaseUpdate):
    """Extended case update request with tags and append support for UDFs."""

    tags: list[str] | None = None
    create_missing_tags: bool = False
    append_description: bool = False


class AssignUserByEmailRequest(Schema):
    """Request body for assigning a user by email."""

    assignee_email: str


# =============================================================================
# UDF-compatible routes (simple dict responses for SDK calls)
# These use /internal/cases prefix with specific sub-paths
# =============================================================================


@router.post("/simple", status_code=HTTP_201_CREATED)
@require_scope("case:create")
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
                dropdown_values=params.dropdown_values,
                assignee_id=params.assignee_id,
                payload=params.payload,
            )
        )

        # Add tags if provided
        if params.tags:
            for tag in params.tags:
                await service.tags.add_case_tag(
                    case.id, tag, create_if_missing=params.create_missing_tags
                )
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
@require_scope("case:update")
async def update_case_simple(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: CaseUpdateWithTags,
    case_id: ExecutorCaseIDPath,
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
    if params.dropdown_values is not None:
        update_params["dropdown_values"] = params.dropdown_values
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
                await service.tags.add_case_tag(
                    case.id, tag, create_if_missing=params.create_missing_tags
                )
            await session.refresh(updated_case)

    except (NoResultFound, TracecatNotFoundError) as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
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
        if is_data_error(e):
            raise
        logger.exception("Database error occurred during case operation")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database operation failed",
        ) from e

    return InternalCaseData.model_validate(updated_case, from_attributes=True)


@router.post("/{case_id}/comments/simple", status_code=HTTP_201_CREATED)
@require_scope("case:update")
async def create_comment_simple(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
    try:
        comment = await comments_svc.create_comment(case, params)
    except (TracecatAuthorizationError, TracecatValidationError) as exc:
        _raise_comment_http_error(exc)
    return InternalCaseCommentData.model_validate(comment, from_attributes=True)


@comments_router.patch("/{comment_id}/simple", status_code=HTTP_200_OK)
@require_scope("case:update")
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
    try:
        updated_comment = await comments_svc.update_comment(comment, params)
    except (TracecatAuthorizationError, TracecatValidationError) as exc:
        _raise_comment_http_error(exc)
    return InternalCaseCommentData.model_validate(updated_comment, from_attributes=True)


@router.post("/{case_id}/assign", status_code=HTTP_200_OK)
@require_scope("case:update")
async def assign_user_to_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
@require_scope("case:update")
async def assign_user_by_email_to_case(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
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
# Include entitlement-gated sub-routers
# =============================================================================

router.include_router(task_router)
router.include_router(duration_router)
