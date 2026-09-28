from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import ColumnClause

from tracecat.db.models import TableColumn
from tracecat.exceptions import TracecatValidationError
from tracecat.query.compiler import compile_filter
from tracecat.query.filters import Condition, FilterOp
from tracecat.query.resolver import FieldKind, FieldResolver
from tracecat.tables.common import sanitize_identifier
from tracecat.tables.enums import SqlType
from tracecat.tables.query import TableFieldResolver

TEXT_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.IN,
        FilterOp.NOT_IN,
        FilterOp.CONTAINS,
        FilterOp.STARTS_WITH,
        FilterOp.IS_NULL,
    }
)
NUMBER_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.IN,
        FilterOp.NOT_IN,
        FilterOp.GT,
        FilterOp.GTE,
        FilterOp.LT,
        FilterOp.LTE,
        FilterOp.IS_NULL,
    }
)
BOOLEAN_OPS = frozenset({FilterOp.EQ, FilterOp.NE, FilterOp.IS_NULL})
TEMPORAL_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.GT,
        FilterOp.GTE,
        FilterOp.LT,
        FilterOp.LTE,
        FilterOp.IS_NULL,
    }
)
ENUM_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.IN,
        FilterOp.NOT_IN,
        FilterOp.IS_NULL,
    }
)


def _column(name: str, sql_type: str) -> TableColumn:
    return TableColumn(name=name, type=sql_type, nullable=True, default=None)


def _compile(
    condition: Condition, resolver: TableFieldResolver
) -> tuple[str, Mapping[str, Any]]:
    compiled = compile_filter(condition, resolver).compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


@pytest.mark.parametrize(
    ("sql_type", "expected_kind", "expected_ops", "expected_sa_type"),
    [
        (SqlType.TEXT, FieldKind.TEXT, TEXT_OPS, sa.String),
        (SqlType.INTEGER, FieldKind.NUMBER, NUMBER_OPS, sa.BigInteger),
        (SqlType.NUMERIC, FieldKind.NUMBER, NUMBER_OPS, sa.Numeric),
        (SqlType.DATE, FieldKind.TEMPORAL, TEMPORAL_OPS, sa.Date),
        (SqlType.BOOLEAN, FieldKind.BOOLEAN, BOOLEAN_OPS, sa.Boolean),
        (SqlType.TIMESTAMPTZ, FieldKind.TEMPORAL, TEMPORAL_OPS, sa.TIMESTAMP),
        (SqlType.SELECT, FieldKind.ENUM, ENUM_OPS, sa.String),
    ],
)
def test_resolves_supported_column_types(
    sql_type: SqlType,
    expected_kind: FieldKind,
    expected_ops: frozenset[FilterOp],
    expected_sa_type: type[sa.types.TypeEngine[Any]],
) -> None:
    name = "Display_Name"
    resolver = TableFieldResolver([_column(name, sql_type.value)])

    resolved = resolver.resolve(name)

    assert resolved is not None
    assert isinstance(resolved.expr, ColumnClause)
    assert resolved.expr.name == sanitize_identifier(name)
    assert resolved.kind is expected_kind
    assert resolved.allowed_ops == expected_ops
    assert isinstance(resolved.expr.type, expected_sa_type)
    if sql_type is SqlType.TIMESTAMPTZ:
        assert isinstance(resolved.expr.type, sa.TIMESTAMP)
        assert resolved.expr.type.timezone is True


@pytest.mark.parametrize(
    ("sql_type", "expected_kind", "expected_sa_type"),
    [
        (SqlType.TEXT, FieldKind.TEXT, sa.String),
        (SqlType.INTEGER, FieldKind.NUMBER, sa.BigInteger),
        (SqlType.NUMERIC, FieldKind.NUMBER, sa.Numeric),
        (SqlType.DATE, FieldKind.TEMPORAL, sa.Date),
        (SqlType.BOOLEAN, FieldKind.BOOLEAN, sa.Boolean),
        (SqlType.TIMESTAMPTZ, FieldKind.TEMPORAL, sa.TIMESTAMP),
        (SqlType.SELECT, FieldKind.ENUM, sa.String),
    ],
)
def test_resolves_supported_aggregation_column_types(
    sql_type: SqlType,
    expected_kind: FieldKind,
    expected_sa_type: type[sa.types.TypeEngine[Any]],
) -> None:
    resolver = TableFieldResolver([_column("value", sql_type.value)])

    resolved = resolver.resolve_aggregation("value")

    assert resolved is not None
    assert isinstance(resolved.expr, ColumnClause)
    assert resolved.kind is expected_kind
    assert resolved.is_multi_valued is False
    assert isinstance(resolved.expr.type, expected_sa_type)
    if sql_type is SqlType.TIMESTAMPTZ:
        assert isinstance(resolved.expr.type, sa.TIMESTAMP)
        assert resolved.expr.type.timezone is True


@pytest.mark.parametrize("sql_type", [SqlType.JSONB, SqlType.MULTI_SELECT])
def test_rejects_unsupported_column_types(sql_type: SqlType) -> None:
    resolver = TableFieldResolver([_column("tags", sql_type.value)])

    with pytest.raises(TracecatValidationError) as exc_info:
        resolver.resolve("tags")

    assert "tags" in str(exc_info.value)
    assert sql_type.value in str(exc_info.value)


@pytest.mark.parametrize("sql_type", [SqlType.JSONB, SqlType.MULTI_SELECT])
def test_rejects_unsupported_aggregation_column_types(sql_type: SqlType) -> None:
    resolver = TableFieldResolver([_column("payload", sql_type.value)])

    with pytest.raises(TracecatValidationError) as exc_info:
        resolver.resolve_aggregation("payload")

    assert "payload" in str(exc_info.value)
    assert sql_type.value in str(exc_info.value)
    assert "aggregations" in str(exc_info.value)


@pytest.mark.parametrize(
    "field", ["__tc_workspace_id", "__TC_workspace_id", "__tc_shadow"]
)
def test_internal_columns_are_never_resolvable(field: str) -> None:
    resolver = TableFieldResolver([_column(field, SqlType.TEXT.value)])

    assert resolver.resolve(field) is None


@pytest.mark.parametrize(
    "field",
    [
        '"; drop table"',
        "'; SELECT pg_sleep(1); --",
        '"quoted"',
        "naïve",
        "名前",
        "123field",
        "",
        "a.b",
    ],
)
def test_hostile_column_names_are_not_resolvable(field: str) -> None:
    resolver = TableFieldResolver([_column("status", SqlType.TEXT.value)])

    assert resolver.resolve(field) is None


@pytest.mark.parametrize("field", ["st;atus", '"status"', "status;--", "sta tus"])
def test_invalid_names_never_alias_to_existing_columns(field: str) -> None:
    # Guard against resolving through sanitize_identifier, which would strip the
    # offending characters and silently alias the input onto a real column.
    assert sanitize_identifier(field) == "status"
    resolver = TableFieldResolver([_column("status", SqlType.TEXT.value)])

    assert resolver.resolve(field) is None


def test_resolves_case_insensitive_fallback() -> None:
    resolver = TableFieldResolver([_column("status", SqlType.TEXT.value)])

    resolved = resolver.resolve("Status")

    assert resolved is not None
    assert resolved.kind is FieldKind.TEXT


def test_exact_column_name_wins_over_normalized_fallback() -> None:
    resolver = TableFieldResolver(
        [
            _column("status", SqlType.TEXT.value),
            _column("Status", SqlType.INTEGER.value),
        ]
    )

    resolved = resolver.resolve("Status")

    assert resolved is not None
    assert resolved.kind is FieldKind.NUMBER


@pytest.mark.parametrize("field", ["created_at", "updated_at", "Created_At"])
def test_resolves_system_temporal_columns(field: str) -> None:
    resolver = TableFieldResolver([])

    resolved = resolver.resolve(field)

    assert resolved is not None
    assert isinstance(resolved.expr, ColumnClause)
    assert resolved.kind is FieldKind.TEMPORAL
    assert resolved.allowed_ops == TEMPORAL_OPS
    assert isinstance(resolved.expr.type, sa.TIMESTAMP)
    assert resolved.expr.type.timezone is True


@pytest.mark.parametrize("field", ["id", "ID"])
def test_id_is_not_resolvable(field: str) -> None:
    resolver = TableFieldResolver([_column(field, SqlType.TEXT.value)])

    assert resolver.resolve(field) is None


def test_system_columns_take_precedence_over_user_metadata() -> None:
    resolver = TableFieldResolver([_column("created_at", SqlType.TEXT.value)])

    resolved = resolver.resolve("created_at")

    assert resolved is not None
    assert resolved.kind is FieldKind.TEMPORAL


@pytest.mark.parametrize("field", ["created_at", "updated_at", "Created_At"])
def test_resolves_system_columns_for_aggregation(field: str) -> None:
    resolver = TableFieldResolver([])

    resolved = resolver.resolve_aggregation(field)

    assert resolved is not None
    assert resolved.kind is FieldKind.TEMPORAL
    assert resolved.is_multi_valued is False
    assert isinstance(resolved.expr, ColumnClause)
    assert isinstance(resolved.expr.type, sa.TIMESTAMP)
    assert resolved.expr.type.timezone is True


@pytest.mark.parametrize("field", ["id", "ID", "__tc_workspace_id", "missing"])
def test_unresolvable_columns_are_not_available_for_aggregation(field: str) -> None:
    resolver = TableFieldResolver([])

    assert resolver.resolve_aggregation(field) is None


def test_compiler_rejects_unknown_column_with_field_name() -> None:
    resolver = TableFieldResolver([])

    with pytest.raises(TracecatValidationError, match="missing"):
        compile_filter(Condition(field="missing", op=FilterOp.EQ, value="x"), resolver)


def test_rejects_invalid_stored_column_type() -> None:
    resolver = TableFieldResolver([_column("payload", "BLOB")])

    with pytest.raises(TracecatValidationError) as exc_info:
        resolver.resolve("payload")

    assert "payload" in str(exc_info.value)
    assert "BLOB" in str(exc_info.value)


@pytest.mark.parametrize(
    ("name", "sql_type", "value", "expected"),
    [
        ("count", SqlType.INTEGER, "42", 42),
        ("amount", SqlType.NUMERIC, "1.50", Decimal("1.50")),
        (
            "observed_at",
            SqlType.TIMESTAMPTZ,
            "2026-09-01T12:00:00Z",
            datetime.fromisoformat("2026-09-01T12:00:00+00:00"),
        ),
        ("observed_on", SqlType.DATE, "2026-09-01", date(2026, 9, 1)),
    ],
)
def test_compiler_normalizes_bind_values_for_physical_type(
    name: str, sql_type: SqlType, value: str, expected: object
) -> None:
    resolver = TableFieldResolver([_column(name, sql_type.value)])

    _, params = _compile(Condition(field=name, op=FilterOp.EQ, value=value), resolver)

    assert list(params.values()) == [expected]


def test_text_contains_compiles_to_escaped_ilike() -> None:
    resolver = TableFieldResolver([_column("summary", SqlType.TEXT.value)])

    sql, params = _compile(
        Condition(field="summary", op=FilterOp.CONTAINS, value="a%_"), resolver
    )

    assert "summary ILIKE" in sql
    assert "ESCAPE '\\\\'" in sql
    assert list(params.values()) == [r"%a\%\_%"]


def test_select_values_are_not_validated_against_column_options() -> None:
    column = _column("status", SqlType.SELECT.value)
    column.options = ["open"]
    resolver = TableFieldResolver([column])

    _, params = _compile(
        Condition(field="status", op=FilterOp.EQ, value="closed"), resolver
    )

    assert list(params.values()) == ["closed"]


def test_compiler_rejects_disallowed_operation() -> None:
    resolver = TableFieldResolver([_column("count", SqlType.INTEGER.value)])

    with pytest.raises(TracecatValidationError, match="count"):
        compile_filter(
            Condition(field="count", op=FilterOp.CONTAINS, value="4"), resolver
        )


def test_satisfies_field_resolver_protocol() -> None:
    resolver: FieldResolver = TableFieldResolver([])

    assert resolver.resolve("missing") is None
