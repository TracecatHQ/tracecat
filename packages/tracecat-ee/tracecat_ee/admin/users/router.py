"""User management endpoints for admin control plane."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi_users import InvalidPasswordException

from tracecat.audit.service import AuditService
from tracecat.auth.credentials import SuperuserRole, SuperuserStepUpRole
from tracecat.auth.mfa.service import MfaService
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.db.models import User
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

from .schemas import AdminUserCreate, AdminUserRead
from .service import AdminUserService

router = APIRouter(prefix="/users", tags=["admin:users"])


@router.post("", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
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
    session: AsyncDBSessionBypass,
) -> list[AdminUserRead]:
    """List all users."""
    service = AdminUserService(session, role)
    return list(await service.list_users())


@router.get("/{user_id}", response_model=AdminUserRead)
async def get_user(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
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
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
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
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
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
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    user_id: uuid.UUID,
) -> None:
    """Delete a platform user globally, clearing sessions and dependencies first."""
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete user without authenticated user context",
        )
    service = AdminUserService(session, role)
    try:
        await service.delete_user(user_id, current_user_id=role.user_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.post("/{user_id}/mfa/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_mfa(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    user_id: uuid.UUID,
) -> None:
    """Remove a user's security keys and recovery codes after they lose access.

    If a policy still requires MFA, the user must register a new key at their
    next sign-in.
    """
    if await session.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    removed = await MfaService(session).reset_user_mfa(user_id)
    logger.info(
        "Platform admin reset user MFA",
        actor_id=str(role.user_id),
        user_id=str(user_id),
        credentials_removed=removed,
    )
    try:
        async with AuditService.with_session(
            role=role, audit_sink="platform"
        ) as audit_svc:
            await audit_svc.create_event(
                resource_type="security_key",
                action="reset",
                resource_id=user_id,
                include_actor_label=False,
            )
    except Exception as exc:
        logger.warning("Failed to emit MFA reset audit event", error=str(exc))
