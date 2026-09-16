"""Organization membership admission.

The membership row is the aggregate root for a user's presence in an
organization; children hang off it by composite foreign key.
"""

from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import OrganizationMembership


async def ensure_member(
    session: AsyncSession,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    """Admit a user to an organization, idempotently.

    The membership row is the aggregate root: children hang off it by composite
    foreign key, so it must exist before any assignment or group-member insert.

    Args:
        session: Database session to execute the upsert on.
        organization_id: Organization the user is admitted to.
        user_id: User being admitted.
    """
    await session.execute(
        pg_insert(OrganizationMembership)
        .values(organization_id=organization_id, user_id=user_id)
        .on_conflict_do_nothing(
            index_elements=[
                OrganizationMembership.organization_id,
                OrganizationMembership.user_id,
            ]
        )
    )
