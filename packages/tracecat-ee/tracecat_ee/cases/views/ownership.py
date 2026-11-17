"""What happens to a user's case views when the user is deleted."""

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.enums import CaseViewVisibility
from tracecat.db.models import CaseView


async def delete_personal_case_views(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete a user's personal case views, before the user is deleted.

    Only its owner can see a personal view, so once the owner is gone nobody
    could read, change, or delete it. Shared views stay, and the foreign key
    sets their ``owner_id`` to null. Pins on the deleted views cascade.

    Callers delete users as a platform superuser, whose session bypasses
    tenant row-level security, so this reaches every workspace.
    """
    await session.execute(
        sa.delete(CaseView).where(
            CaseView.owner_id == user_id,
            CaseView.visibility == CaseViewVisibility.PERSONAL,
        )
    )
