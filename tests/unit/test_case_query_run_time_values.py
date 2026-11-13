"""Relative time windows, "me", and creator filters on the case query contract."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from tracecat.cases.search_adapters import (
    CurrentUserRequiredError,
    legacy_case_filters,
    resolve_user_refs,
)
from tracecat.cases.search_schemas import (
    CaseQuery,
    RelativeTimeUnit,
    RelativeTimeWindow,
)
from tracecat.cases.search_service import relative_window_start
from tracecat.query.filters import Condition, FilterOp

NOW = datetime(2026, 3, 31, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("amount", "unit", "expected"),
    [
        (6, RelativeTimeUnit.HOUR, [0, 0, 0, 0, 6]),
        (30, RelativeTimeUnit.DAY, [0, 0, 0, 30, 0]),
        (2, RelativeTimeUnit.WEEK, [0, 0, 2, 0, 0]),
        (1, RelativeTimeUnit.MONTH, [0, 1, 0, 0, 0]),
    ],
)
def test_relative_window_resolves_in_sql_with_fixed_binds(
    amount: int, unit: RelativeTimeUnit, expected: list[int]
) -> None:
    """The window becomes now() - make_interval(...), so bind values stay the
    same on every page and the pagination cursor fingerprint matches."""
    expression = relative_window_start(RelativeTimeWindow(amount=amount, unit=unit))
    compiled = expression.compile(dialect=postgresql.dialect())

    assert "now() - make_interval(" in str(compiled)
    assert list(compiled.params.values()) == expected


@pytest.mark.parametrize("amount", [0, -1, 1001])
def test_relative_window_rejects_out_of_range_amounts(amount: int) -> None:
    with pytest.raises(ValidationError):
        RelativeTimeWindow(amount=amount, unit=RelativeTimeUnit.DAY)


@pytest.mark.parametrize(
    "payload",
    [
        {"created_within": {"amount": 1, "unit": "day"}, "start_time": NOW},
        {"created_within": {"amount": 1, "unit": "day"}, "end_time": NOW},
        {"updated_within": {"amount": 1, "unit": "day"}, "updated_after": NOW},
        {"updated_within": {"amount": 1, "unit": "day"}, "updated_before": NOW},
    ],
)
def test_query_rejects_window_combined_with_fixed_bounds(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        CaseQuery.model_validate(payload)


def test_query_accepts_me_for_assignee_and_creator() -> None:
    query = CaseQuery.model_validate(
        {"assignee_id": ["me", "unassigned"], "created_by": ["me"]}
    )

    assert query.assignee_id == ["me", "unassigned"]
    assert query.created_by == ["me"]


def test_resolve_user_refs_replaces_me_and_drops_sentinels() -> None:
    user_id = uuid.uuid4()
    other = uuid.uuid4()

    resolved = resolve_user_refs(["me", other, "unassigned", user_id], user_id=user_id)

    assert resolved == [user_id, other]


def test_resolve_user_refs_without_user_fails_with_typed_error() -> None:
    with pytest.raises(CurrentUserRequiredError) as exc_info:
        resolve_user_refs(["me"], user_id=None)

    assert exc_info.value.code == "current_user_required"


def test_resolve_user_refs_without_me_needs_no_user() -> None:
    other = uuid.uuid4()

    assert resolve_user_refs([other], user_id=None) == [other]


def test_creator_ids_become_a_created_by_condition() -> None:
    creator = uuid.uuid4()

    tree = legacy_case_filters(creator_ids=[creator, creator])

    assert tree == Condition(field="created_by", op=FilterOp.IN, value=[creator])
