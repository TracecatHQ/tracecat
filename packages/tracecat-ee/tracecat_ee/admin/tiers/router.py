"""Admin tier management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.tiers.exceptions import (
    CannotDeleteDefaultTierError,
    OrganizationNotFoundError,
    TierInUseError,
    TierNotFoundError,
)
from tracecat.tiers.schemas import (
    OrganizationTierRead,
    OrganizationTierUpdate,
    TierCreate,
    TierRead,
    TierUpdate,
)
from tracecat_ee.admin.tiers.service import AdminTierService

router = APIRouter(prefix="/tiers", tags=["admin:tiers"])


# Tier CRUD endpoints


@router.get("", response_model=list[TierRead])
async def list_tiers(
    role: SuperuserRole,
    session: AsyncDBSession,
    include_inactive: bool = Query(
        False, description="Include inactive tiers in results"
    ),
) -> list[TierRead]:
    """List all tiers."""
    service = AdminTierService(session, role=role)
    return list(await service.list_tiers(include_inactive=include_inactive))


@router.post("", response_model=TierRead, status_code=status.HTTP_201_CREATED)
async def create_tier(
    role: SuperuserRole,
    session: AsyncDBSession,
    params: TierCreate,
) -> TierRead:
    """Create a new tier."""
    service = AdminTierService(session, role=role)
    try:
        return await service.create_tier(params)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.get("/by-slug/{slug}", response_model=TierRead)
async def get_tier_by_slug(
    role: SuperuserRole,
    session: AsyncDBSession,
    slug: str,
) -> TierRead:
    """Get tier by slug."""
    service = AdminTierService(session, role=role)
    try:
        return await service.get_tier_by_slug(slug)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/{tier_id}", response_model=TierRead)
async def get_tier(
    role: SuperuserRole,
    session: AsyncDBSession,
    tier_id: uuid.UUID,
) -> TierRead:
    """Get tier by ID."""
    service = AdminTierService(session, role=role)
    try:
        return await service.get_tier(tier_id)
    except TierNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.patch("/{tier_id}", response_model=TierRead)
async def update_tier(
    role: SuperuserRole,
    session: AsyncDBSession,
    tier_id: uuid.UUID,
    params: TierUpdate,
) -> TierRead:
    """Update a tier."""
    service = AdminTierService(session, role=role)
    try:
        return await service.update_tier(tier_id, params)
    except TierNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete("/{tier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tier(
    role: SuperuserRole,
    session: AsyncDBSession,
    tier_id: uuid.UUID,
) -> None:
    """Delete a tier (only if no orgs are assigned to it)."""
    service = AdminTierService(session, role=role)
    try:
        await service.delete_tier(tier_id)
    except TierNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (CannotDeleteDefaultTierError, TierInUseError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


# Organization tier endpoints


@router.get("/organizations/{org_id}", response_model=OrganizationTierRead)
async def get_org_tier(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
) -> OrganizationTierRead:
    """Get tier assignment for an organization."""
    service = AdminTierService(session, role=role)
    try:
        return await service.get_org_tier(org_id)
    except (OrganizationNotFoundError, TierNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.patch("/organizations/{org_id}", response_model=OrganizationTierRead)
async def update_org_tier(
    role: SuperuserRole,
    session: AsyncDBSession,
    org_id: uuid.UUID,
    params: OrganizationTierUpdate,
) -> OrganizationTierRead:
    """Update organization's tier assignment and overrides."""
    service = AdminTierService(session, role=role)
    try:
        return await service.update_org_tier(org_id, params)
    except (OrganizationNotFoundError, TierNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
