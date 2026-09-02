from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from tracecat.exceptions import TracecatValidationError
from tracecat.query.filters import (
    AndClause,
    Condition,
    Filter,
    FilterOp,
    FilterScalar,
    FilterValue,
    NotClause,
    OrClause,
)
from tracecat.query.resolver import FieldKind, FieldResolver, ResolvedField

POSTGRES_SMALLINT_MIN = -(2**15)
POSTGRES_SMALLINT_MAX = 2**15 - 1
POSTGRES_INTEGER_MIN = -(2**31)
POSTGRES_INTEGER_MAX = 2**31 - 1
POSTGRES_BIGINT_MIN = -(2**63)
POSTGRES_BIGINT_MAX = 2**63 - 1


def compile_filter(node: Filter, resolver: FieldResolver) -> ColumnElement[bool]:
    """Compile a validated filter tree into a SQLAlchemy predicate."""
    match node:
        case Condition():
            return _compile_condition(node, resolver)
        case AndClause(and_=children):
            return sa.and_(*(compile_filter(child, resolver) for child in children))
        case OrClause(or_=children):
            return sa.or_(*(compile_filter(child, resolver) for child in children))
        case NotClause(not_=child):
            return sa.not_(compile_filter(child, resolver))


def _compile_condition(
    condition: Condition, resolver: FieldResolver
) -> ColumnElement[bool]:
    resolved = resolver.resolve(condition.field)
    if resolved is None:
        raise _condition_error(condition, f"unknown field {condition.field!r}")
    if condition.op not in resolved.allowed_ops:
        raise _condition_error(
            condition,
            f"operation {condition.op.value!r} is not allowed for {resolved.kind.value}",
        )

    value = _validate_value_shape(condition, resolved.kind)
    if condition.op is FilterOp.IN and value == []:
        return sa.false()
    if condition.op is FilterOp.NOT_IN and value == []:
        return sa.true()

    expression = resolved.expr
    if isinstance(expression, InstrumentedAttribute):
        column_expression = expression.expression
    elif isinstance(expression, ColumnElement):
        column_expression = expression
    else:
        return expression(condition.op, value)

    normalized = _normalize_value(condition, resolved, column_expression, value)
    return _compile_expression(condition, column_expression, normalized)


def _validate_value_shape(condition: Condition, kind: FieldKind) -> FilterValue | None:
    value = condition.value
    if condition.op is FilterOp.IS_NULL:
        if value is not None:
            raise _condition_error(condition, "is_null does not accept a value")
        return None
    if value is None:
        raise _condition_error(condition, "operation requires a value")

    if condition.op in {FilterOp.IN, FilterOp.NOT_IN}:
        if not isinstance(value, list):
            raise _condition_error(condition, "operation requires a list value")
        for item in value:
            _validate_scalar(condition, kind, item)
        return value

    if isinstance(value, list):
        raise _condition_error(condition, "operation requires a scalar value")
    _validate_scalar(condition, kind, value)
    return value


def _validate_scalar(
    condition: Condition, kind: FieldKind, value: FilterScalar
) -> None:
    valid = _matches_kind_value(kind, value)
    if not valid:
        raise _condition_error(
            condition,
            f"value has the wrong shape for {kind.value}",
        )


def _matches_kind_value(kind: FieldKind, value: FilterScalar) -> bool:
    match kind:
        case FieldKind.TEXT | FieldKind.ENUM | FieldKind.TAG:
            return isinstance(value, str) and "\x00" not in value
        case FieldKind.NUMBER:
            if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
                return False
            if isinstance(value, float):
                return math.isfinite(value)
            if isinstance(value, Decimal):
                return value.is_finite()
            return True
        case FieldKind.BOOLEAN:
            return isinstance(value, bool)
        case FieldKind.TEMPORAL:
            if isinstance(value, date | datetime):
                return True
            if not isinstance(value, str):
                return False
            try:
                _parse_iso_datetime(value)
            except ValueError:
                return False
            return True
        case FieldKind.UUID:
            if isinstance(value, UUID):
                return True
            if not isinstance(value, str):
                return False
            try:
                UUID(value)
            except ValueError:
                return False
            return True


def _normalize_value(
    condition: Condition,
    resolved: ResolvedField,
    expression: ColumnElement[Any],
    value: FilterValue | None,
) -> object | list[object] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [
            _normalize_scalar(condition, resolved.kind, expression, item)
            for item in value
        ]
    return _normalize_scalar(condition, resolved.kind, expression, value)


def _normalize_scalar(
    condition: Condition,
    kind: FieldKind,
    expression: ColumnElement[Any],
    value: FilterScalar,
) -> object:
    try:
        match kind:
            case FieldKind.NUMBER:
                return _normalize_number(expression, value)
            case FieldKind.TEMPORAL:
                return _normalize_temporal(expression, value)
            case FieldKind.ENUM:
                return _normalize_enum(expression, value)
            case FieldKind.UUID:
                if isinstance(value, UUID):
                    return value
                if isinstance(value, str):
                    return UUID(value)
                raise TypeError
            case _:
                return value
    except (TypeError, ValueError) as exc:
        raise _condition_error(
            condition,
            f"value is invalid for {kind.value}",
        ) from exc


def _normalize_number(
    expression: ColumnElement[Any], value: FilterScalar
) -> int | float | Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise TypeError
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError
    if isinstance(expression.type, sa.Integer):
        decimal_value = Decimal(str(value))
        if decimal_value != decimal_value.to_integral_value():
            raise ValueError
        integer_value = int(decimal_value)
        lower_bound, upper_bound = _integer_bounds(expression.type)
        if not lower_bound <= integer_value <= upper_bound:
            raise ValueError
        return integer_value
    if isinstance(expression.type, sa.Float):
        try:
            float_value = float(value)
        except OverflowError as exc:
            raise ValueError from exc
        if not math.isfinite(float_value):
            raise ValueError
        return float_value
    if isinstance(expression.type, sa.Numeric):
        # SQLAlchemy otherwise infers an integer literal as BIGINT, even when the
        # compared expression is NUMERIC. Decimal forces the correct bind type.
        return Decimal(str(value))
    return value


def _integer_bounds(type_: sa.Integer) -> tuple[int, int]:
    if isinstance(type_, sa.SmallInteger):
        return POSTGRES_SMALLINT_MIN, POSTGRES_SMALLINT_MAX
    if isinstance(type_, sa.BigInteger):
        return POSTGRES_BIGINT_MIN, POSTGRES_BIGINT_MAX
    return POSTGRES_INTEGER_MIN, POSTGRES_INTEGER_MAX


def _normalize_temporal(
    expression: ColumnElement[Any], value: FilterScalar
) -> date | datetime:
    if not isinstance(value, str | date | datetime):
        raise TypeError
    if isinstance(expression.type, sa.Date) and not isinstance(
        expression.type, sa.DateTime
    ):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(value)
        except ValueError:
            return _parse_iso_datetime(value).date()

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=UTC)
    else:
        parsed = _parse_iso_datetime(value)
    if isinstance(expression.type, sa.DateTime) and not expression.type.timezone:
        return parsed.replace(tzinfo=None)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _normalize_enum(expression: ColumnElement[Any], value: FilterScalar) -> object:
    if not isinstance(value, str):
        raise TypeError
    if not isinstance(expression.type, sa.Enum):
        return value

    if enum_class := expression.type.enum_class:
        return enum_class(value)
    if value not in expression.type.enums:
        raise ValueError
    return value


def _parse_iso_datetime(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    return datetime.fromisoformat(text)


def _compile_expression(
    condition: Condition,
    expression: ColumnElement[Any],
    value: object | list[object] | None,
) -> ColumnElement[bool]:
    match condition.op:
        case FilterOp.EQ:
            return expression == value
        case FilterOp.NE:
            return expression != value
        case FilterOp.IN:
            assert isinstance(value, list)
            return sa.false() if not value else expression.in_(value)
        case FilterOp.NOT_IN:
            assert isinstance(value, list)
            return sa.true() if not value else expression.not_in(value)
        case FilterOp.GT:
            return expression > value
        case FilterOp.GTE:
            return expression >= value
        case FilterOp.LT:
            return expression < value
        case FilterOp.LTE:
            return expression <= value
        case FilterOp.CONTAINS:
            assert isinstance(value, str)
            return expression.ilike(f"%{_escape_like(value)}%", escape="\\")
        case FilterOp.STARTS_WITH:
            assert isinstance(value, str)
            return expression.ilike(f"{_escape_like(value)}%", escape="\\")
        case FilterOp.IS_NULL:
            return expression.is_(None)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _condition_error(condition: Condition, message: str) -> TracecatValidationError:
    return TracecatValidationError(
        f"Invalid filter condition for field {condition.field!r} "
        f"with operation {condition.op.value!r}: {message}"
    )
