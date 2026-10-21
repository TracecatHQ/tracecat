"""Workspace availability endpoint, separate from table selection and ranking routes."""

from fastapi import APIRouter, Depends, HTTPException

from tracecat.auth.dependencies import WorkspaceUserPathRole, require_workspace_id_path
from tracecat.db.dependencies import AsyncDBSession
from tracecat.search.embeddings.schemas import (
    EmbeddingConfigurationRead,
    EmbeddingErrorRead,
    EmbeddingErrorResponse,
)
from tracecat.search.embeddings.service import WorkspaceEmbeddingService
from tracecat.search.embeddings.types import EmbeddingError, EmbeddingErrorCode
from tracecat.search.types import SearchError
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement


async def require_semantic_search_entitlement(
    role: WorkspaceUserPathRole, session: AsyncDBSession
) -> None:
    await check_entitlement(session, role, Entitlement.SEMANTIC_SEARCH)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/search/configuration",
    tags=["search"],
    responses={
        code: {"model": EmbeddingErrorResponse} for code in (400, 409, 429, 502, 504)
    },
    dependencies=[
        Depends(require_workspace_id_path),
        Depends(require_semantic_search_entitlement),
    ],
)


@router.get("")
async def get_embedding_configuration(
    role: WorkspaceUserPathRole,
) -> EmbeddingConfigurationRead:
    """Read automatic embedding availability without credential metadata."""
    try:
        return await WorkspaceEmbeddingService(role).get()
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
    raise error
