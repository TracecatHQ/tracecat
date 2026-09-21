"""SCIM connection token and group mapping administration API (EE)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import OrgActorRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.pagination import Page, PageParams
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.scim.connections import ScimConnectionService
from tracecat_ee.scim.schemas import (
    ExternalGroupMappingCreate,
    ExternalGroupMappingRead,
    ExternalGroupRead,
    ScimActivationRequest,
    ScimActivationReviewRead,
    ScimConnectionRead,
    ScimConnectionTokenRead,
)
from tracecat_ee.scim.service import SCIMService


async def _require_scim_entitlement(
    role: OrgActorRole,
    session: AsyncDBSession,
) -> None:
    """Router-level dependency gating SCIM connection management."""
    await check_entitlement(session, role, Entitlement.RBAC_ADDONS)


connections_router = APIRouter(
    prefix="/scim/connection",
    tags=["scim"],
    dependencies=[Depends(_require_scim_entitlement)],
)


@connections_router.post("", response_model=ScimConnectionTokenRead)
async def issue_scim_token(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
) -> ScimConnectionTokenRead:
    """Create or rotate the SCIM connection token.

    The raw token is returned only in this response.
    """
    service = ScimConnectionService(session, role=role)
    issued = await service.issue_token()
    return ScimConnectionTokenRead(
        connection=ScimConnectionRead.model_validate(issued.connection),
        token=issued.token,
    )


@connections_router.get("", response_model=ScimConnectionRead)
async def get_scim_connection(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
) -> ScimConnectionRead:
    """Read the SCIM connection status. Never returns the token."""
    service = ScimConnectionService(session, role=role)
    try:
        connection = await service.get_connection()
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="SCIM connection not found"
        ) from e
    return ScimConnectionRead.model_validate(connection)


@connections_router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_scim_token(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
) -> None:
    """Revoke the SCIM connection token."""
    service = ScimConnectionService(session, role=role)
    try:
        await service.revoke()
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="SCIM connection not found"
        ) from e


# Distinct from the /scim/v2 protocol surface: these are org-admin session
# routes, so is_scim_path leaves their errors in the normal envelope.
mappings_router = APIRouter(
    prefix="/scim",
    tags=["scim"],
    dependencies=[Depends(_require_scim_entitlement)],
)


@mappings_router.get("/external-groups", response_model=Page[ExternalGroupRead])
async def list_external_groups(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None, max_length=8192),
) -> Page[ExternalGroupRead]:
    """List synced IdP groups available as mapping sources."""
    return await SCIMService(session, role=role).list_external_groups(
        page=PageParams(limit=limit, cursor=cursor)
    )


@mappings_router.post("/activation/review", response_model=ScimActivationReviewRead)
async def review_scim_activation(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    params: ScimActivationRequest,
) -> ScimActivationReviewRead:
    """Report what the provider pushed and what activating would change.

    A read: the returned plan is not stored, so activation recomputes it.
    """
    try:
        return await SCIMService(session, role=role).review_activation(params.mappings)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@mappings_router.post("/activation", status_code=status.HTTP_204_NO_CONTENT)
async def activate_scim_connection(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    params: ScimActivationRequest,
) -> None:
    """Admit the pushed directory and install the reviewed mappings."""
    try:
        await SCIMService(session, role=role).activate(params.mappings)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@mappings_router.get("/mappings", response_model=Page[ExternalGroupMappingRead])
async def list_scim_mappings(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None, max_length=8192),
) -> Page[ExternalGroupMappingRead]:
    """List group mappings with both sides joined in."""
    return await SCIMService(session, role=role).list_mappings(
        page=PageParams(limit=limit, cursor=cursor)
    )


@mappings_router.post("/mappings", response_model=ExternalGroupMappingRead)
async def create_scim_mapping(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    params: ExternalGroupMappingCreate,
) -> ExternalGroupMappingRead:
    """Map an external group into a Tracecat group and reconcile immediately."""
    service = SCIMService(session, role=role)
    try:
        mapping = await service.create_mapping(
            external_group_id=params.external_group_id, group_id=params.group_id
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    await session.commit()
    return await service.get_mapping(mapping.id)


@mappings_router.delete(
    "/mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_scim_mapping(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    mapping_id: uuid.UUID,
) -> None:
    """Remove a mapping and revoke the membership only it supplied."""
    try:
        await SCIMService(session, role=role).delete_mapping(mapping_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await session.commit()
