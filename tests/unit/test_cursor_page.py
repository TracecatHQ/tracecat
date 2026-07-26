"""Tests for the shared keyset pagination page builder.

These exercise the contract that every cursor-paginated service relies on:
a reverse scan runs in the inverted sort order, and the page it produces must
be handed back to the client in forward (display) semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from tracecat.pagination import (
    CursorData,
    InvalidCursorError,
    build_cursor_page,
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


def test_invalid_cursor_error_is_a_value_error() -> None:
    """Routers map ValueError to 400; the subclass keeps that mapping."""
    assert issubclass(InvalidCursorError, ValueError)


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
