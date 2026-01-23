"""Organization management endpoints for admin control plane."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat_ee.admin.organizations.schemas import (
    OrgCreate,
    OrgInviteRequest,
    OrgInviteResponse,
    OrgRead,
    OrgRegistryRepositoryRead,
    OrgRegistrySyncRequest,
    OrgRegistrySyncResponse,
    OrgRegistryVersionPromoteResponse,
    OrgRegistryVersionRead,
    OrgUpdate,
)
from tracecat_ee.admin.organizations.service import AdminOrgService

router = APIRouter(prefix="/organizations", tags=["admin:organizations"])


@router.get("", response_model=list[OrgRead])
async def list_organizations(
    role: SuperuserRole,
    session: AsyncDBSession,
) -> list[OrgRead]:
    """List all organizations."""
    service = AdminOrgService(session, role=role)
    return list(await service.list_organizations())


@router.post("", response_model=OrgRead, status_code=status.HTTP_201_CREATED)
async def create_organization(
    role: SuperuserRole,
    session: AsyncDBSession,
    params: OrgCreate,
) -> OrgRead:
    """Create a new organization."""
    service = AdminOrgService(session, role=role)
    try:
        return await service.create_organization(params)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.get("/{org_id}", response_model=OrgRead)
async def get_organization(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
) -> OrgRead:
    """Get organization by ID."""
    service = AdminOrgService(session, role=role)
    try:
        return await service.get_organization(org_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.patch("/{org_id}", response_model=OrgRead)
async def update_organization(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
    params: OrgUpdate,
) -> OrgRead:
    """Update organization."""
    service = AdminOrgService(session, role=role)
    try:
        return await service.update_organization(org_id, params)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
            ) from e
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
) -> None:
    """Delete organization."""
    service = AdminOrgService(session, role=role)
    try:
        await service.delete_organization(org_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


# Invitation Endpoints


@router.post(
    "/invitations",
    response_model=OrgInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_org_user(
    role: SuperuserRole,
    session: AsyncDBSession,
    params: OrgInviteRequest,
) -> OrgInviteResponse:
    """Invite a user to an organization.

    If the organization doesn't exist, creates it first.
    Optionally sends an invitation email with a magic link.
    """
    service = AdminOrgService(session, role=role)
    try:
        return await service.invite_org_user(params)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


# Org Registry Endpoints


@router.get(
    "/{org_id}/registry/repositories", response_model=list[OrgRegistryRepositoryRead]
)
async def list_org_repositories(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
) -> list[OrgRegistryRepositoryRead]:
    """List registry repositories for an organization."""
    service = AdminOrgService(session, role=role)
    try:
        return list(await service.list_org_repositories(org_id))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get(
    "/{org_id}/registry/repositories/{repository_id}/versions",
    response_model=list[OrgRegistryVersionRead],
)
async def list_org_repository_versions(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
    repository_id: uuid.UUID,
) -> list[OrgRegistryVersionRead]:
    """List versions for a specific repository in an organization."""
    service = AdminOrgService(session, role=role)
    try:
        return list(await service.list_org_repository_versions(org_id, repository_id))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post(
    "/{org_id}/registry/repositories/{repository_id}/sync",
    response_model=OrgRegistrySyncResponse,
)
async def sync_org_repository(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
    repository_id: uuid.UUID,
    params: OrgRegistrySyncRequest | None = None,
) -> OrgRegistrySyncResponse:
    """Sync a registry repository for an organization."""
    service = AdminOrgService(session, role=role)
    force = params.force if params else False
    try:
        return await service.sync_org_repository(org_id, repository_id, force=force)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post(
    "/{org_id}/registry/repositories/{repository_id}/versions/{version_id}/promote",
    response_model=OrgRegistryVersionPromoteResponse,
)
async def promote_org_repository_version(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
    repository_id: uuid.UUID,
    version_id: uuid.UUID,
) -> OrgRegistryVersionPromoteResponse:
    """Promote a registry version to be the current version for an org repository."""
    service = AdminOrgService(session, role=role)
    try:
        return await service.promote_org_repository_version(
            org_id, repository_id, version_id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
