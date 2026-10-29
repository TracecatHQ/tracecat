"""Lightweight organization lookups for processes outside the API."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import Organization
from tracecat.identifiers import OrganizationID


async def get_default_organization_id(session: AsyncSession) -> OrganizationID:
    """Get the default (first) organization ID.

    This is used by auth modules that need to read platform-level settings
    before user authentication provides an org context.

    Args:
        session: Database session.

    Returns:
        OrganizationID: The ID of the first organization.

    Raises:
        ValueError: If no organizations exist.
    """
    result = await session.execute(
        select(Organization).order_by(Organization.created_at).limit(1)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise ValueError("No organizations exist. Run bootstrap first.")
    return org.id
