import uuid

from fastapi import APIRouter, HTTPException, status

from tracecat.agent.schemas import (
    AgentModelSourceCreate,
    AgentModelSourceRead,
    AgentModelSourceUpdate,
    ModelCatalogEntry,
)
from tracecat.agent.sources.service import AgentSourceService
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(prefix="/sources", tags=["agent"])


@router.get("")
@require_scope("agent:read")
async def list_sources(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[AgentModelSourceRead]:
    service = AgentSourceService(session, role=role)
    return await service.list_model_sources()


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("agent:update")
async def create_source(
    *,
    params: AgentModelSourceCreate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AgentModelSourceRead:
    service = AgentSourceService(session, role=role)
    try:
        return await service.create_model_source(params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception("Unexpected agent source API error", action="create source")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create source.",
        ) from e


@router.patch("/{source_id}")
@require_scope("agent:update")
async def update_source(
    *,
    source_id: uuid.UUID,
    params: AgentModelSourceUpdate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AgentModelSourceRead:
    service = AgentSourceService(session, role=role)
    try:
        return await service.update_model_source(source_id=source_id, params=params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        logger.exception("Unexpected agent source API error", action="update source")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update source.",
        ) from e


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def delete_source(
    *,
    source_id: uuid.UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    service = AgentSourceService(session, role=role)
    try:
        await service.delete_model_source(source_id=source_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/{source_id}/refresh")
@require_scope("agent:update")
async def refresh_source(
    *,
    source_id: uuid.UUID,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[ModelCatalogEntry]:
    service = AgentSourceService(session, role=role)
    try:
        return await service.refresh_model_source(source_id=source_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        logger.exception("Unexpected agent source API error", action="refresh source")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to refresh source.",
        ) from e
