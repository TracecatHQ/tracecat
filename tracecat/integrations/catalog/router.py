"""FastAPI routes for the consolidated integrations catalog."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.dependencies import WorkspaceUserRouteRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.integrations.catalog.schemas import (
    CatalogConnectionRead,
    CatalogIntegrationDetail,
    CatalogIntegrationRead,
    CatalogStaticKVConnectionCreate,
)
from tracecat.integrations.catalog.service import (
    ConnectionsService,
    IntegrationCatalogService,
)
from tracecat.integrations.enums import IntegrationSource

# Mounted under /integrations to keep the URL surface unified. Paths are
# disjoint from the legacy router endpoints, which use {provider_id}
# directly under /integrations.
catalog_router = APIRouter(prefix="/integrations", tags=["integrations-catalog"])


# ----------------------------------------------------------------------
# Catalog (Integration table)
# ----------------------------------------------------------------------


@catalog_router.get("/catalog", response_model=list[CatalogIntegrationRead])
async def list_catalog(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    source: IntegrationSource | None = Query(default=None),
    search: str | None = Query(default=None, max_length=128),
) -> list[CatalogIntegrationRead]:
    """List integrations visible to this workspace.

    Includes platform-shipped (``workspace_id IS NULL``) rows plus
    workspace-authored rows for the caller's workspace.
    """
    service = IntegrationCatalogService(session, role=role)
    rows = await service.list_integrations(
        source=source,
        search=search,
    )
    return [await service.to_read_schema(row) for row in rows]


@catalog_router.get(
    "/catalog/{integration_id}", response_model=CatalogIntegrationDetail
)
async def get_catalog_entry(
    integration_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
) -> CatalogIntegrationDetail:
    """Get an integration with its connections."""
    catalog = IntegrationCatalogService(session, role=role)
    connections_svc = ConnectionsService(session, role=role)

    try:
        integration = await catalog.get_integration(integration_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    conns = await connections_svc.list_connection_summaries(integration)
    auth_options = await catalog.auth_options_for(integration)

    return CatalogIntegrationDetail(
        id=integration.id,
        workspace_id=integration.workspace_id,
        namespace=integration.namespace,
        display_name=integration.display_name,
        description=integration.description,
        icon_url=integration.icon_url,
        source=integration.source,
        auth_options=auth_options,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
        connections=conns,
    )


# ----------------------------------------------------------------------
# Connections (per-integration)
# ----------------------------------------------------------------------


@catalog_router.get(
    "/catalog/{integration_id}/connections",
    response_model=list[CatalogConnectionRead],
)
async def list_connections(
    integration_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
) -> list[CatalogConnectionRead]:
    catalog = IntegrationCatalogService(session, role=role)
    connections = ConnectionsService(session, role=role)
    try:
        integration = await catalog.get_integration(integration_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return await connections.list_connection_summaries(integration)


@catalog_router.post(
    "/catalog/{integration_id}/connections",
    response_model=CatalogConnectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    integration_id: uuid.UUID,
    params: CatalogStaticKVConnectionCreate,
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
) -> CatalogConnectionRead:
    """Create a static key-value connection for registry credentials."""
    connections_svc = ConnectionsService(session, role=role)
    catalog_svc = IntegrationCatalogService(session, role=role)
    try:
        integration = await catalog_svc.get_integration(integration_id)
        await catalog_svc.validate_connection_create(integration, params)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    connection = await connections_svc.create_connection(integration, params)
    await session.commit()
    return connection


@catalog_router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_connection(
    connection_id: uuid.UUID,
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
) -> None:
    service = ConnectionsService(session, role=role)
    try:
        await service.delete_connection(connection_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    await session.commit()
