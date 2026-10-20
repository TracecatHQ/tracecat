"""Payload equality uses bound JSON values, with the same case resolver everywhere."""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from tracecat.cases.query import CaseFieldResolver
from tracecat.cases.search_fields import case_filter_metadata
from tracecat.exceptions import TracecatValidationError
from tracecat.query.compiler import compile_filter
from tracecat.query.filters import Condition, FilterOp

WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000001")


@pytest.mark.parametrize("value", ["Microsoft", "80", 80, 80.5, True, False, "", 0])
def test_payload_equality_binds_json_scalar_types(
    value: str | int | float | bool,
) -> None:
    condition = Condition(
        field="payload.metadata.product.vendor_name", op=FilterOp.EQ, value=value
    )
    compiled = compile_filter(condition, CaseFieldResolver(WORKSPACE_ID, {})).compile(
        dialect=postgresql.dialect()
    )
    json_binds = [
        bind
        for bind in compiled.binds.values()
        if isinstance(bind.type, postgresql.JSONB)
    ]
    assert json_binds
    assert all(
        type(bind.value) is type(value) and bind.value == value for bind in json_binds
    )
    assert ("metadata", "product", "vendor_name") in compiled.params.values()
    assert "::JSONB" in str(compiled)


@pytest.mark.parametrize(
    "field",
    ["payload.", "payload..name", "payload.a\x00", "payload." + ".".join(["key"] * 33)],
)
def test_payload_rejects_invalid_paths(field: str) -> None:
    with pytest.raises(TracecatValidationError, match="payload filter path"):
        compile_filter(
            Condition(field=field, op=FilterOp.EQ, value="synthetic"),
            CaseFieldResolver(WORKSPACE_ID, {}),
        )


@pytest.mark.parametrize(
    "op,value",
    [
        (FilterOp.GT, 5),
        (FilterOp.CONTAINS, "x"),
        (FilterOp.IN, ["x"]),
        (FilterOp.EQ, date(2026, 1, 1)),
    ],
)
def test_payload_rejects_unsupported_operations_and_values(
    op: FilterOp, value: object
) -> None:
    with pytest.raises(TracecatValidationError, match="Invalid filter condition"):
        compile_filter(
            Condition.model_validate(
                {"field": "payload.value", "op": op, "value": value}
            ),
            CaseFieldResolver(WORKSPACE_ID, {}),
        )


def test_metadata_uses_resolver_operators_and_physical_input_types() -> None:
    metadata = case_filter_metadata(
        WORKSPACE_ID,
        {
            "risk_score": {"type": "INTEGER"},
            "active": {"type": "BOOLEAN"},
            "observed": {"type": "TIMESTAMPTZ"},
            "due": {"type": "DATE"},
            "category": {"type": "SELECT", "options": ["synthetic"]},
            "document": {"type": "JSONB"},
        },
        ["synthetic-dropdown"],
    )
    fields = {field.field: field for field in metadata.fields}
    assert fields["fields.risk_score"].input_type == "number"
    assert fields["fields.observed"].input_type == "datetime-local"
    assert fields["fields.due"].input_type == "date"
    assert fields["fields.category"].choices == ["synthetic"]
    assert "fields.document" not in fields
    assert fields["payload.<path>"].operators == [
        FilterOp.EQ,
        FilterOp.IS_NULL,
        FilterOp.NE,
    ]
    resolver = CaseFieldResolver(WORKSPACE_ID, {"active": {"type": "BOOLEAN"}})
    resolved = resolver.resolve("fields.active")
    assert resolved is not None
    assert set(fields["fields.active"].operators) == resolved.allowed_ops


def test_deleted_custom_select_option_is_rejected() -> None:
    resolver = CaseFieldResolver(
        WORKSPACE_ID, {"category": {"type": "SELECT", "options": ["current"]}}
    )
    with pytest.raises(TracecatValidationError, match="fields.category"):
        compile_filter(
            Condition(field="fields.category", op=FilterOp.EQ, value="deleted"),
            resolver,
        )


@pytest.mark.parametrize("options", [None, []])
def test_unconstrained_select_accepts_values(options: list[str] | None) -> None:
    schema = {"category": {"type": "SELECT", "options": options}}
    resolver = CaseFieldResolver(WORKSPACE_ID, schema)
    compile_filter(
        Condition(field="fields.category", op=FilterOp.EQ, value="synthetic"),
        resolver,
    )
    metadata = case_filter_metadata(WORKSPACE_ID, schema, [])
    category = next(
        field for field in metadata.fields if field.field == "fields.category"
    )
    assert category.choices is None


@pytest.mark.parametrize("field", ["priority", "severity"])
def test_metadata_range_choices_match_executing_resolver(field: str) -> None:
    metadata = case_filter_metadata(WORKSPACE_ID, {}, [])
    advertised = next(item for item in metadata.fields if item.field == field)
    assert advertised.choices is not None
    assert {"unknown", "other"} <= set(advertised.choices)
    assert advertised.range_choices is not None
    assert not {"unknown", "other"} & set(advertised.range_choices)
    resolver = CaseFieldResolver(WORKSPACE_ID, {})
    for op in [FilterOp.GT, FilterOp.GTE, FilterOp.LT, FilterOp.LTE]:
        for value in advertised.range_choices:
            compile_filter(Condition(field=field, op=op, value=value), resolver)
    compile_filter(Condition(field=field, op=FilterOp.EQ, value="unknown"), resolver)
