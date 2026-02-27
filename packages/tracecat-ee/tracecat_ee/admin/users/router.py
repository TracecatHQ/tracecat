"""User management endpoints for admin control plane."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi_users import InvalidPasswordException

from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSession

from .schemas import AdminUserCreate, AdminUserRead
from .service import AdminUserService

router = APIRouter(prefix="/users", tags=["admin:users"])


@router.post("", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    role: SuperuserRole,
    session: AsyncDBSession,
    params: AdminUserCreate,
) -> AdminUserRead:
    """Create a platform-level user without org membership."""
    service = AdminUserService(session, role)
    try:
        return await service.create_user(params)
    except InvalidPasswordException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except ValueError as e:
        detail = str(e)
        if "already exists" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=detail
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from e


@router.get("", response_model=list[AdminUserRead])
async def list_users(
    role: SuperuserRole,
    session: AsyncDBSession,
) -> list[AdminUserRead]:
    """List all users."""
    service = AdminUserService(session, role)
    return list(await service.list_users())


@router.get("/{user_id}", response_model=AdminUserRead)
async def get_user(
    role: SuperuserRole,
    session: AsyncDBSession,
    user_id: uuid.UUID,
) -> AdminUserRead:
    """Get user by ID."""
    service = AdminUserService(session, role)
    try:
        return await service.get_user(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/{user_id}/promote", response_model=AdminUserRead)
async def promote_to_superuser(
    role: SuperuserRole,
    session: AsyncDBSession,
    user_id: uuid.UUID,
) -> AdminUserRead:
    """Promote a user to superuser status."""
    service = AdminUserService(session, role)
    try:
        return await service.promote_superuser(user_id)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.post("/{user_id}/demote", response_model=AdminUserRead)
async def demote_from_superuser(
    role: SuperuserRole,
    session: AsyncDBSession,
    user_id: uuid.UUID,
) -> AdminUserRead:
    """Remove superuser status from a user."""
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot demote user without authenticated user context",
        )
    service = AdminUserService(session, role)
    try:
        return await service.demote_superuser(user_id, current_user_id=role.user_id)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    role: SuperuserRole,
    session: AsyncDBSession,
    user_id: uuid.UUID,
) -> None:
    """Delete a platform user."""
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete user without authenticated user context",
        )

    service = AdminUserService(session, role)
    try:
        await service.delete_user(user_id, current_user_id=role.user_id)
    except ValueError as e:
        detail = str(e)
        if "not found" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=detail,
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from e
