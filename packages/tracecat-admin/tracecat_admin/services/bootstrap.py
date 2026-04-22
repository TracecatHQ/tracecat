"""Bootstrap service for direct database operations.

This module provides direct database access for bootstrap scenarios,
such as creating the first superuser before any authenticated users exist.

Requires: tracecat package to be installed (tracecat-admin[bootstrap]).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserCreate, UserRole, UserUpdate
from tracecat.auth.users import (
    get_or_create_user,
    get_user_db_context,
    get_user_manager_context,
    lookup_user_by_email,
)
from tracecat.db.engine import (
    get_async_session_bypass_rls_context_manager,
    get_async_session_context_manager,
)
from tracecat.db.models import (
    Membership,
    OrganizationMembership,
    OrganizationTier,
    Tier,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.organization.management import ensure_default_organization
from tracecat.tiers import defaults as tier_defaults
from tracecat.tiers.enums import Entitlement
from tracecat.tiers.types import EntitlementsDict

ALL_ENTITLEMENTS = tuple(entitlement.value for entitlement in Entitlement)


@dataclass
class CreateSuperuserResult:
    """Result of create_superuser operation."""

    email: str
    user_id: str
    created: bool  # True if user was created, False if promoted existing


@dataclass
class CreateDevUserResult:
    """Result of create_dev_user operation."""

    email: str
    user_id: str
    superuser_email: str
    superuser_user_id: str
    superuser_created: bool
    organization_id: str
    workspace_id: str
    default_tier_id: str
    default_tier_entitlements: dict[str, bool]
    org_role: str
    workspace_role: str
    created: bool


def resolve_default_tier_entitlements(value: str | None) -> dict[str, bool]:
    """Resolve dev default-tier entitlement selection into a full bool mapping."""
    normalized = (value or "all").strip().lower().replace("-", "_")
    if normalized in {"", "all"}:
        return dict.fromkeys(ALL_ENTITLEMENTS, True)
    if normalized in {"none", "false", "off", "disabled"}:
        return dict.fromkeys(ALL_ENTITLEMENTS, False)

    requested = {
        item.strip().lower().replace("-", "_")
        for item in normalized.split(",")
        if item.strip()
    }
    unknown = requested - set(ALL_ENTITLEMENTS)
    if unknown:
        valid = ", ".join(ALL_ENTITLEMENTS)
        invalid = ", ".join(sorted(unknown))
        raise ValueError(
            f"Unknown entitlements: {invalid}. Valid entitlements: {valid}"
        )
    return {entitlement: entitlement in requested for entitlement in ALL_ENTITLEMENTS}


async def create_superuser(
    email: str,
    password: str | None = None,
    create: bool = False,
) -> CreateSuperuserResult:
    """Create or promote a user to superuser status.

    Args:
        email: User email address.
        password: Password for new user (required if create=True).
        create: If True, create a new user. If False, promote existing user.

    Returns:
        CreateSuperuserResult with user details.

    Raises:
        ValueError: If user not found (when create=False) or already superuser.
        ValueError: If password not provided when create=True.
    """
    async with get_async_session_context_manager() as session:
        if create:
            if not password:
                raise ValueError("Password is required when creating a new user")

            # Check if user already exists
            existing = await lookup_user_by_email(session=session, email=email)
            if existing:
                raise ValueError(f"User with email '{email}' already exists")

            # Create the user (role defaults to BASIC)
            user_create = UserCreate(
                email=email,
                password=password,
                is_superuser=True,
                is_verified=True,
            )
            user = await get_or_create_user(user_create, exist_ok=False)

            # Update user role to ADMIN (use admin_update to bypass ctx_role check)
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    user_update = UserUpdate(role=UserRole.ADMIN)
                    user = await user_manager.admin_update(user_update, user)

            return CreateSuperuserResult(
                email=user.email,
                user_id=str(user.id),
                created=True,
            )
        else:
            # Find existing user and promote
            user = await lookup_user_by_email(session=session, email=email)
            if not user:
                raise ValueError(f"User with email '{email}' not found")

            if user.is_superuser:
                raise ValueError(f"User '{email}' is already a superuser")

            # Promote to superuser
            user.is_superuser = True
            user.role = UserRole.ADMIN
            await session.commit()
            await session.refresh(user)

            return CreateSuperuserResult(
                email=user.email,
                user_id=str(user.id),
                created=False,
            )


async def _get_role_by_slug(
    *,
    session: AsyncSession,
    organization_id: UUID,
    slug: str,
) -> DBRole:
    result = await session.execute(
        select(DBRole).where(
            DBRole.organization_id == organization_id,
            DBRole.slug == slug,
        )
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise ValueError(
            f"Role '{slug}' was not found for organization '{organization_id}'"
        )
    return role


async def _get_default_workspace(
    *, session: AsyncSession, organization_id: UUID
) -> Workspace:
    result = await session.execute(
        select(Workspace)
        .where(Workspace.organization_id == organization_id)
        .order_by(Workspace.created_at.asc())
        .limit(1)
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise ValueError(f"No workspace found for organization '{organization_id}'")
    return workspace


async def _ensure_default_tier(
    *,
    session: AsyncSession,
    entitlements: dict[str, bool],
) -> Tier:
    result = await session.execute(
        select(Tier)
        .where(Tier.is_default.is_(True))
        .order_by(Tier.is_active.desc(), Tier.created_at.asc())
    )
    default_tiers = list(result.scalars().all())
    if default_tiers:
        tier = default_tiers[0]
    else:
        tier = Tier(
            display_name=tier_defaults.DEFAULT_TIER_DISPLAY_NAME,
            max_concurrent_workflows=None,
            max_action_executions_per_workflow=None,
            max_concurrent_actions=None,
            api_rate_limit=None,
            api_burst_capacity=None,
            entitlements=cast(EntitlementsDict, entitlements),
            is_default=True,
            sort_order=0,
            is_active=True,
        )
        session.add(tier)
        await session.flush()

    tier.display_name = tier_defaults.DEFAULT_TIER_DISPLAY_NAME
    tier.entitlements = cast(EntitlementsDict, entitlements)
    tier.is_default = True
    tier.is_active = True

    for extra_tier in default_tiers[1:]:
        extra_tier.is_default = False

    return tier


async def _ensure_org_tier(
    *,
    session: AsyncSession,
    organization_id: UUID,
    tier_id: UUID,
) -> None:
    result = await session.execute(
        select(OrganizationTier).where(
            OrganizationTier.organization_id == organization_id
        )
    )
    org_tier = result.scalar_one_or_none()
    if org_tier is None:
        session.add(
            OrganizationTier(
                organization_id=organization_id,
                tier_id=tier_id,
            )
        )
        return

    org_tier.tier_id = tier_id
    org_tier.entitlement_overrides = None


async def _hash_password(*, session: AsyncSession, password: str) -> str:
    async with get_user_db_context(session) as user_db:
        async with get_user_manager_context(user_db) as user_manager:
            return user_manager.password_helper.hash(password)


async def _ensure_platform_superuser(
    *,
    session: AsyncSession,
    email: str,
    password: str,
) -> tuple[User, bool]:
    hashed_password = await _hash_password(session=session, password=password)
    existing = await lookup_user_by_email(session=session, email=email)
    if existing is not None:
        existing.hashed_password = hashed_password
        existing.is_active = True
        existing.is_verified = True
        existing.is_superuser = True
        existing.role = UserRole.ADMIN
        return existing, False

    user = User(
        email=email,
        hashed_password=hashed_password,
        role=UserRole.ADMIN,
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user, True


async def _get_or_create_local_user(
    *,
    session: AsyncSession,
    email: str,
    password: str,
) -> tuple[User, bool]:
    hashed_password = await _hash_password(session=session, password=password)
    existing = await lookup_user_by_email(session=session, email=email)
    if existing is not None:
        if existing.is_superuser:
            raise ValueError(
                f"User '{email}' already exists as a superuser; choose a non-superuser dev email"
            )
        existing.hashed_password = hashed_password
        existing.is_active = True
        existing.is_verified = True
        existing.role = UserRole.BASIC
        return existing, False

    user = User(
        email=email,
        hashed_password=hashed_password,
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user, True


async def _ensure_org_membership(
    *,
    session: AsyncSession,
    user_id: UUID,
    organization_id: UUID,
) -> None:
    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )
    if result.scalar_one_or_none() is None:
        session.add(
            OrganizationMembership(
                user_id=user_id,
                organization_id=organization_id,
            )
        )


async def _ensure_workspace_membership(
    *,
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
) -> None:
    result = await session.execute(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id == workspace_id,
        )
    )
    if result.scalar_one_or_none() is None:
        session.add(
            Membership(
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )


async def _ensure_role_assignment(
    *,
    session: AsyncSession,
    user_id: UUID,
    organization_id: UUID,
    workspace_id: UUID | None,
    role_id: UUID,
) -> None:
    statement = select(UserRoleAssignment).where(
        UserRoleAssignment.user_id == user_id,
    )
    if workspace_id is None:
        statement = statement.where(UserRoleAssignment.workspace_id.is_(None))
    else:
        statement = statement.where(UserRoleAssignment.workspace_id == workspace_id)

    result = await session.execute(statement)
    assignment = result.scalar_one_or_none()
    if assignment is None:
        session.add(
            UserRoleAssignment(
                organization_id=organization_id,
                user_id=user_id,
                workspace_id=workspace_id,
                role_id=role_id,
            )
        )
        return

    assignment.organization_id = organization_id
    assignment.role_id = role_id


async def create_dev_user(
    *,
    email: str,
    password: str,
    superuser_email: str = "test@tracecat.com",
    superuser_password: str = "password1234",
    default_tier_entitlements: str = "all",
    org_role: str = "organization-owner",
    workspace_role: str = "workspace-admin",
) -> CreateDevUserResult:
    """Create local development SU and tenant user accounts.

    This bypasses public first-user registration on purpose. It is intended for
    dev clusters where platform superusers cannot enter tenant context.
    """
    if email.lower() == superuser_email.lower():
        raise ValueError("Dev user email must be different from superuser email")

    organization_id = await ensure_default_organization()
    entitlements = resolve_default_tier_entitlements(default_tier_entitlements)

    async with get_async_session_bypass_rls_context_manager() as session:
        default_tier = await _ensure_default_tier(
            session=session,
            entitlements=entitlements,
        )
        await _ensure_org_tier(
            session=session,
            organization_id=organization_id,
            tier_id=default_tier.id,
        )
        superuser, superuser_created = await _ensure_platform_superuser(
            session=session,
            email=superuser_email,
            password=superuser_password,
        )
        user, created = await _get_or_create_local_user(
            session=session,
            email=email,
            password=password,
        )
        workspace = await _get_default_workspace(
            session=session,
            organization_id=organization_id,
        )
        org_role_obj = await _get_role_by_slug(
            session=session,
            organization_id=organization_id,
            slug=org_role,
        )
        workspace_role_obj = await _get_role_by_slug(
            session=session,
            organization_id=organization_id,
            slug=workspace_role,
        )

        await _ensure_org_membership(
            session=session,
            user_id=user.id,
            organization_id=organization_id,
        )
        await _ensure_workspace_membership(
            session=session,
            user_id=user.id,
            workspace_id=workspace.id,
        )
        await _ensure_role_assignment(
            session=session,
            user_id=user.id,
            organization_id=organization_id,
            workspace_id=None,
            role_id=org_role_obj.id,
        )
        await _ensure_role_assignment(
            session=session,
            user_id=user.id,
            organization_id=organization_id,
            workspace_id=workspace.id,
            role_id=workspace_role_obj.id,
        )

        await session.commit()

        return CreateDevUserResult(
            email=user.email,
            user_id=str(user.id),
            superuser_email=superuser.email,
            superuser_user_id=str(superuser.id),
            superuser_created=superuser_created,
            organization_id=str(organization_id),
            workspace_id=str(workspace.id),
            default_tier_id=str(default_tier.id),
            default_tier_entitlements=entitlements,
            org_role=org_role,
            workspace_role=workspace_role,
            created=created,
        )
