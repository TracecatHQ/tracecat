"""HTTP wiring for agent model selection and workspace subset routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.agent.schemas import (
    DefaultModelSelection,
    DefaultModelSelectionUpdate,
    EnabledModelOperation,
    EnabledModelRuntimeConfigUpdate,
    EnabledModelsBatchOperation,
    ModelCatalogEntry,
    WorkspaceModelSubsetRead,
    WorkspaceModelSubsetUpdate,
)
from tracecat.agent.selections.service import AgentSelectionsService
from tracecat.auth.dependencies import (
    OrgUserOptionalWorkspaceRole,
    OrgUserRole,
    WorkspaceUserInPathRole,
)
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(tags=["agent"])


@router.get("/models")
@require_scope("agent:read")
async def list_models(
    *,
    role: OrgUserOptionalWorkspaceRole,
    session: AsyncDBSession,
    workspace_id: uuid.UUID | None = Query(
        default=None,
        description="Optional workspace filter for workspace-level enabled model subsets.",
    ),
) -> list[ModelCatalogEntry]:
    if workspace_id is not None and role.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
    service = AgentSelectionsService(session, role=role)
    return await service.list_models(workspace_id=workspace_id)


@router.get("/workspaces/{workspace_id}/model-subset")
@require_scope("workspace:read")
async def get_workspace_model_subset(
    *,
    role: WorkspaceUserInPathRole,
    workspace_id: uuid.UUID,
    session: AsyncDBSession,
) -> WorkspaceModelSubsetRead:
    service = AgentSelectionsService(session, role=role)
    return await service.get_workspace_model_subset(workspace_id)


@router.put("/workspaces/{workspace_id}/model-subset")
@require_scope("workspace:update")
async def replace_workspace_model_subset(
    *,
    role: WorkspaceUserInPathRole,
    workspace_id: uuid.UUID,
    params: WorkspaceModelSubsetUpdate,
    session: AsyncDBSession,
) -> WorkspaceModelSubsetRead:
    service = AgentSelectionsService(session, role=role)
    try:
        return await service.replace_workspace_model_subset(workspace_id, params)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.delete(
    "/workspaces/{workspace_id}/model-subset",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("workspace:update")
async def clear_workspace_model_subset(
    *,
    role: WorkspaceUserInPathRole,
    workspace_id: uuid.UUID,
    session: AsyncDBSession,
) -> None:
    service = AgentSelectionsService(session, role=role)
    try:
        await service.clear_workspace_model_subset(workspace_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/default-model")
@require_scope("agent:read")
async def get_default_model(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> DefaultModelSelection | None:
    service = AgentSelectionsService(session, role=role)
    return await service.get_default_model()


@router.put("/default-model")
@require_scope("agent:update")
async def set_default_model(
    *,
    params: DefaultModelSelectionUpdate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> DefaultModelSelection:
    service = AgentSelectionsService(session, role=role)
    try:
        return await service.set_default_model_selection(params)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected agent selection API error", action="set default model"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set default model.",
        ) from exc


@router.post("/models/enabled")
@require_scope("agent:update")
async def enable_model(
    *,
    params: EnabledModelOperation,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> ModelCatalogEntry:
    service = AgentSelectionsService(session, role=role)
    try:
        return await service.enable_model(params)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post("/models/enabled/batch")
@require_scope("agent:update")
async def enable_models(
    *,
    params: EnabledModelsBatchOperation,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[ModelCatalogEntry]:
    service = AgentSelectionsService(session, role=role)
    try:
        return await service.enable_models(params)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.delete("/models/enabled", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def disable_model(
    *,
    source_id: uuid.UUID | None = Query(default=None),
    model_provider: str = Query(...),
    model_name: str = Query(...),
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    service = AgentSelectionsService(session, role=role)
    await service.disable_model(
        EnabledModelOperation(
            source_id=source_id,
            model_provider=model_provider,
            model_name=model_name,
        )
    )


async def _disable_models(
    *,
    params: EnabledModelsBatchOperation,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    service = AgentSelectionsService(session, role=role)
    await service.disable_models(params)


@router.post("/models/disabled/batch", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def disable_models(
    *,
    params: EnabledModelsBatchOperation,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    await _disable_models(params=params, role=role, session=session)


@router.delete(
    "/models/enabled/batch",
    status_code=status.HTTP_204_NO_CONTENT,
    deprecated=True,
)
@require_scope("agent:update")
async def disable_models_legacy(
    *,
    params: EnabledModelsBatchOperation,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> None:
    await _disable_models(params=params, role=role, session=session)


@router.patch("/models/enabled")
@require_scope("agent:update")
async def update_enabled_model_config(
    *,
    params: EnabledModelRuntimeConfigUpdate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> ModelCatalogEntry:
    service = AgentSelectionsService(session, role=role)
    try:
        return await service.update_enabled_model_config(params)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
