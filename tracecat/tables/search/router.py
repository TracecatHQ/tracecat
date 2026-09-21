"""Public table selection, status and retry; query routes belong to search."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import TableID
from tracecat.pagination import PaginationError
from tracecat.search.embeddings.service import WorkspaceEmbeddingService
from tracecat.search.embeddings.types import EmbeddingError
from tracecat.search.types import SearchError, SearchErrorCode, SearchScope
from tracecat.tables.search.schemas import (
    TableSearchConfiguration,
    TableSearchErrorResponse,
    TableSearchProgressPage,
    TableSearchProgressParams,
    TableSearchRetry,
    TableSearchSelection,
    TableSearchSelectionErrorResponse,
)
from tracecat.tables.search.service import TableSearchService

router = APIRouter(
    prefix="/{table_id}/search",
    tags=["tables"],
    responses={code: {"model": TableSearchErrorResponse} for code in (404, 409)},
)


def search_http_error(exc: SearchError) -> HTTPException:
    """Translate safe domain codes without catching unrelated route failures."""
    status = 404 if exc.code == SearchErrorCode.NOT_FOUND else 409
    return HTTPException(status, detail={"code": exc.code.value})


@router.get("")
@require_scope("table:read", "workspace:read")
async def get_table_search(
    table_id: TableID, role: WorkspaceActorRouteRole, session: AsyncDBSession
) -> TableSearchConfiguration:
    """Read settings and provider availability; requires table:read and workspace:read."""
    assert role.organization_id is not None and role.workspace_id is not None
    service = TableSearchService(
        session, SearchScope(role.organization_id, role.workspace_id)
    )
    try:
        await service.table(table_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, detail={"code": "NOT_FOUND"}) from exc
    # Provider resolution is a separate read-only transaction, never a source
    # mutation. Authorize the table before inspecting workspace availability.
    availability = None
    provider_error = False
    try:
        availability = await WorkspaceEmbeddingService(role).get()
    except EmbeddingError:
        provider_error = True
    try:
        return await service.configuration(
            table_id, availability=availability, provider_error=provider_error
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(404, detail={"code": "NOT_FOUND"}) from exc
    except SearchError as exc:
        raise search_http_error(exc) from exc


@router.patch(
    "/selection", responses={422: {"model": TableSearchSelectionErrorResponse}}
)
@require_scope("table:update")
async def select_table_search_column(
    table_id: TableID,
    params: TableSearchSelection,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> TableSearchConfiguration:
    """Persist selection and backfill marker together, even without a provider."""
    assert role.organization_id is not None and role.workspace_id is not None
    service = TableSearchService(
        session, SearchScope(role.organization_id, role.workspace_id)
    )
    try:
        await service.select_column(table_id, params)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, detail={"code": "NOT_FOUND"}) from exc
    except SearchError as exc:
        raise search_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "INVALID_SELECTION"}) from exc
    # These shared primitives never commit. Read the response while the selection
    # lock is held, then commit both selection and backfill intent together.
    try:
        result = await service.configuration(table_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, detail={"code": "NOT_FOUND"}) from exc
    except SearchError as exc:
        raise search_http_error(exc) from exc
    await session.commit()
    return result


@router.post("/retry", status_code=204)
@require_scope("table:update")
async def retry_table_search(
    table_id: TableID,
    params: TableSearchRetry,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> None:
    """Record retry intent for a bounded explicit list of failed documents."""
    assert role.organization_id is not None and role.workspace_id is not None
    service = TableSearchService(
        session, SearchScope(role.organization_id, role.workspace_id)
    )
    try:
        await service.retry(table_id, params.expected_generation, params.document_ids)
    except TracecatNotFoundError as exc:
        raise HTTPException(404, detail={"code": "NOT_FOUND"}) from exc
    except SearchError as exc:
        raise search_http_error(exc) from exc
    # Commit only after every requested document passes validation; a later
    # failure must roll back earlier retry changes in this same request.
    await session.commit()


@router.get("/documents", responses={400: {"model": TableSearchErrorResponse}})
@require_scope("table:read")
async def get_table_search_progress(
    table_id: TableID,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: Annotated[TableSearchProgressParams, Depends()],
) -> TableSearchProgressPage:
    """Read bounded per-document progress and safe retry references."""
    assert role.organization_id is not None and role.workspace_id is not None
    service = TableSearchService(
        session, SearchScope(role.organization_id, role.workspace_id)
    )
    try:
        return await service.progress(
            table_id,
            params=params,
        )
    except PaginationError:
        error = HTTPException(400, detail={"code": "INVALID_CURSOR"})
    except TracecatNotFoundError as exc:
        raise HTTPException(404, detail={"code": "NOT_FOUND"}) from exc
    except SearchError as exc:
        raise search_http_error(exc) from exc

    raise error
