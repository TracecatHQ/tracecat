from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import ColumnElement

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.query import (
    BOOLEAN_OPS,
    ENUM_OPS,
    NUMBER_OPS,
    RANKED_ENUM_OPS,
    TEMPORAL_OPS,
    TEXT_OPS,
    UUID_OPS,
    CaseFieldResolver,
)
from tracecat.db.models import Case
from tracecat.exceptions import TracecatValidationError
from tracecat.query.compiler import compile_filter
from tracecat.query.filters import Condition, FilterOp
from tracecat.query.resolver import FieldKind, FieldResolver
from tracecat.tables.enums import SqlType

WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000001")


def _schema(**fields: str | Mapping[str, object]) -> dict[str, object]:
    return {
        name: {"type": definition} if isinstance(definition, str) else dict(definition)
        for name, definition in fields.items()
    }


def _resolver(**fields: str | Mapping[str, object]) -> CaseFieldResolver:
    return CaseFieldResolver(WORKSPACE_ID, _schema(**fields))


def _compile(
    condition: Condition,
    resolver: CaseFieldResolver,
    *,
    literal_binds: bool = False,
) -> tuple[str, Mapping[str, Any]]:
    predicate = compile_filter(condition, resolver)
    statement = sa.select(Case.id).where(predicate)
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": literal_binds},
    )
    return str(compiled), compiled.params


@pytest.mark.parametrize(
    ("field", "expected_kind", "expected_ops"),
    [
        ("status", FieldKind.ENUM, ENUM_OPS),
        ("priority", FieldKind.ENUM, RANKED_ENUM_OPS),
        ("severity", FieldKind.ENUM, RANKED_ENUM_OPS),
        ("assignee_id", FieldKind.UUID, UUID_OPS),
        ("created_at", FieldKind.TEMPORAL, TEMPORAL_OPS),
        ("updated_at", FieldKind.TEMPORAL, TEMPORAL_OPS),
        ("summary", FieldKind.TEXT, TEXT_OPS),
        ("description", FieldKind.TEXT, TEXT_OPS),
        ("case_number", FieldKind.NUMBER, NUMBER_OPS),
    ],
)
def test_resolves_fixed_filter_fields(
    field: str,
    expected_kind: FieldKind,
    expected_ops: frozenset[FilterOp],
) -> None:
    resolved = _resolver().resolve(field)

    assert resolved is not None
    assert resolved.kind is expected_kind
    assert resolved.allowed_ops == expected_ops


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "priority",
        "severity",
        "assignee_id",
        "created_at",
        "updated_at",
    ],
)
def test_resolves_groupable_fixed_fields(field: str) -> None:
    resolved = _resolver().resolve_aggregation(field)

    assert resolved is not None
    assert resolved.join is None


@pytest.mark.parametrize("field", ["summary", "description", "case_number"])
def test_filter_only_fixed_fields_are_not_aggregation_dimensions(field: str) -> None:
    assert _resolver().resolve_aggregation(field) is None


def test_short_id_is_not_resolvable() -> None:
    resolver = _resolver()

    assert resolver.resolve("short_id") is None
    assert resolver.resolve_aggregation("short_id") is None


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("status", "new", CaseStatus.NEW),
        ("priority", "high", CasePriority.HIGH),
        ("severity", "fatal", CaseSeverity.FATAL),
    ],
)
def test_enum_values_are_validated_from_lowercase_public_values(
    field: str,
    value: str,
    expected: CaseStatus | CasePriority | CaseSeverity,
) -> None:
    _, params = _compile(
        Condition(field=field, op=FilterOp.EQ, value=value),
        _resolver(),
    )

    assert expected in params.values()


@pytest.mark.parametrize("field", ["status", "priority", "severity"])
@pytest.mark.parametrize("value", ["HIGH", "not_a_member"])
def test_invalid_enum_values_are_rejected(field: str, value: str) -> None:
    with pytest.raises(TracecatValidationError, match=field):
        compile_filter(
            Condition(field=field, op=FilterOp.EQ, value=value),
            _resolver(),
        )


@pytest.mark.parametrize(
    ("field", "value", "members", "unordered"),
    [
        (
            "priority",
            "high",
            ["low", "medium", "high", "critical"],
            ["unknown", "other"],
        ),
        (
            "severity",
            "high",
            ["informational", "low", "medium", "high", "critical", "fatal"],
            ["unknown", "other"],
        ),
    ],
)
def test_ranked_enum_ranges_compile_dedicated_case_expression(
    field: str,
    value: str,
    members: list[str],
    unordered: list[str],
) -> None:
    sql, _ = _compile(
        Condition(field=field, op=FilterOp.GTE, value=value),
        _resolver(),
        literal_binds=True,
    )

    assert "CASE" in sql
    assert sql.count("WHEN") == len(members)
    for member in members:
        assert member.upper() in sql
    for member in unordered:
        assert member.upper() not in sql
    # PostgreSQL CASE defaults to NULL when no ELSE branch is present, so
    # unordered rows cannot satisfy the comparison.
    assert "ELSE" not in sql
    assert " END >=" in sql


@pytest.mark.parametrize("field", ["priority", "severity"])
@pytest.mark.parametrize("value", ["unknown", "other"])
def test_unordered_enum_members_are_rejected_as_range_values(
    field: str, value: str
) -> None:
    with pytest.raises(TracecatValidationError, match=field):
        compile_filter(
            Condition(field=field, op=FilterOp.GTE, value=value),
            _resolver(),
        )


@pytest.mark.parametrize(
    "op",
    [FilterOp.GT, FilterOp.GTE, FilterOp.LT, FilterOp.LTE],
)
def test_status_rejects_range_operations(op: FilterOp) -> None:
    with pytest.raises(TracecatValidationError, match="status"):
        compile_filter(
            Condition(field="status", op=op, value="new"),
            _resolver(),
        )


@pytest.mark.parametrize(
    ("sql_type", "expected_kind", "expected_ops", "expected_sa_type"),
    [
        (SqlType.TEXT, FieldKind.TEXT, TEXT_OPS, sa.String),
        (SqlType.INTEGER, FieldKind.NUMBER, NUMBER_OPS, sa.BigInteger),
        (SqlType.NUMERIC, FieldKind.NUMBER, NUMBER_OPS, sa.Numeric),
        (SqlType.BOOLEAN, FieldKind.BOOLEAN, BOOLEAN_OPS, sa.Boolean),
        (SqlType.DATE, FieldKind.TEMPORAL, TEMPORAL_OPS, sa.Date),
        (SqlType.TIMESTAMPTZ, FieldKind.TEMPORAL, TEMPORAL_OPS, sa.TIMESTAMP),
        (SqlType.SELECT, FieldKind.ENUM, ENUM_OPS, sa.String),
    ],
)
def test_resolves_supported_custom_field_types(
    sql_type: SqlType,
    expected_kind: FieldKind,
    expected_ops: frozenset[FilterOp],
    expected_sa_type: type[sa.types.TypeEngine[Any]],
) -> None:
    resolver = _resolver(custom=sql_type.value)

    resolved = resolver.resolve("fields.custom")
    aggregation = resolver.resolve_aggregation("fields.custom")

    assert resolved is not None
    assert resolved.kind is expected_kind
    assert resolved.allowed_ops == expected_ops
    assert resolved.value_type is not None
    assert isinstance(resolved.value_type, expected_sa_type)
    assert aggregation is not None
    assert aggregation.field.kind is expected_kind
    assert isinstance(aggregation.field.expr.type, expected_sa_type)
    assert aggregation.join is not None
    assert aggregation.join.is_outer is True


def test_long_text_kind_behaves_as_text() -> None:
    resolved = _resolver(
        notes={"type": SqlType.TEXT.value, "kind": "LONG_TEXT"}
    ).resolve("fields.notes")

    assert resolved is not None
    assert resolved.kind is FieldKind.TEXT
    assert resolved.allowed_ops == TEXT_OPS


def test_url_kind_uses_nested_url_as_text() -> None:
    resolver = _resolver(link={"type": SqlType.JSONB.value, "kind": "URL"})

    resolved = resolver.resolve("fields.link")
    aggregation = resolver.resolve_aggregation("fields.link")
    sql, params = _compile(
        Condition(field="fields.link", op=FilterOp.CONTAINS, value="example.com"),
        resolver,
    )

    assert resolved is not None
    assert resolved.kind is FieldKind.TEXT
    assert isinstance(resolved.value_type, sa.String)
    assert aggregation is not None
    assert aggregation.field.kind is FieldKind.TEXT
    assert isinstance(aggregation.field.expr.type, sa.String)
    assert "->>" in sql
    assert "ILIKE" in sql
    assert r"%example.com%" in params.values()


@pytest.mark.parametrize("sql_type", [SqlType.JSONB, SqlType.MULTI_SELECT])
def test_rejects_unsupported_custom_field_types(sql_type: SqlType) -> None:
    resolver = _resolver(custom=sql_type.value)

    with pytest.raises(TracecatValidationError) as filter_error:
        resolver.resolve("fields.custom")
    with pytest.raises(TracecatValidationError) as aggregation_error:
        resolver.resolve_aggregation("fields.custom")

    assert "custom" in str(filter_error.value)
    assert sql_type.value in str(filter_error.value)
    assert "custom" in str(aggregation_error.value)
    assert sql_type.value in str(aggregation_error.value)


def test_unknown_custom_field_is_unresolvable_without_physical_table_access() -> None:
    resolver = _resolver()

    assert resolver.resolve("fields.missing") is None
    assert resolver.resolve_aggregation("fields.missing") is None
    with pytest.raises(TracecatValidationError, match="fields.missing"):
        compile_filter(
            Condition(field="fields.missing", op=FilterOp.EQ, value="x"),
            resolver,
        )


@pytest.mark.parametrize(
    "field",
    [
        "fields.",
        "fields.case_id",
        "fields.created_at",
        "fields.__tc_workspace_id",
        'fields."; drop table case_fields; --',
        "fields.a.b",
        "field.custom",
    ],
)
def test_invalid_or_reserved_custom_field_addresses_are_unresolvable(
    field: str,
) -> None:
    assert _resolver(custom=SqlType.TEXT.value).resolve(field) is None


def test_custom_filter_compiles_to_correlated_exists() -> None:
    sql, params = _compile(
        Condition(field="fields.region", op=FilterOp.EQ, value="emea"),
        _resolver(region=SqlType.TEXT.value),
    )

    assert "WHERE EXISTS (SELECT 1" in sql
    assert '.case_fields.case_id = "case".id' in sql
    assert ".case_fields.region =" in sql
    assert list(params.values()) == ["emea"]


def test_custom_is_null_compiles_to_not_exists_non_null_form() -> None:
    sql, params = _compile(
        Condition(field="fields.region", op=FilterOp.IS_NULL),
        _resolver(region=SqlType.TEXT.value),
    )

    assert "NOT (EXISTS (SELECT 1" in sql or "NOT EXISTS (SELECT 1" in sql
    assert '.case_fields.case_id = "case".id' in sql
    assert ".case_fields.region IS NOT NULL" in sql
    assert params == {}


def test_custom_text_filter_escapes_like_wildcards() -> None:
    _, params = _compile(
        Condition(field="fields.summary", op=FilterOp.CONTAINS, value="a%_\\"),
        _resolver(summary=SqlType.TEXT.value),
    )

    assert list(params.values()) == [r"%a\%\_\\%"]


def test_custom_number_filter_uses_physical_type_for_value_normalization() -> None:
    _, params = _compile(
        Condition(field="fields.score", op=FilterOp.GTE, value="42"),
        _resolver(score=SqlType.INTEGER.value),
    )

    assert list(params.values()) == [42]


def test_custom_aggregation_fields_share_one_left_join_spec() -> None:
    resolver = _resolver(
        region=SqlType.TEXT.value,
        score=SqlType.INTEGER.value,
    )

    region = resolver.resolve_aggregation("fields.region")
    score = resolver.resolve_aggregation("fields.score")

    assert region is not None and region.join is not None
    assert score is not None and score.join is not None
    assert region.join is score.join
    assert region.join.key == "case_fields"
    statement = (
        sa.select(Case.id)
        .select_from(Case)
        .join(
            region.join.target,
            region.join.onclause,
            isouter=region.join.is_outer,
        )
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN" in sql
    assert ".case_fields ON" in sql
    assert '.case_fields.case_id = "case".id' in sql


@pytest.mark.parametrize(
    "field_schema",
    [
        {"field": "TEXT"},
        {"field": {}},
        {"field": {"type": "BLOB"}},
        {"field": {"type": "TEXT", "kind": "RICH_TEXT"}},
        {"case_id": {"type": "TEXT"}},
        {'bad"name': {"type": "TEXT"}},
    ],
)
def test_rejects_invalid_stored_custom_field_schema(
    field_schema: Mapping[str, object],
) -> None:
    with pytest.raises(TracecatValidationError, match="field|case_id|bad"):
        CaseFieldResolver(WORKSPACE_ID, field_schema)


@pytest.mark.parametrize(
    "definition",
    [
        {"type": SqlType.TEXT.value, "kind": "URL"},
        {"type": SqlType.JSONB.value, "kind": "LONG_TEXT"},
    ],
)
def test_rejects_invalid_kind_storage_pair(definition: Mapping[str, object]) -> None:
    resolver = _resolver(custom=definition)

    with pytest.raises(TracecatValidationError, match="custom"):
        resolver.resolve("fields.custom")


def test_satisfies_field_resolver_protocol() -> None:
    resolver: FieldResolver = _resolver()

    assert resolver.resolve("missing") is None


def test_compiled_filter_is_a_sqlalchemy_boolean_expression() -> None:
    predicate = compile_filter(
        Condition(field="priority", op=FilterOp.GTE, value="high"),
        _resolver(),
    )

    assert isinstance(predicate, ColumnElement)
