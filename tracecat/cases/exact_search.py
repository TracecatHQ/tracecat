"""Automatic exact-reference promotion for the interactive case search."""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tracecat.db.models import Case


@dataclass(frozen=True, slots=True)
class ExactCaseMatch:
    """A workspace-authorized case and whether it falls outside the query."""

    id: uuid.UUID
    outside_filters: bool


async def find_exact_case_match(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    search_term: str | None,
    filters: Sequence[ColumnElement[bool]],
) -> ExactCaseMatch | None:
    """Recognize complete prefixed IDs/UUIDs; leave ordinary numbers as text.

    View filters determine the explanatory flag, never workspace authorization.
    The caller retains strict filtering for relationship and explicit-ID queries.
    """
    if not search_term:
        return None
    value = search_term.strip()
    if match := re.fullmatch(r"CASE-(\d+)", value, re.IGNORECASE):
        digits = match.group(1)
        if len(digits) > 10:
            raise ValueError("Short ID must match CASE-<number> or <number>")
        number = int(digits)
        if number > 2**31 - 1:
            raise ValueError("Short ID number cannot exceed 2147483647")
        identity = Case.case_number == number
    elif re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
        re.IGNORECASE,
    ):
        identity = Case.id == uuid.UUID(value)
    else:
        return None
    row = (
        await session.execute(
            sa.select(Case.id, sa.and_(*filters)).where(
                Case.workspace_id == workspace_id, identity
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return ExactCaseMatch(id=row[0], outside_filters=not bool(row[1]))
