"""Automatic exact-reference promotion for the interactive case search."""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tracecat.db.models import Case

MAX_CASE_NUMBER = 2**31 - 1
SHORT_ID_PATTERN = re.compile(r"^(?:CASE-)?(\d{1,10})$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ExactCaseMatch:
    """A stable identity predicate and optional workspace-authorized match metadata."""

    id: uuid.UUID | None
    predicate: ColumnElement[bool]
    outside_filters: bool


def case_reference_predicate(search_term: str | None) -> ColumnElement[bool] | None:
    """Recognize a complete prefixed reference without a database lookup."""
    if not search_term:
        return None
    value = search_term.strip()
    if match := re.fullmatch(r"CASE-(\d+)", value, re.IGNORECASE):
        digits = match.group(1)
        if not SHORT_ID_PATTERN.fullmatch(value):
            raise ValueError("Short ID must match CASE-<number> or <number>")
        number = int(digits)
        if number > MAX_CASE_NUMBER:
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
    return identity


async def prepare_exact_case_match(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    search_term: str | None,
    filters: Sequence[ColumnElement[bool]],
) -> ExactCaseMatch | None:
    """Recognize complete prefixed IDs/UUIDs; leave ordinary numbers as text.

    View filters determine the explanatory flag, never workspace authorization.
    The caller retains strict filtering for relationship and explicit-ID queries.
    Recognized references keep their predicate when no case exists, so deleting
    an exact result cannot invalidate continuation cursors.
    """
    identity = case_reference_predicate(search_term)
    if identity is None:
        return None
    row = (
        await session.execute(
            sa.select(Case.id, sa.and_(*filters)).where(
                Case.workspace_id == workspace_id, identity
            )
        )
    ).one_or_none()
    return ExactCaseMatch(
        id=row[0] if row is not None else None,
        predicate=identity,
        outside_filters=row is not None and not bool(row[1]),
    )
