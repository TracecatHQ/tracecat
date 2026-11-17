"""Capture schedule side effects for a caller-owned durable receipt."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

_KEY = "durable_schedule_changes"


@dataclass
class ScheduleChanges:
    """IDs to reconcile after the transaction commits, safe to replay."""

    created: list[uuid.UUID] = field(default_factory=list)
    deleted: list[uuid.UUID] = field(default_factory=list)

    @classmethod
    def of(cls, session: AsyncSession) -> ScheduleChanges | None:
        return cast(ScheduleChanges | None, session.info.get(_KEY))

    @classmethod
    @contextmanager
    def capture(cls, session: AsyncSession) -> Iterator[ScheduleChanges]:
        """Replace volatile callbacks with an explicit, serializable change set."""
        if cls.of(session) is not None:
            raise RuntimeError("Schedule capture is already active")
        changes = cls()
        session.info[_KEY] = changes
        try:
            yield changes
        finally:
            session.info.pop(_KEY)
