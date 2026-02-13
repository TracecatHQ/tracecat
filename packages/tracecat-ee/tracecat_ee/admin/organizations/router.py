"""Organization management endpoints for admin control plane."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatValidationError
from tracecat_ee.admin.organizations.schemas import (
    OrgCreate,
    OrgDomainCreate,
    OrgDomainRead,
    OrgDomainUpdate,
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


def _require_multi_tenant() -> None:
    if config.TRACECAT__EE_MULTI_TENANT:
        return
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail="Multi-tenant organization management is not enabled.",
    )


@router.get("", response_model=list[OrgRead])
async def list_organizations(
    role: SuperuserRole,
    session: AsyncDBSession,
) -> list[OrgRead]:
    """List all organizations."""
    service = AdminOrgService(session, role)
    return list(await service.list_organizations())


@router.post("", response_model=OrgRead, status_code=status.HTTP_201_CREATED)
async def create_organization(
    role: SuperuserRole,
    session: AsyncDBSession,
    params: OrgCreate,
) -> OrgRead:
    """Create a new organization."""
    _require_multi_tenant()
    service = AdminOrgService(session, role)
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
    service = AdminOrgService(session, role)
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
    service = AdminOrgService(session, role)
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
    confirm: str | None = Query(
        default=None,
        description="Must exactly match the organization name.",
    ),
) -> None:
    """Delete organization."""
    _require_multi_tenant()
    service = AdminOrgService(session, role)
    try:
        await service.delete_organization(org_id, confirmation=confirm)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


# Org Domain Endpoints


@router.get("/{org_id}/domains", response_model=list[OrgDomainRead])
async def list_organization_domains(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
) -> list[OrgDomainRead]:
    """List all assigned domains for an organization."""
    service = AdminOrgService(session, role)
    try:
        return list(await service.list_org_domains(org_id))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post(
    "/{org_id}/domains",
    response_model=OrgDomainRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization_domain(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
    params: OrgDomainCreate,
) -> OrgDomainRead:
    """Create a new assigned domain for an organization."""
    service = AdminOrgService(session, role)
    try:
        return await service.create_org_domain(org_id, params)
    except ValueError as e:
        detail = str(e)
        detail_lower = detail.lower()
        if "not found" in detail_lower:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=detail
            ) from e
        if "already assigned" in detail_lower:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e


@router.patch("/{org_id}/domains/{domain_id}", response_model=OrgDomainRead)
async def update_organization_domain(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
    domain_id: uuid.UUID,
    params: OrgDomainUpdate,
) -> OrgDomainRead:
    """Update active/primary state for an assigned organization domain."""
    service = AdminOrgService(session, role)
    try:
        return await service.update_org_domain(org_id, domain_id, params)
    except ValueError as e:
        detail = str(e)
        if "not found" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=detail
            ) from e
        if "already assigned" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e


@router.delete("/{org_id}/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization_domain(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
    domain_id: uuid.UUID,
) -> None:
    """Delete an assigned organization domain."""
    service = AdminOrgService(session, role)
    try:
        await service.delete_org_domain(org_id, domain_id)
    except ValueError as e:
        detail = str(e)
        if "not found" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e


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
    service = AdminOrgService(session, role)
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
    service = AdminOrgService(session, role)
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
    service = AdminOrgService(session, role)
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
    service = AdminOrgService(session, role)
    try:
        return await service.promote_org_repository_version(
            org_id, repository_id, version_id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
