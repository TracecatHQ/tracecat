"""Capture workspace backfills for a caller-owned durable receipt."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

_KEY = "durable_case_duration_backfills"


@dataclass
class CaseDurationBackfills:
    """Workspaces whose current duration definitions must be materialized."""

    workspaces: list[uuid.UUID] = field(default_factory=list)

    @classmethod
    def of(cls, session: AsyncSession) -> "CaseDurationBackfills | None":
        return cast(CaseDurationBackfills | None, session.info.get(_KEY))

    @classmethod
    @contextmanager
    def capture(cls, session: AsyncSession) -> Iterator["CaseDurationBackfills"]:
        """Collect backfill requests instead of registering volatile callbacks."""
        if cls.of(session) is not None:
            raise RuntimeError("Case duration backfill capture is already active")
        backfills = cls()
        session.info[_KEY] = backfills
        try:
            yield backfills
        finally:
            session.info.pop(_KEY)
