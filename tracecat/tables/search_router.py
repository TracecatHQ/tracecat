"""Public table selection, status and retry; query routes belong to search."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import TableID
from tracecat.search.embeddings.service import WorkspaceEmbeddingService
from tracecat.search.embeddings.types import EmbeddingError
from tracecat.search.types import SearchError, SearchErrorCode, SearchScope
from tracecat.tables.search import TableSearchService
from tracecat.tables.search_schemas import (
    TableSearchConfiguration,
    TableSearchErrorResponse,
    TableSearchProgressPage,
    TableSearchRetry,
    TableSearchSelection,
)

router = APIRouter(
    prefix="/{table_id}/search",
    tags=["tables"],
    responses={code: {"model": TableSearchErrorResponse} for code in (404, 409)},
)


@asynccontextmanager
async def search_errors() -> AsyncIterator[None]:
    """Map only typed, safe failures to the public API."""
    try:
        yield
        return
    except SearchError as exc:
        status = 404 if exc.code == SearchErrorCode.NOT_FOUND else 409
        error = HTTPException(status, detail={"code": exc.code.value})
    except TracecatNotFoundError:
        error = HTTPException(404, detail={"code": "NOT_FOUND"})
    except ValueError:
        error = HTTPException(422, detail={"code": "INVALID_SELECTION"})
    raise error


def table_search(
    session: AsyncDBSession, role: WorkspaceActorRouteRole
) -> TableSearchService:
    assert role.organization_id is not None and role.workspace_id is not None
    return TableSearchService(
        session, SearchScope(role.organization_id, role.workspace_id)
    )


@router.get("")
@require_scope("table:read")
async def get_table_search(
    table_id: TableID, role: WorkspaceActorRouteRole, session: AsyncDBSession
) -> TableSearchConfiguration:
    """Read settings and current provider availability without saving credentials."""
    async with search_errors():
        service = table_search(session, role)
        await service.table(table_id)
        # Provider resolution is a separate read-only transaction, never a source
        # mutation. Authorize the table before inspecting workspace availability.
        try:
            availability = await WorkspaceEmbeddingService(role).get()
        except EmbeddingError:
            return await service.configuration(table_id, provider_error=True)
        return await service.configuration(table_id, availability=availability)
    raise AssertionError("unreachable")


@router.patch("/selection")
@require_scope("table:update")
async def select_table_search_column(
    table_id: TableID,
    params: TableSearchSelection,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> TableSearchConfiguration:
    """Persist selection and backfill marker together, even without a provider."""
    async with search_errors():
        service = table_search(session, role)
        await service.select_column(table_id, params)
        result = await service.configuration(table_id)
        await session.commit()
        return result
    raise AssertionError("unreachable")


@router.post("/retry", status_code=204)
@require_scope("table:update")
async def retry_table_search(
    table_id: TableID,
    params: TableSearchRetry,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> None:
    """Record retry intent for a bounded explicit list of failed documents."""
    async with search_errors():
        service = table_search(session, role)
        await service.retry(table_id, params.expected_generation, params.document_ids)
        await session.commit()


@router.get("/documents")
@require_scope("table:read")
async def get_table_search_progress(
    table_id: TableID,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    generation: int = Query(ge=1),
    cursor: UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> TableSearchProgressPage:
    """Read bounded per-document progress and safe retry references."""
    async with search_errors():
        return await table_search(session, role).progress(
            table_id,
            generation=generation,
            cursor=cursor,
            limit=limit,
        )
    raise AssertionError("unreachable")
