"""Organization management utilities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import UUID4
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.seeding import seed_system_roles_for_org
from tracecat.cases.service import CaseFieldsService
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    Membership,
    Organization,
    OrganizationMembership,
    OrganizationSecret,
    Ownership,
    RegistryAction,
    RegistryIndex,
    RegistryRepository,
    RegistryVersion,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.settings.service import SettingsService
from tracecat.tiers.exceptions import DefaultTierNotConfiguredError
from tracecat.tiers.service import TierService
from tracecat.workflow.schedules.service import WorkflowSchedulesService
from tracecat.workspaces.service import WorkspaceService


@dataclass(frozen=True)
class SingleTenantUserDefaultsResult:
    """Result of ensuring single-tenant user organization defaults."""

    organization_id: OrganizationID | None
    changed: bool


async def ensure_organization_defaults(
    session: AsyncSession,
    org_id: OrganizationID,
) -> None:
    """Ensure an organization has default settings, workspace, and tier.

    This is idempotent - safe to call multiple times on the same org.

    Args:
        session: Database session.
        org_id: Organization ID to ensure defaults for.
    """
    org_role = Role(
        type="service",
        service_id="tracecat-service",
        organization_id=org_id,
        is_platform_superuser=True,
        scopes=frozenset({"*"}),
    )

    # Ensure settings exist (idempotent)
    settings_service = SettingsService(session, role=org_role)
    await settings_service.init_default_settings()

    # Ensure at least one workspace exists
    workspace_count_result = await session.execute(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.organization_id == org_id)
    )
    if workspace_count_result.scalar_one() == 0:
        logger.info("Creating default workspace", organization_id=str(org_id))
        workspace_service = WorkspaceService(session, role=org_role)
        await workspace_service.create_workspace(name="Default Workspace")

    # Ensure system roles exist for this org (idempotent)
    await seed_system_roles_for_org(session, org_id)
    await session.commit()

    # Ensure org tier exists (assigns default tier if configured)
    tier_service = TierService(session)
    try:
        await tier_service.get_or_create_org_tier(org_id)
    except DefaultTierNotConfiguredError:
        # No default tier configured - skip tier assignment
        logger.debug(
            "No default tier configured, skipping tier assignment",
            organization_id=str(org_id),
        )


async def create_organization(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    org_id: UUID4 | None = None,
) -> Organization:
    """Create an organization record.

    Args:
        session: Database session.
        name: Organization name.
        slug: Organization slug (must be unique).
        org_id: Optional UUID for the organization.

    Returns:
        The created Organization.

    Raises:
        ValueError: If an organization with the given slug already exists.
    """
    org = Organization(
        id=org_id or uuid.uuid4(),
        name=name,
        slug=slug,
        is_active=True,
    )
    session.add(org)

    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise ValueError(f"Organization with slug '{slug}' already exists") from e

    return org


async def create_organization_with_defaults(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    org_id: UUID4 | None = None,
) -> Organization:
    """Create a new organization with default settings and workspace.

    This is the canonical way to create an organization.

    Args:
        session: Database session.
        name: Organization name.
        slug: Organization slug (must be unique).
        org_id: Optional UUID for the organization.

    Returns:
        The created Organization.

    Raises:
        ValueError: If an organization with the given slug already exists.
    """
    org = await create_organization(session, name=name, slug=slug, org_id=org_id)
    await ensure_organization_defaults(session, org.id)
    await session.refresh(org)
    return org


def validate_organization_delete_confirmation(
    organization: Organization,
    *,
    confirmation: str | None,
) -> None:
    """Validate the operator confirmation text for destructive org deletion."""
    if confirmation is None or confirmation.strip() != organization.name:
        raise TracecatValidationError(
            "Confirmation text must exactly match the organization name."
        )


async def delete_organization_with_cleanup(
    session: AsyncSession,
    *,
    organization: Organization,
    operator_user_id: uuid.UUID | None,
) -> None:
    """Delete an organization after cleaning up dependent resources.

    This handles explicit cleanup for resources guarded by RESTRICT organization FKs
    and runs workspace teardown logic that isn't represented by FK cascades.
    """
    result = await session.execute(
        select(Workspace).where(Workspace.organization_id == organization.id)
    )
    workspaces = list(result.scalars().all())
    workspace_ids = [workspace.id for workspace in workspaces]

    for workspace in workspaces:
        bootstrap_role = Role(
            type="service",
            user_id=operator_user_id,
            service_id="tracecat-service",
            organization_id=organization.id,
            workspace_id=workspace.id,
        )
        schedule_service = WorkflowSchedulesService(
            session=session, role=bootstrap_role
        )
        for schedule in await schedule_service.list_schedules():
            await schedule_service.delete_schedule(schedule.id, commit=False)

        case_fields_service = CaseFieldsService(session=session, role=bootstrap_role)
        await case_fields_service.drop_workspace_schema()

        await session.execute(
            delete(Membership).where(Membership.workspace_id == workspace.id)
        )
        await session.delete(workspace)

    if workspace_ids:
        workspace_resource_ids = [str(workspace_id) for workspace_id in workspace_ids]
        await session.execute(
            delete(Ownership).where(Ownership.resource_id.in_(workspace_resource_ids))
        )

    await session.execute(
        delete(Ownership).where(Ownership.owner_id == organization.id)
    )
    await session.execute(
        delete(OrganizationSecret).where(
            OrganizationSecret.organization_id == organization.id
        )
    )
    await session.execute(
        delete(RegistryIndex).where(RegistryIndex.organization_id == organization.id)
    )
    await session.execute(
        delete(RegistryVersion).where(
            RegistryVersion.organization_id == organization.id
        )
    )
    await session.execute(
        delete(RegistryAction).where(RegistryAction.organization_id == organization.id)
    )
    await session.execute(
        delete(RegistryRepository).where(
            RegistryRepository.organization_id == organization.id
        )
    )

    await session.delete(organization)
    await session.flush()


async def get_default_organization_id(session: AsyncSession) -> OrganizationID:
    """Get the ID of the first/default organization.

    Args:
        session: Database session.

    Returns:
        OrganizationID: The ID of the first organization.

    Raises:
        NoResultFound: If no organization exists.
    """
    result = await session.execute(
        select(Organization).order_by(Organization.created_at.asc()).limit(1)
    )
    return result.scalar_one().id


async def ensure_default_organization() -> OrganizationID:
    """Ensure a default organization exists with settings and workspace.

    Returns:
        OrganizationID: The ID of the default (or first) organization.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        # Check if any organization exists
        count_result = await session.execute(
            select(func.count()).select_from(Organization)
        )

        if count_result.scalar_one() == 0:
            org = await create_organization_with_defaults(
                session,
                name="Default Organization",
                slug="default",
            )
            logger.info(
                "Created default organization",
                organization_id=str(org.id),
                name=org.name,
            )
            return org.id

        # Get existing org and ensure it has defaults
        result = await session.execute(
            select(Organization).order_by(Organization.created_at.asc()).limit(1)
        )
        org = result.scalar_one()
        logger.debug(
            "Using existing organization",
            organization_id=str(org.id),
            name=org.name,
        )
        await ensure_organization_defaults(session, org.id)
        return org.id


async def ensure_single_tenant_user_defaults(
    *,
    user_id: uuid.UUID,
    is_superuser: bool,
) -> OrganizationID | None:
    """Ensure single-tenant users are real members of the default organization.

    In multi-tenant deployments this is a no-op. In single-tenant deployments,
    every user receives default organization membership. Superusers receive the
    organization-owner role; regular users receive organization-member unless
    they already have an org-wide assignment.
    """
    if config.TRACECAT__EE_MULTI_TENANT:
        return None

    organization_id = await ensure_default_organization()
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await ensure_single_tenant_user_defaults_for_session(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            is_superuser=is_superuser,
        )
        await session.commit()
    return result.organization_id


async def ensure_single_tenant_user_defaults_for_session(
    *,
    session: AsyncSession,
    user_id: uuid.UUID,
    is_superuser: bool,
    organization_id: OrganizationID | None = None,
) -> SingleTenantUserDefaultsResult:
    """Ensure single-tenant user defaults in a caller-owned session.

    This checks tenant mode, resolves or creates the default organization, and
    applies membership/RBAC without committing the caller's session.
    """
    if config.TRACECAT__EE_MULTI_TENANT:
        return SingleTenantUserDefaultsResult(organization_id=None, changed=False)

    if organization_id is None:
        try:
            organization_id = await get_default_organization_id(session)
        except NoResultFound:
            organization_id = await ensure_default_organization()

    changed = await ensure_single_tenant_user_defaults_in_session(
        session=session,
        user_id=user_id,
        organization_id=organization_id,
        is_superuser=is_superuser,
    )
    return SingleTenantUserDefaultsResult(
        organization_id=organization_id,
        changed=changed,
    )


async def ensure_single_tenant_user_defaults_in_session(
    *,
    session: AsyncSession,
    user_id: uuid.UUID,
    organization_id: OrganizationID,
    is_superuser: bool,
) -> bool:
    """Ensure single-tenant org membership and org-wide RBAC in a session."""
    # Fast path: if the user already has default-org membership and an
    # acceptable org-wide role, there is nothing to repair.
    membership_result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )
    membership = membership_result.scalar_one_or_none()

    assignment_result = await session.execute(
        select(UserRoleAssignment, DBRole.slug)
        .join(DBRole, UserRoleAssignment.role_id == DBRole.id)
        .where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.workspace_id.is_(None),
        )
    )
    assignment_row = assignment_result.tuples().one_or_none()
    if membership is not None and assignment_row is not None:
        _, current_role_slug = assignment_row
        if not is_superuser or current_role_slug == "organization-owner":
            return False

    # Role lookup below depends on the preset roles existing. This is idempotent
    # and only runs after the fast path determines a repair may be needed.
    await seed_system_roles_for_org(session, organization_id)

    changed = False
    if membership is None:
        # Auth-path lazy repair can run concurrently for the same legacy user.
        # Use an idempotent insert so one request repairs the row and the other
        # continues without surfacing a unique-constraint failure.
        membership_insert = pg_insert(OrganizationMembership).values(
            user_id=user_id,
            organization_id=organization_id,
        )
        membership_insert = membership_insert.on_conflict_do_nothing(
            index_elements=[
                OrganizationMembership.user_id,
                OrganizationMembership.organization_id,
            ]
        )
        membership_result = await session.execute(membership_insert)
        changed = (membership_result.rowcount or 0) > 0  # pyright: ignore[reportAttributeAccessIssue]

    # Single-tenant defaults are intentionally minimal for regular users, while
    # superusers are granted default-org owner permissions.
    role_slug = "organization-owner" if is_superuser else "organization-member"
    role_result = await session.execute(
        select(DBRole).where(
            DBRole.organization_id == organization_id,
            DBRole.slug == role_slug,
        )
    )
    role = role_result.scalar_one()

    assignment = assignment_row[0] if assignment_row is not None else None
    if assignment is None:
        # There can only be one org-wide assignment per user. For superusers, a
        # conflict means another request created or found a stale org-wide
        # assignment, so upgrade it to owner. Regular users do not update on
        # insert conflict; existing rows observed by this session are handled by
        # the normalization branch below.
        assignment_insert = pg_insert(UserRoleAssignment).values(
            organization_id=organization_id,
            user_id=user_id,
            workspace_id=None,
            role_id=role.id,
        )
        conflict_target = {
            "index_elements": [UserRoleAssignment.user_id],
            "index_where": UserRoleAssignment.workspace_id.is_(None),
        }
        if is_superuser:
            assignment_insert = assignment_insert.on_conflict_do_update(
                **conflict_target,
                set_={
                    "organization_id": organization_id,
                    "role_id": role.id,
                },
            )
        else:
            assignment_insert = assignment_insert.on_conflict_do_nothing(
                **conflict_target
            )
        assignment_result = await session.execute(assignment_insert)
        changed = changed or (assignment_result.rowcount or 0) > 0  # pyright: ignore[reportAttributeAccessIssue]
        return changed

    # At this point a repair is needed: either the membership row was missing,
    # or a superuser still had a non-owner org-wide role. Keep the org-wide
    # assignment aligned with the default role for this user type, but skip
    # no-op writes.
    if assignment.role_id != role.id:
        assignment.role_id = role.id
        changed = True
    return changed
