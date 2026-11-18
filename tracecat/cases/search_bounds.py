"""Bounds for keyword searches that match most of a workspace.

Ranking costs a few microseconds per matching case, and an exact count reads
every match. A request with ``cap_broad_matches`` bounds both: relevance order
ranks only the SEARCH_MATCH_CAP newest matches, and status counts stop at
SEARCH_MATCH_CAP. See ``tracecat/cases/SEARCH.md`` ("Broad searches").
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from tracecat.cases.enums import CaseStatus
from tracecat.cases.schemas import CaseSearchAggregateRead, CaseStatusGroupCounts
from tracecat.cases.search_schemas import CaseSearchRequest
from tracecat.db.models import Case
from tracecat.pagination import read_cursor_anchor
from tracecat.query.execution import transaction_settings
from tracecat.query.lexical_types import SearchMode

# Keyword searches count, and rank by relevance, at most this many matches.
SEARCH_MATCH_CAP = 10_000
# How far back, in cases, the relevance cutoff reads in date order before it
# sorts the matches by date instead.
NEWEST_CASES_SCAN = 100_000

# Planner settings for a keyword statement with a LIMIT or OFFSET. PostgreSQL
# then favours plans that read the table, or ix_case_cursor_pagination, in
# order and stop early. Its row estimates for keyword predicates are
# unreliable, and for a keyword that matches less than it expects those plans
# read the whole workspace. Bitmap scans stay on, so matches are still found
# through the GIN and trigram indexes.
_TEXT_INDEX_ONLY: Mapping[str, str] = {
    "enable_indexscan": "off",
    "enable_seqscan": "off",
}


def ranks_newest_matches(request: CaseSearchRequest) -> bool:
    """Whether this request's relevance order is limited to the newest matches."""
    return (
        request.cap_broad_matches
        and request.search_mode == SearchMode.FULL_TEXT
        and request.order_by in (None, "relevance")
    )


async def relevance_window_start(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    request: CaseSearchRequest,
    filters: Sequence[ColumnElement[bool]],
) -> datetime | None:
    """The cutoff for a ranked page: cases created at or after it are ranked.

    The first page computes it. Its cursors carry it (``paginate``'s
    ``anchor``), and later pages read it back, so one search ranks the same
    window from page to page. None means every match is ranked.
    """
    if request.cursor is not None:
        anchor = read_cursor_anchor(request.cursor)
        return None if anchor is None else datetime.fromisoformat(anchor)
    return await _newest_matches_start(session, workspace_id, filters)


def window_anchor(start: datetime | None) -> str | None:
    """The cursor anchor that carries a window start to later pages."""
    return None if start is None else start.isoformat()


async def _newest_matches_start(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    filters: Sequence[ColumnElement[bool]],
) -> datetime | None:
    """Creation time of the SEARCH_MATCH_CAP-th newest match.

    Returns None when no more than SEARCH_MATCH_CAP cases match. Cases created
    at the returned instant are kept too.
    """
    # 1. Is the search broad? Read at most one match past the cap.
    async with transaction_settings(session, _TEXT_INDEX_ONLY):
        matches = (
            await session.execute(
                select(func.count()).select_from(_matches_up_to_cap(Case.id, filters))
            )
        ).scalar_one()
    if matches <= SEARCH_MATCH_CAP:
        return None

    # 2. A broad keyword usually matches a good share of recent cases. Read the
    # newest cases in date order and check each one. The LIMIT bounds the read
    # even when the matches are old, so this step reads the index in order on
    # purpose and runs without _TEXT_INDEX_ONLY.
    newest = (
        select(Case.created_at, sa.and_(*filters).label("matches"))
        .where(Case.workspace_id == workspace_id)
        .order_by(Case.created_at.desc())
        .limit(NEWEST_CASES_SCAN)
        .subquery()
    )
    cutoff = await session.scalar(
        select(newest.c.created_at)
        .where(newest.c.matches)
        .order_by(newest.c.created_at.desc())
        .offset(SEARCH_MATCH_CAP - 1)
        .limit(1)
    )
    if cutoff is not None:
        return cutoff

    # 3. Fewer than SEARCH_MATCH_CAP of the newest cases match: find the
    # matches through the text indexes and sort them by date. This reads every
    # match, at about a third of the cost of scoring them.
    async with transaction_settings(session, _TEXT_INDEX_ONLY):
        return await session.scalar(
            select(Case.created_at)
            .where(*filters)
            .order_by(Case.created_at.desc())
            .offset(SEARCH_MATCH_CAP - 1)
            .limit(1)
        )


async def capped_status_counts(
    session: AsyncSession, filters: Sequence[ColumnElement[bool]]
) -> CaseSearchAggregateRead:
    """Status counts that read at most one match past SEARCH_MATCH_CAP.

    Past the cap there are no per-status counts: an exact count per status
    would have to read every match, which is what the cap avoids.
    """
    matches = _matches_up_to_cap(Case.status, filters)
    async with transaction_settings(session, _TEXT_INDEX_ONLY):
        row = (
            await session.execute(select(*status_count_columns(matches.c.status)))
        ).one()
    if row.total > SEARCH_MATCH_CAP:
        return CaseSearchAggregateRead(
            total=SEARCH_MATCH_CAP, status_groups=None, capped=True
        )
    return aggregate_read(row)


def _matches_up_to_cap(
    column: InstrumentedAttribute[Any], filters: Sequence[ColumnElement[bool]]
) -> sa.Subquery:
    return select(column).where(*filters).limit(SEARCH_MATCH_CAP + 1).subquery()


def status_count_columns(
    status: ColumnElement[CaseStatus] | InstrumentedAttribute[CaseStatus],
) -> list[ColumnElement[int]]:
    """Total and per-status counts over rows exposing a case status."""
    return [
        func.count().label("total"),
        *(
            func.count().filter(status == value).label(value.value)
            for value in CaseStatus
        ),
    ]


def aggregate_read(row: sa.Row[Any]) -> CaseSearchAggregateRead:
    """Exact status counts from a row of ``status_count_columns``."""
    counts = row._mapping
    return CaseSearchAggregateRead(
        total=int(counts["total"]),
        status_groups=CaseStatusGroupCounts(
            **{value.value: int(counts[value.value]) for value in CaseStatus}
        ),
    )
