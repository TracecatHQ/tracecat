"""Organization management utilities."""

from __future__ import annotations

import uuid

from pydantic import UUID4
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Organization
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.settings.service import SettingsService
from tracecat.workspaces.service import WorkspaceService


async def create_organization_with_defaults(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    org_id: UUID4 | None = None,
) -> Organization:
    """Create a new organization with default settings and workspace.

    This is the canonical way to create an organization. It:
    1. Creates the Organization record
    2. Initializes default settings for the organization
    3. Creates a default workspace

    Args:
        session: Database session.
        name: Organization name.
        slug: Organization slug (must be unique).
        org_id: Optional UUID for the organization. If not provided, a random UUID is generated.

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

    # Create a service role for the new organization
    org_role = Role(
        type="service",
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-service",
        organization_id=org.id,
    )

    # Initialize default settings for the organization
    settings_service = SettingsService(session, role=org_role)
    await settings_service.init_default_settings()

    # Create a default workspace for the new organization
    workspace_service = WorkspaceService(session, role=org_role)
    await workspace_service.create_workspace(name="Default Workspace")

    await session.refresh(org)
    return org


async def get_default_organization_id(session: AsyncSession) -> OrganizationID:
    """Get the ID of the first/default organization.

    Args:
        session: Database session.

    Returns:
        OrganizationID: The ID of the first organization.

    Raises:
        NoResultFound: If no organization exists.
    """
    result = await session.execute(select(Organization).limit(1))
    org = result.scalar_one()
    return org.id


async def ensure_default_organization() -> OrganizationID:
    """Ensure a default organization exists with settings and workspace.

    If no organizations exist, creates one with default settings and workspace.
    If organizations already exist, returns the first one's ID.

    Returns:
        OrganizationID: The ID of the default (or first) organization.
    """
    async with get_async_session_context_manager() as session:
        # Check if any organization exists
        count_result = await session.execute(
            select(func.count()).select_from(Organization)
        )
        org_count = count_result.scalar_one()

        if org_count == 0:
            # Create a default organization with settings and workspace
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

        # Return the first organization's ID
        result = await session.execute(select(Organization).limit(1))
        org = result.scalar_one()
        logger.debug(
            "Using existing organization",
            organization_id=str(org.id),
            name=org.name,
        )
        return org.id
