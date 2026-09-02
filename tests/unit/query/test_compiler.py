from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from tracecat.exceptions import TracecatValidationError
from tracecat.query.compiler import compile_filter
from tracecat.query.filters import (
    AndClause,
    Condition,
    FilterOp,
    FilterScalar,
    NotClause,
    OrClause,
)
from tracecat.query.resolver import FieldKind, ResolvedField

ALL_OPS = frozenset(FilterOp)
KIND_OPS: Mapping[FieldKind, frozenset[FilterOp]] = {
    FieldKind.TEXT: frozenset(
        {
            FilterOp.EQ,
            FilterOp.NE,
            FilterOp.IN,
            FilterOp.NOT_IN,
            FilterOp.CONTAINS,
            FilterOp.STARTS_WITH,
            FilterOp.IS_NULL,
        }
    ),
    FieldKind.NUMBER: frozenset(
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
    ),
    FieldKind.BOOLEAN: frozenset({FilterOp.EQ, FilterOp.NE, FilterOp.IS_NULL}),
    FieldKind.TEMPORAL: frozenset(
        {
            FilterOp.EQ,
            FilterOp.NE,
            FilterOp.GT,
            FilterOp.GTE,
            FilterOp.LT,
            FilterOp.LTE,
            FilterOp.IS_NULL,
        }
    ),
    FieldKind.ENUM: frozenset(
        {
            FilterOp.EQ,
            FilterOp.NE,
            FilterOp.IN,
            FilterOp.NOT_IN,
            FilterOp.IS_NULL,
        }
    ),
    FieldKind.UUID: frozenset(
        {
            FilterOp.EQ,
            FilterOp.NE,
            FilterOp.IN,
            FilterOp.NOT_IN,
            FilterOp.IS_NULL,
        }
    ),
    FieldKind.TAG: frozenset({FilterOp.CONTAINS, FilterOp.IN, FilterOp.IS_NULL}),
}


class StubResolver:
    def __init__(self, fields: Mapping[str, ResolvedField]) -> None:
        self._fields = fields

    def resolve(self, field: str) -> ResolvedField | None:
        return self._fields.get(field)


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "query_compiler_test_row"

    value: Mapped[str] = mapped_column(primary_key=True)


class _NativeStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


def _expression(kind: FieldKind) -> ColumnElement[Any]:
    match kind:
        case FieldKind.NUMBER:
            return sa.column("value", sa.Numeric())
        case FieldKind.BOOLEAN:
            return sa.column("value", sa.Boolean())
        case FieldKind.TEMPORAL:
            return sa.column("value", sa.DateTime(timezone=True))
        case FieldKind.UUID:
            return sa.column("value", sa.Uuid())
        case _:
            return sa.column("value", sa.String())


def _scalar(kind: FieldKind) -> FilterScalar:
    match kind:
        case FieldKind.NUMBER:
            return 42
        case FieldKind.BOOLEAN:
            return True
        case FieldKind.TEMPORAL:
            return "2026-09-01T12:00:00Z"
        case FieldKind.UUID:
            return "00000000-0000-0000-0000-000000000001"
        case _:
            return "value"


def _value(kind: FieldKind, op: FilterOp) -> Any:
    if op is FilterOp.IS_NULL:
        return None
    scalar = _scalar(kind)
    if op in {FilterOp.IN, FilterOp.NOT_IN}:
        return [scalar]
    return scalar


def _compile_sql(expression: ColumnElement[bool]) -> tuple[str, Mapping[str, Any]]:
    compiled = expression.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


@pytest.mark.parametrize(
    ("kind", "op"),
    [(kind, op) for kind in FieldKind for op in FilterOp],
)
def test_operation_kind_matrix(kind: FieldKind, op: FilterOp) -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=_expression(kind),
                kind=kind,
                allowed_ops=KIND_OPS[kind],
            )
        }
    )
    condition = Condition(field="value", op=op, value=_value(kind, op))

    if op not in KIND_OPS[kind]:
        with pytest.raises(TracecatValidationError, match="field 'value'"):
            compile_filter(condition, resolver)
        return

    assert isinstance(compile_filter(condition, resolver), ColumnElement)


@pytest.mark.parametrize(
    ("op", "sql_fragment"),
    [
        (FilterOp.EQ, "value ="),
        (FilterOp.NE, "value !="),
        (FilterOp.IN, "value IN"),
        (FilterOp.NOT_IN, "value NOT IN"),
        (FilterOp.GT, "value >"),
        (FilterOp.GTE, "value >="),
        (FilterOp.LT, "value <"),
        (FilterOp.LTE, "value <="),
        (FilterOp.CONTAINS, "value ILIKE"),
        (FilterOp.STARTS_WITH, "value ILIKE"),
        (FilterOp.IS_NULL, "value IS NULL"),
    ],
)
def test_compiles_each_operation(op: FilterOp, sql_fragment: str) -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=sa.column("value", sa.String()),
                kind=FieldKind.TEXT,
                allowed_ops=ALL_OPS,
            )
        }
    )
    value: Any
    if op is FilterOp.IS_NULL:
        value = None
    elif op in {FilterOp.IN, FilterOp.NOT_IN}:
        value = ["expected"]
    else:
        value = "expected"

    sql, _ = _compile_sql(
        compile_filter(Condition(field="value", op=op, value=value), resolver)
    )

    assert sql_fragment in sql


def test_ne_and_not_in_keep_sql_three_valued_logic() -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=sa.column("value", sa.String()),
                kind=FieldKind.TEXT,
                allowed_ops=ALL_OPS,
            )
        }
    )

    ne_sql, _ = _compile_sql(
        compile_filter(
            Condition(field="value", op=FilterOp.NE, value="expected"), resolver
        )
    )
    not_in_sql, _ = _compile_sql(
        compile_filter(
            Condition(field="value", op=FilterOp.NOT_IN, value=["expected"]),
            resolver,
        )
    )

    assert "IS NULL" not in ne_sql
    assert "IS DISTINCT FROM" not in ne_sql
    assert "IS NULL" not in not_in_sql
    assert "IS DISTINCT FROM" not in not_in_sql


@pytest.mark.parametrize(
    ("op", "expected_sql"),
    [(FilterOp.IN, "false"), (FilterOp.NOT_IN, "true")],
)
def test_empty_membership_short_circuits_direct_expression(
    op: FilterOp, expected_sql: str
) -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=sa.column("value", sa.String()),
                kind=FieldKind.TEXT,
                allowed_ops=ALL_OPS,
            )
        }
    )

    sql, _ = _compile_sql(
        compile_filter(Condition(field="value", op=op, value=[]), resolver)
    )

    assert sql == expected_sql


@pytest.mark.parametrize(
    ("op", "expected_sql"),
    [(FilterOp.IN, "false"), (FilterOp.NOT_IN, "true")],
)
def test_empty_membership_short_circuits_exists_factory(
    op: FilterOp, expected_sql: str
) -> None:
    calls = 0

    def factory(_op: FilterOp, _value: Any) -> ColumnElement[bool]:
        nonlocal calls
        calls += 1
        return sa.exists(sa.select(1))

    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=factory,
                kind=FieldKind.TEXT,
                allowed_ops=ALL_OPS,
            )
        }
    )

    sql, _ = _compile_sql(
        compile_filter(Condition(field="value", op=op, value=[]), resolver)
    )

    assert sql == expected_sql
    assert calls == 0


def test_ilike_escapes_wildcards_and_escape_character() -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=sa.column("value", sa.String()),
                kind=FieldKind.TEXT,
                allowed_ops=ALL_OPS,
            )
        }
    )

    contains, contains_params = _compile_sql(
        compile_filter(
            Condition(field="value", op=FilterOp.CONTAINS, value=r"a%_\b"),
            resolver,
        )
    )
    starts_sql, starts_params = _compile_sql(
        compile_filter(
            Condition(field="value", op=FilterOp.STARTS_WITH, value=r"a%_\b"),
            resolver,
        )
    )

    assert "ESCAPE '\\\\'" in contains
    assert "ESCAPE '\\\\'" in starts_sql
    assert list(contains_params.values()) == [r"%a\%\_\\b%"]
    assert list(starts_params.values()) == [r"a\%\_\\b%"]


def test_compiles_boolean_tree() -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=sa.column("value", sa.String()),
                kind=FieldKind.TEXT,
                allowed_ops=ALL_OPS,
            )
        }
    )
    negated = NotClause.model_validate(
        {"not_": Condition(field="value", op=FilterOp.EQ, value="third")}
    )
    disjunction = OrClause.model_validate(
        {
            "or_": [
                Condition(field="value", op=FilterOp.EQ, value="second"),
                negated,
            ]
        }
    )
    tree = AndClause.model_validate(
        {
            "and_": [
                Condition(field="value", op=FilterOp.EQ, value="first"),
                disjunction,
            ]
        }
    )

    sql, _ = _compile_sql(compile_filter(tree, resolver))

    assert " AND " in sql
    assert " OR " in sql
    assert "value !=" in sql


def test_exists_factory_receives_validated_condition() -> None:
    received: list[tuple[FilterOp, Any]] = []

    def factory(op: FilterOp, value: Any) -> ColumnElement[bool]:
        received.append((op, value))
        return sa.exists(sa.select(1).where(sa.literal_column("tag") == value))

    resolver = StubResolver(
        {
            "tags": ResolvedField(
                expr=factory,
                kind=FieldKind.TAG,
                allowed_ops=KIND_OPS[FieldKind.TAG],
            )
        }
    )

    expression = compile_filter(
        Condition(field="tags", op=FilterOp.CONTAINS, value="malware"), resolver
    )
    sql, _ = _compile_sql(expression)

    assert isinstance(expression, ColumnElement)
    assert received == [(FilterOp.CONTAINS, "malware")]
    assert "EXISTS" in sql
    assert "tag =" in sql


def test_accepts_orm_instrumented_attribute() -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=_Row.value,
                kind=FieldKind.TEXT,
                allowed_ops=ALL_OPS,
            )
        }
    )

    sql, _ = _compile_sql(
        compile_filter(
            Condition(field="value", op=FilterOp.EQ, value="expected"), resolver
        )
    )

    assert "query_compiler_test_row.value =" in sql


@pytest.mark.parametrize(
    "condition",
    [
        Condition(field="value", op=FilterOp.EQ, value=["one"]),
        Condition(field="value", op=FilterOp.IN, value="one"),
        Condition(field="value", op=FilterOp.IS_NULL, value="one"),
        Condition(field="value", op=FilterOp.EQ, value=None),
    ],
)
def test_rejects_wrong_operator_value_shape(condition: Condition) -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=sa.column("value", sa.String()),
                kind=FieldKind.TEXT,
                allowed_ops=ALL_OPS,
            )
        }
    )

    with pytest.raises(TracecatValidationError, match="field 'value'"):
        compile_filter(condition, resolver)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (FieldKind.TEXT, 1),
        (FieldKind.NUMBER, True),
        (FieldKind.NUMBER, float("inf")),
        (FieldKind.BOOLEAN, "true"),
        (FieldKind.TEMPORAL, "not-a-date"),
        (FieldKind.ENUM, 1),
        (FieldKind.UUID, "not-a-uuid"),
        (FieldKind.TAG, 1),
    ],
)
def test_rejects_wrong_kind_value(kind: FieldKind, value: Any) -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=_expression(kind),
                kind=kind,
                allowed_ops=ALL_OPS,
            )
        }
    )

    with pytest.raises(TracecatValidationError, match="field 'value'"):
        compile_filter(Condition(field="value", op=FilterOp.EQ, value=value), resolver)


def test_normalizes_direct_expression_values() -> None:
    field_id = "00000000-0000-0000-0000-000000000001"
    scenarios: list[tuple[FieldKind, ColumnElement[Any], Any, object]] = [
        (
            FieldKind.NUMBER,
            sa.column("value", sa.Numeric()),
            Decimal("1.5"),
            Decimal("1.5"),
        ),
        (
            FieldKind.TEMPORAL,
            sa.column("value", sa.Date()),
            "2026-09-01",
            date(2026, 9, 1),
        ),
        (
            FieldKind.TEMPORAL,
            sa.column("value", sa.DateTime(timezone=True)),
            "2026-09-01T12:00:00Z",
            datetime.fromisoformat("2026-09-01T12:00:00+00:00"),
        ),
        (FieldKind.UUID, sa.column("value", sa.Uuid()), field_id, UUID(field_id)),
    ]

    for kind, expression, value, expected in scenarios:
        resolver = StubResolver(
            {
                "value": ResolvedField(
                    expr=expression,
                    kind=kind,
                    allowed_ops=ALL_OPS,
                )
            }
        )
        _, params = _compile_sql(
            compile_filter(
                Condition(field="value", op=FilterOp.EQ, value=value), resolver
            )
        )
        assert list(params.values()) == [expected]


@pytest.mark.parametrize(
    ("sql_type", "minimum", "maximum"),
    [
        (sa.SmallInteger(), -(2**15), 2**15 - 1),
        (sa.Integer(), -(2**31), 2**31 - 1),
        (sa.BigInteger(), -(2**63), 2**63 - 1),
    ],
)
def test_validates_integer_bounds(
    sql_type: sa.Integer, minimum: int, maximum: int
) -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=sa.column("value", sql_type),
                kind=FieldKind.NUMBER,
                allowed_ops=ALL_OPS,
            )
        }
    )

    for value in (minimum, maximum):
        _, params = _compile_sql(
            compile_filter(
                Condition(field="value", op=FilterOp.EQ, value=value), resolver
            )
        )
        assert list(params.values()) == [value]

    for value in (minimum - 1, maximum + 1):
        with pytest.raises(TracecatValidationError, match="field 'value'"):
            compile_filter(
                Condition(field="value", op=FilterOp.EQ, value=value), resolver
            )


def test_normalizes_large_numeric_integer_to_decimal() -> None:
    resolver = StubResolver(
        {
            "value": ResolvedField(
                expr=sa.column("value", sa.Numeric()),
                kind=FieldKind.NUMBER,
                allowed_ops=ALL_OPS,
            )
        }
    )
    value = 10**100

    expression = compile_filter(
        Condition(field="value", op=FilterOp.EQ, value=value), resolver
    )
    _, params = _compile_sql(expression)

    assert list(params.values()) == [Decimal(str(value))]
    assert isinstance(expression.right.type, sa.Numeric)


def test_normalizes_native_enum_values() -> None:
    expression = sa.column("status", sa.Enum(_NativeStatus))
    resolver = StubResolver(
        {
            "status": ResolvedField(
                expr=expression,
                kind=FieldKind.ENUM,
                allowed_ops=ALL_OPS,
            )
        }
    )

    _, params = _compile_sql(
        compile_filter(
            Condition(field="status", op=FilterOp.IN, value=["open", "closed"]),
            resolver,
        )
    )

    assert list(params.values()) == [[_NativeStatus.OPEN, _NativeStatus.CLOSED]]


def test_rejects_unknown_native_enum_value() -> None:
    resolver = StubResolver(
        {
            "status": ResolvedField(
                expr=sa.column("status", sa.Enum(_NativeStatus)),
                kind=FieldKind.ENUM,
                allowed_ops=ALL_OPS,
            )
        }
    )

    with pytest.raises(TracecatValidationError, match="field 'status'"):
        compile_filter(
            Condition(field="status", op=FilterOp.EQ, value="invalid"), resolver
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-01T12:00:00", datetime(2026, 9, 1, 12)),
        ("2026-09-01T12:00:00-04:00", datetime(2026, 9, 1, 12)),
        (date(2026, 9, 1), datetime(2026, 9, 1)),
    ],
)
def test_normalizes_timezone_free_datetime_without_tzinfo(
    value: str | date, expected: datetime
) -> None:
    resolver = StubResolver(
        {
            "created_at": ResolvedField(
                expr=sa.column("created_at", sa.DateTime(timezone=False)),
                kind=FieldKind.TEMPORAL,
                allowed_ops=ALL_OPS,
            )
        }
    )

    _, params = _compile_sql(
        compile_filter(
            Condition(field="created_at", op=FilterOp.EQ, value=value), resolver
        )
    )

    assert list(params.values()) == [expected]


@pytest.mark.parametrize(
    "field",
    ["unknown", '"; drop table case; --', "workspace_id", "internal_secret"],
)
def test_rejects_unknown_and_hostile_field_names(field: str) -> None:
    resolver = StubResolver({})

    with pytest.raises(TracecatValidationError, match=field.replace("?", "\\?")):
        compile_filter(Condition(field=field, op=FilterOp.EQ, value="value"), resolver)
