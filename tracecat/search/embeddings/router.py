"""Workspace setup endpoints, separate from table selection and ranking routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, HTTPException

from tracecat.auth.dependencies import WorkspaceUserPathRole, require_workspace_id_path
from tracecat.auth.types import Role
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.schemas import (
    EmbeddingConfigurationDisable,
    EmbeddingConfigurationInput,
    EmbeddingConfigurationRead,
    EmbeddingConfigurationSave,
    EmbeddingErrorRead,
    EmbeddingErrorResponse,
    EmbeddingValidationRead,
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
        async with httpx.AsyncClient() as http:
            yield WorkspaceEmbeddingService(role, EmbeddingClient(http))
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
    """Read this workspace's settings and supported models without credential values."""
    async with _service(role) as service:
        return await service.get()


@router.post("/validate")
async def validate_embedding_configuration(
    params: EmbeddingConfigurationInput,
    role: WorkspaceUserPathRole,
) -> EmbeddingValidationRead:
    """Probe a proposed configuration with synthetic text without saving it."""
    async with _service(role) as service:
        return await service.validate(params)


@router.put("")
async def save_embedding_configuration(
    params: EmbeddingConfigurationSave,
    role: WorkspaceUserPathRole,
) -> EmbeddingConfigurationRead:
    """Probe and save with an expected-version check against concurrent edits."""
    async with _service(role) as service:
        return await service.save(params)


@router.post("/disable")
async def disable_embedding_configuration(
    params: EmbeddingConfigurationDisable,
    role: WorkspaceUserPathRole,
) -> EmbeddingConfigurationRead:
    """Disable search and invalidate its pointer while retaining cleanup records."""
    async with _service(role) as service:
        return await service.disable(params.expected_version)
