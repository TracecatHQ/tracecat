"""Workspace availability endpoint, separate from table selection and ranking routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException

from tracecat.auth.dependencies import WorkspaceUserPathRole, require_workspace_id_path
from tracecat.auth.types import Role
from tracecat.search.embeddings.schemas import (
    EmbeddingConfigurationRead,
    EmbeddingErrorRead,
    EmbeddingErrorResponse,
)
from tracecat.search.embeddings.service import WorkspaceEmbeddingService
from tracecat.search.embeddings.types import EmbeddingError, EmbeddingErrorCode
from tracecat.search.types import SearchError

router = APIRouter(
    prefix="/workspaces/{workspace_id}/search/configuration",
    tags=["search"],
    responses={
        code: {"model": EmbeddingErrorResponse} for code in (400, 409, 429, 502, 504)
    },
    dependencies=[Depends(require_workspace_id_path)],
)


@asynccontextmanager
async def _service(role: Role) -> AsyncIterator[WorkspaceEmbeddingService]:
    try:
        yield WorkspaceEmbeddingService(role)
    except EmbeddingError as exc:
        match exc.code:
            case EmbeddingErrorCode.CONFIGURATION_CHANGED:
                status = 409
            case EmbeddingErrorCode.RATE_LIMITED:
                status = 429
            case EmbeddingErrorCode.TIMEOUT:
                status = 504
            case EmbeddingErrorCode.UNAVAILABLE | EmbeddingErrorCode.RESPONSE_INVALID:
                status = 502
            case _:
                status = 400
        error = HTTPException(
            status,
            detail=EmbeddingErrorRead(
                code=exc.code,
                retryable=exc.retryable,
                retry_after=exc.retry_after,
            ).model_dump(),
        )
    except SearchError:
        error = HTTPException(404, detail="Workspace unavailable")
    else:
        return
    raise error


@router.get("")
async def get_embedding_configuration(
    role: WorkspaceUserPathRole,
) -> EmbeddingConfigurationRead:
    """Read automatic embedding availability without credential metadata."""
    async with _service(role) as service:
        return await service.get()
