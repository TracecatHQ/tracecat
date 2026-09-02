from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from tracecat.query.filters import (
    MAX_FILTER_CONDITIONS,
    AndClause,
    Condition,
    Filter,
    FilterOp,
)

FILTER_ADAPTER = TypeAdapter(Filter)


def _condition(index: int = 0) -> dict[str, Any]:
    return {"field": f"field_{index}", "op": "eq", "value": "value"}


def _nested_not(depth: int) -> dict[str, Any]:
    node = _condition()
    for _ in range(depth - 1):
        node = {"not": node}
    return node


def test_filter_aliases_round_trip() -> None:
    payload = {
        "and": [
            {"field": "status", "op": "eq", "value": "open"},
            {
                "or": [
                    {"field": "priority", "op": "in", "value": ["high"]},
                    {
                        "not": {
                            "field": "summary",
                            "op": "contains",
                            "value": "test",
                        }
                    },
                ]
            },
        ]
    }

    parsed = FILTER_ADAPTER.validate_python(payload)

    assert isinstance(parsed, AndClause)
    assert parsed.model_dump(mode="json") == payload
    assert FILTER_ADAPTER.validate_python(parsed.model_dump()) == parsed


def test_filter_alias_accepts_python_field_name() -> None:
    parsed = AndClause.model_validate(
        {"and_": [Condition(field="status", op=FilterOp.EQ, value="open")]}
    )

    assert parsed.model_dump(mode="json") == {
        "and": [{"field": "status", "op": "eq", "value": "open"}]
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "field": "status",
            "op": "eq",
            "value": "open",
            "and": [_condition()],
        },
        {"and": []},
        {"not": [_condition()]},
        {"field": "status", "op": "eq", "value": "open", "unexpected": True},
        {},
    ],
)
def test_filter_rejects_discriminator_edge_shapes(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FILTER_ADAPTER.validate_python(payload)


def test_filter_accepts_maximum_depth() -> None:
    parsed = FILTER_ADAPTER.validate_python(_nested_not(4))

    assert parsed.model_dump(mode="json") == _nested_not(4)


def test_filter_rejects_tree_over_maximum_depth() -> None:
    with pytest.raises(ValidationError, match="maximum depth 4"):
        FILTER_ADAPTER.validate_python(_nested_not(5))


def test_filter_accepts_maximum_condition_count() -> None:
    parsed = FILTER_ADAPTER.validate_python(
        {"and": [_condition(index) for index in range(MAX_FILTER_CONDITIONS)]}
    )

    assert isinstance(parsed, AndClause)
    assert len(parsed.and_) == MAX_FILTER_CONDITIONS


def test_filter_rejects_tree_over_maximum_condition_count() -> None:
    with pytest.raises(ValidationError, match="maximum condition count 50"):
        FILTER_ADAPTER.validate_python(
            {"and": [_condition(index) for index in range(MAX_FILTER_CONDITIONS + 1)]}
        )
