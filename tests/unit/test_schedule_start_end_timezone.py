"""Tests for timezone-aware Schedule.start_at and Schedule.end_at."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import Schedule, Workspace

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_schedule_start_end_are_timezone_aware(session: AsyncSession) -> None:
    """Persist and reload Schedule.start_at / end_at with timezone info."""
    # Create workspace in the same session to ensure it's visible
    workspace = Workspace(
        id=uuid.uuid4(),
        name="test-schedule-workspace",
        organization_id=uuid.uuid4(),
    )
    session.add(workspace)
    await session.flush()

    start_at = datetime(2025, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
    end_at = datetime(2025, 1, 1, 13, 0, tzinfo=timezone(timedelta(hours=2)))

    schedule = Schedule(
        workspace_id=workspace.id,
        workflow_id=None,
        start_at=start_at,
        end_at=end_at,
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)

    assert schedule.start_at is not None
    assert schedule.end_at is not None
    assert schedule.start_at.tzinfo is not None
    assert schedule.end_at.tzinfo is not None

    # The database may normalize to UTC; compare in UTC.
    assert schedule.start_at.astimezone(UTC) == start_at.astimezone(UTC)
    assert schedule.end_at.astimezone(UTC) == end_at.astimezone(UTC)
