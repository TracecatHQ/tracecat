"""Tests for the shared keyset pagination page builder.

These exercise the contract that every cursor-paginated service relies on:
a reverse scan runs in the inverted sort order, and the page it produces must
be handed back to the client in forward (display) semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest
import sqlalchemy as sa

from tracecat.pagination import (
    BaseCursorPaginator,
    CursorData,
    InvalidCursorError,
    build_cursor_page,
    keyset_filter,
    take_cursor_page,
    validate_cursor_sort_column,
)


@dataclass(frozen=True)
class Row:
    """A row in a list sorted newest-first by ``rank``."""

    rank: int

    @property
    def cursor(self) -> str:
        return f"cursor-{self.rank}"


# Display order is descending by rank: rank 0 is the first row of page 1.
ROWS = [Row(rank=i) for i in range(100)]


def scan(*, limit: int, cursor: str | None, reverse: bool) -> list[Row]:
    """Mimic a keyset scan: filter past the cursor, order, over-fetch by one.

    Forward scans walk down the display order; reverse scans walk back up it in
    the inverted order, which is what puts the rows adjacent to the cursor
    inside the LIMIT.
    """
    anchor = None if cursor is None else int(cursor.removeprefix("cursor-"))
    if reverse:
        rows = [row for row in ROWS if anchor is not None and row.rank < anchor]
        rows.sort(key=lambda row: row.rank, reverse=True)
    else:
        rows = [row for row in ROWS if anchor is None or row.rank > anchor]
        rows.sort(key=lambda row: row.rank)
    return rows[: limit + 1]


def page(*, limit: int, cursor: str | None = None, reverse: bool = False):
    rows, has_more = take_cursor_page(
        scan(limit=limit, cursor=cursor, reverse=reverse), limit=limit
    )
    return build_cursor_page(
        rows,
        cursor=cursor,
        reverse=reverse,
        has_more=has_more,
        encode_cursor=lambda row: row.cursor,
    )


def ranks(rows: list[Row]) -> list[int]:
    return [row.rank for row in rows]


def test_take_cursor_page_trims_the_extra_row() -> None:
    rows, has_more = take_cursor_page(ROWS[:11], limit=10)

    assert ranks(rows) == list(range(10))
    assert has_more is True


def test_take_cursor_page_reports_no_more_when_short() -> None:
    rows, has_more = take_cursor_page(ROWS[:3], limit=10)

    assert ranks(rows) == [0, 1, 2]
    assert has_more is False


def test_first_page_has_no_previous() -> None:
    first = page(limit=20)

    assert ranks(first.items) == list(range(20))
    assert first.has_more is True
    assert first.has_previous is False
    assert first.prev_cursor is None
    assert first.next_cursor == "cursor-19"


def test_forward_page_exposes_both_cursors() -> None:
    second = page(limit=20, cursor="cursor-19")

    assert ranks(second.items) == list(range(20, 40))
    assert second.has_more is True
    assert second.has_previous is True
    assert second.next_cursor == "cursor-39"
    assert second.prev_cursor == "cursor-20"


def test_reverse_page_returns_the_preceding_page_in_display_order() -> None:
    # Regression: the reverse branch used to return page 1 from any page, since
    # it selected everything before the cursor and then kept the top `limit`.
    third = page(limit=20, cursor="cursor-39")
    assert ranks(third.items) == list(range(40, 60))

    back = page(limit=20, cursor=third.prev_cursor, reverse=True)

    assert ranks(back.items) == list(range(20, 40))
    assert back.has_more is True
    assert back.has_previous is True


def test_reverse_page_cursors_round_trip() -> None:
    third = page(limit=20, cursor="cursor-39")
    back = page(limit=20, cursor=third.prev_cursor, reverse=True)

    # Stepping back again must reach page 1, not repeat page 2.
    assert back.prev_cursor == "cursor-20"
    further_back = page(limit=20, cursor=back.prev_cursor, reverse=True)
    assert ranks(further_back.items) == list(range(20))

    # And stepping forward from the reverse page must return page 3.
    assert back.next_cursor == "cursor-39"
    forward = page(limit=20, cursor=back.next_cursor)
    assert ranks(forward.items) == ranks(third.items)


def test_reverse_page_onto_the_first_page_reports_no_previous() -> None:
    second = page(limit=20, cursor="cursor-19")
    back = page(limit=20, cursor=second.prev_cursor, reverse=True)

    assert ranks(back.items) == list(range(20))
    # Nothing precedes page 1, but page 2 is still ahead of it.
    assert back.has_previous is False
    assert back.prev_cursor is None
    assert back.has_more is True
    assert back.next_cursor == "cursor-19"


def test_empty_page_has_no_cursors() -> None:
    empty = build_cursor_page(
        [],
        cursor="cursor-99",
        reverse=True,
        has_more=False,
        encode_cursor=lambda row: "unreachable",
    )

    assert empty.items == []
    assert empty.next_cursor is None
    assert empty.prev_cursor is None
    assert empty.has_more is False
    assert empty.has_previous is False


def test_validate_cursor_sort_column_returns_the_sort_value() -> None:
    cursor = CursorData(id="abc", sort_column="created_at", sort_value="2026-01-01")

    assert validate_cursor_sort_column(cursor, sort_column="created_at") == (
        datetime(2026, 1, 1)
    )


def test_validate_cursor_sort_column_rejects_a_different_sort() -> None:
    """A cursor from another sort must 400, not silently rewind to page 1."""
    cursor = CursorData(id="abc", sort_column="created_at", sort_value="2026-01-01")

    with pytest.raises(InvalidCursorError, match="sorts by 'priority'"):
        validate_cursor_sort_column(cursor, sort_column="priority")


def test_validate_cursor_sort_column_rejects_a_sortless_cursor() -> None:
    """Cursors predating sort-aware pagination carry no sort column."""
    cursor = CursorData(id="abc")

    with pytest.raises(InvalidCursorError):
        validate_cursor_sort_column(cursor, sort_column="created_at")


def test_validate_cursor_sort_column_rejects_a_missing_sort_value() -> None:
    cursor = CursorData(id="abc", sort_column="created_at")

    with pytest.raises(InvalidCursorError, match="missing a sort value"):
        validate_cursor_sort_column(cursor, sort_column="created_at")


def test_validate_cursor_sort_column_rejects_a_wrongly_typed_sort_value() -> None:
    """Enum and task-count sorts order by a rank, so their cursors carry ints."""
    cursor = CursorData(id="abc", sort_column="priority", sort_value="high")

    with pytest.raises(InvalidCursorError, match="wrong type"):
        validate_cursor_sort_column(cursor, sort_column="priority", expected_type=int)


def test_validate_cursor_sort_column_accepts_an_explicit_null_when_allowed() -> None:
    """A nullable sort column can legitimately anchor a page on a NULL value."""
    cursor = BaseCursorPaginator.decode_cursor(
        BaseCursorPaginator.encode_cursor("abc", sort_column="score", sort_value=None)
    )

    assert cursor.has_sort_value is True
    assert (
        validate_cursor_sort_column(cursor, sort_column="score", allow_null=True)
        is None
    )


def test_validate_cursor_sort_column_rejects_a_null_by_default() -> None:
    """Sorts on NOT NULL columns keep treating a NULL anchor as unusable."""
    cursor = BaseCursorPaginator.decode_cursor(
        BaseCursorPaginator.encode_cursor(
            "abc", sort_column="created_at", sort_value=None
        )
    )

    with pytest.raises(InvalidCursorError, match="missing a sort value"):
        validate_cursor_sort_column(cursor, sort_column="created_at")


def test_validate_cursor_sort_column_rejects_an_omitted_sort_value_when_null_allowed() -> (
    None
):
    """An absent field is a legacy cursor, not a row whose sort value is NULL."""
    cursor = CursorData.model_validate({"id": "abc", "sort_column": "score"})

    assert cursor.has_sort_value is False
    with pytest.raises(InvalidCursorError, match="missing a sort value"):
        validate_cursor_sort_column(cursor, sort_column="score", allow_null=True)


def test_invalid_cursor_error_is_a_value_error() -> None:
    """Routers map ValueError to 400; the subclass keeps that mapping."""
    assert issubclass(InvalidCursorError, ValueError)


# Keyset filtering over a nullable sort column, exercised on in-memory SQLite.
# NULL placement is pinned with explicit NULLS FIRST/LAST, so the ordering
# matches PostgreSQL's defaults and the predicate is checked against real rows
# rather than a rendered SQL string.
_KEYSET_TABLE = sa.Table(
    "keyset_rows",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("score", sa.Integer, nullable=True),
)
# Ids paired with a nullable sort value, deliberately interleaving NULLs with
# ties so both the tie-breaker and the NULL/non-NULL transition are crossed.
_KEYSET_ROWS = [
    (1, None),
    (2, 10),
    (3, None),
    (4, 5),
    (5, 10),
    (6, None),
    (7, 1),
    (8, 5),
]
# (score ASC NULLS LAST, id ASC). The descending display order is its reverse.
_ASCENDING_IDS = [7, 4, 8, 2, 5, 1, 3, 6]


@pytest.fixture
def keyset_conn():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _KEYSET_TABLE.create(conn)
        conn.execute(
            _KEYSET_TABLE.insert(),
            [{"id": id_, "score": score} for id_, score in _KEYSET_ROWS],
        )
    with engine.connect() as conn:
        yield conn
    engine.dispose()


def _keyset_scan(conn, *, cursor: str | None, ascending: bool, limit: int):
    """Run one over-fetched keyset scan, mirroring what the services build."""
    stmt = sa.select(_KEYSET_TABLE.c.id, _KEYSET_TABLE.c.score)
    if cursor is not None:
        cursor_data = BaseCursorPaginator.decode_cursor(cursor)
        stmt = stmt.where(
            keyset_filter(
                _KEYSET_TABLE.c.score,
                _KEYSET_TABLE.c.id,
                sort_value=validate_cursor_sort_column(
                    cursor_data, sort_column="score", allow_null=True
                ),
                id_value=int(cursor_data.id),
                ascending=ascending,
            )
        )
    sort_order = (
        _KEYSET_TABLE.c.score.asc().nulls_last()
        if ascending
        else _KEYSET_TABLE.c.score.desc().nulls_first()
    )
    id_order = _KEYSET_TABLE.c.id.asc() if ascending else _KEYSET_TABLE.c.id.desc()
    return list(conn.execute(stmt.order_by(sort_order, id_order).limit(limit + 1)))


def _keyset_page(
    conn, *, cursor: str | None, ascending: bool, limit: int, reverse: bool = False
):
    """One page in display order, following the cursor contract end to end."""
    scanned = _keyset_scan(
        conn, cursor=cursor, ascending=ascending != reverse, limit=limit
    )
    rows, has_more = take_cursor_page(scanned, limit=limit)
    return build_cursor_page(
        rows,
        cursor=cursor,
        reverse=reverse,
        has_more=has_more,
        encode_cursor=lambda row: BaseCursorPaginator.encode_cursor(
            row.id, sort_column="score", sort_value=row.score
        ),
    )


@pytest.mark.parametrize("ascending", [True, False])
def test_keyset_filter_walks_every_row_across_the_null_boundary(
    keyset_conn, ascending: bool
) -> None:
    """Regression: a page anchored on a NULL sort value used to be unreachable.

    Nullable columns let a page boundary land inside the NULL block, so the
    server issues a cursor whose sort value is NULL. Following it must resume
    inside that block rather than reject the cursor or restart the scan.
    """
    expected = _ASCENDING_IDS if ascending else list(reversed(_ASCENDING_IDS))

    seen: list[int] = []
    cursor: str | None = None
    while True:
        page = _keyset_page(keyset_conn, cursor=cursor, ascending=ascending, limit=2)
        seen.extend(row.id for row in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor

    assert seen == expected


@pytest.mark.parametrize("ascending", [True, False])
def test_keyset_filter_reverses_across_the_null_boundary(
    keyset_conn, ascending: bool
) -> None:
    """Paging back from a NULL-adjacent page must land on the page before it."""
    expected = _ASCENDING_IDS if ascending else list(reversed(_ASCENDING_IDS))

    page_1 = _keyset_page(keyset_conn, cursor=None, ascending=ascending, limit=3)
    page_2 = _keyset_page(
        keyset_conn, cursor=page_1.next_cursor, ascending=ascending, limit=3
    )
    assert [row.id for row in page_2.items] == expected[3:6]

    back = _keyset_page(
        keyset_conn,
        cursor=page_2.prev_cursor,
        ascending=ascending,
        limit=3,
        reverse=True,
    )

    assert [row.id for row in back.items] == expected[:3]


@pytest.mark.parametrize("ascending", [True, False])
def test_keyset_filter_omits_null_branches_for_not_null_columns(
    ascending: bool,
) -> None:
    """NOT NULL sorts keep the tight two-branch predicate they had before."""
    predicate = keyset_filter(
        _KEYSET_TABLE.c.score,
        _KEYSET_TABLE.c.id,
        sort_value=5,
        id_value=4,
        ascending=ascending,
        nullable=False,
    )

    assert "NULL" not in str(predicate.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize("reverse", [False, True])
def test_build_cursor_page_does_not_mutate_the_input(reverse: bool) -> None:
    rows = list(ROWS[:5])
    original = list(rows)

    build_cursor_page(
        rows,
        cursor="cursor-9",
        reverse=reverse,
        has_more=True,
        encode_cursor=lambda row: row.cursor,
    )

    assert rows == original
