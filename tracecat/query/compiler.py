from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.type_api import TypeEngine

from tracecat.exceptions import TracecatValidationError
from tracecat.query.aggregations import (
    AggFunction,
    AggregationSpec,
    AggSpec,
    GroupBySpec,
    SortDirection,
)
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
from tracecat.query.resolver import (
    FieldKind,
    FieldResolver,
    NormalizedFilterScalar,
    NormalizedFilterValue,
    ResolvedAggregationField,
)

POSTGRES_SMALLINT_MIN = -(2**15)
POSTGRES_SMALLINT_MAX = 2**15 - 1
POSTGRES_INTEGER_MIN = -(2**31)
POSTGRES_INTEGER_MAX = 2**31 - 1
POSTGRES_BIGINT_MIN = -(2**63)
POSTGRES_BIGINT_MAX = 2**63 - 1
POSTGRES_NUMERIC_MAX_WHOLE_DIGITS = 131_072
POSTGRES_NUMERIC_MAX_FRACTIONAL_DIGITS = 16_383


def compile_aggregation(
    statement: Select[Any],
    spec: AggregationSpec,
    resolved_fields: Mapping[str, ResolvedAggregationField],
    *,
    limit: int,
    entity_id: ColumnElement[Any] | InstrumentedAttribute[Any] | None = None,
) -> Select[Any]:
    """Compile aggregation clauses onto an entity-scoped base statement.

    The caller owns field resolution, joins, and row-level predicates. This
    function replaces the base statement's projection, then adds GROUP BY,
    aggregates, HAVING, deterministic ordering, and ``LIMIT limit + 1``.

    Args:
        statement: A base select with its FROM clauses, joins, and filters.
        spec: Validated shared aggregation query shape.
        resolved_fields: Trusted aggregation expressions keyed by field address.
        limit: Maximum number of groups the caller will return.
        entity_id: Stable row identity used to de-duplicate counts when a
            multi-valued grouping dimension explodes one entity into many rows.

    Returns:
        The compiled SQLAlchemy select.

    Raises:
        TracecatValidationError: If resolved fields or requested operations are
            semantically incompatible.
    """
    if limit < 1:
        raise TracecatValidationError("Aggregation limit must be at least 1")

    resolved_groups = [
        (group, _resolve_aggregation_field(group.field, resolved_fields))
        for group in spec.group_by
    ]
    resolved_aggs = [
        (
            agg,
            None
            if agg.field is None
            else _resolve_aggregation_field(agg.field, resolved_fields),
        )
        for agg in spec.aggs
    ]
    has_multi_valued_group = any(field.is_multi_valued for _, field in resolved_groups)
    entity_id_expression = None if entity_id is None else _column_expression(entity_id)
    if has_multi_valued_group and entity_id_expression is None:
        raise TracecatValidationError(
            "Aggregation with a multi-valued group requires an entity id expression"
        )

    group_expressions = [
        _compile_group_by(group, field) for group, field in resolved_groups
    ]
    agg_expressions = [
        _compile_aggregate(
            agg,
            field,
            has_multi_valued_group=has_multi_valued_group,
            entity_id=entity_id_expression,
        )
        for agg, field in resolved_aggs
    ]

    compiled = statement.with_only_columns(
        *group_expressions,
        *agg_expressions,
        maintain_column_froms=True,
    )
    compiled = compiled.group_by(None).order_by(None).limit(None)
    if group_expressions:
        compiled = compiled.group_by(*group_expressions)

    if spec.min_count is not None:
        row_count = _compile_row_count(
            has_multi_valued_group=has_multi_valued_group,
            entity_id=entity_id_expression,
        )
        compiled = compiled.having(row_count >= spec.min_count)

    output_expressions = {
        expression.name: expression
        for expression in [*group_expressions, *agg_expressions]
    }
    order_key, direction = _resolve_ordering(spec)
    primary = output_expressions[order_key]
    order_expressions = [_ordered(primary, direction)]
    order_expressions.extend(
        _ordered(group, direction)
        for group in group_expressions
        if group.name != order_key
    )
    return compiled.order_by(*order_expressions).limit(limit + 1)


def _resolve_aggregation_field(
    field: str,
    resolved_fields: Mapping[str, ResolvedAggregationField],
) -> ResolvedAggregationField:
    resolved = resolved_fields.get(field)
    if resolved is None:
        raise TracecatValidationError(f"Unknown aggregation field {field!r}")
    return resolved


def _column_expression(
    expression: ColumnElement[Any] | InstrumentedAttribute[Any],
) -> ColumnElement[Any]:
    if isinstance(expression, InstrumentedAttribute):
        return expression.expression
    return expression


def _compile_group_by(
    spec: GroupBySpec, resolved: ResolvedAggregationField
) -> sa.Label[Any]:
    expression = _column_expression(resolved.expr)
    if resolved.kind is FieldKind.TEMPORAL:
        if spec.bucket is None:
            raise _aggregation_error(spec.field, "temporal group keys require a bucket")
        expression = _compile_bucket(spec, expression)
    elif spec.bucket is not None:
        raise _aggregation_error(
            spec.field,
            f"bucket is not allowed for {resolved.kind.value} fields",
        )

    if _uses_bounded_text_group(resolved.kind, expression.type):
        expression = sa.func.left(expression, 256, type_=sa.String())
    return expression.label(spec.output_key)


def _compile_bucket(
    spec: GroupBySpec, expression: ColumnElement[Any]
) -> ColumnElement[Any]:
    type_ = expression.type
    if isinstance(type_, sa.Date) and not isinstance(type_, sa.DateTime):
        if spec.timezone is not None:
            raise _aggregation_error(spec.field, "timezone is not allowed for DATE")
        timestamp = sa.cast(expression, sa.DateTime(timezone=False))
        return sa.cast(
            sa.func.date_trunc(spec.bucket, timestamp),
            sa.Date(),
        )
    if isinstance(type_, sa.DateTime) and type_.timezone:
        return sa.func.date_trunc(
            spec.bucket,
            expression,
            spec.timezone or "UTC",
            type_=sa.DateTime(timezone=True),
        )
    raise _aggregation_error(spec.field, "temporal buckets require DATE or TIMESTAMPTZ")


def _uses_bounded_text_group(kind: FieldKind, type_: TypeEngine[Any]) -> bool:
    if kind is FieldKind.TEXT:
        return True
    return (
        kind is FieldKind.ENUM
        and isinstance(type_, sa.String)
        and not isinstance(type_, sa.Enum)
    )


def _compile_aggregate(
    spec: AggSpec,
    resolved: ResolvedAggregationField | None,
    *,
    has_multi_valued_group: bool,
    entity_id: ColumnElement[Any] | None,
) -> sa.Label[Any]:
    if resolved is None:
        assert spec.function is AggFunction.COUNT
        expression = _compile_row_count(
            has_multi_valued_group=has_multi_valued_group,
            entity_id=entity_id,
        )
        return expression.label(spec.output_key)

    field_expression = _column_expression(resolved.expr)
    _validate_aggregate_target(spec, resolved.kind, field_expression.type)

    if has_multi_valued_group and spec.function in {
        AggFunction.SUM,
        AggFunction.MEAN,
        AggFunction.MEDIAN,
    }:
        raise _aggregation_error(
            spec.field,
            f"{spec.function.value} is not allowed with a multi-valued group",
        )

    match spec.function:
        case AggFunction.COUNT:
            if has_multi_valued_group:
                assert entity_id is not None
                expression = sa.func.count(sa.distinct(entity_id)).filter(
                    field_expression.is_not(None)
                )
            else:
                expression = sa.func.count(field_expression)
        case AggFunction.COUNT_DISTINCT:
            expression = sa.func.count(sa.distinct(field_expression))
        case AggFunction.SUM:
            expression = sa.func.sum(field_expression)
            if isinstance(field_expression.type, sa.Integer):
                expression = sa.cast(expression, sa.BigInteger())
            else:
                expression = sa.cast(expression, postgresql.DOUBLE_PRECISION())
        case AggFunction.MEAN:
            expression = sa.cast(
                sa.func.avg(field_expression), postgresql.DOUBLE_PRECISION()
            )
        case AggFunction.MEDIAN:
            expression = sa.cast(
                sa.func.percentile_cont(0.5).within_group(field_expression),
                postgresql.DOUBLE_PRECISION(),
            )
        case AggFunction.MIN:
            expression = sa.func.min(field_expression)
            if isinstance(field_expression.type, sa.Numeric) and not isinstance(
                field_expression.type, sa.Integer
            ):
                expression = sa.cast(expression, postgresql.DOUBLE_PRECISION())
        case AggFunction.MAX:
            expression = sa.func.max(field_expression)
            if isinstance(field_expression.type, sa.Numeric) and not isinstance(
                field_expression.type, sa.Integer
            ):
                expression = sa.cast(expression, postgresql.DOUBLE_PRECISION())
    return expression.label(spec.output_key)


def _validate_aggregate_target(
    spec: AggSpec, kind: FieldKind, type_: TypeEngine[Any]
) -> None:
    function = spec.function
    if function is AggFunction.COUNT:
        return
    if function is AggFunction.COUNT_DISTINCT and kind in {
        FieldKind.TEXT,
        FieldKind.NUMBER,
        FieldKind.BOOLEAN,
        FieldKind.TEMPORAL,
        FieldKind.ENUM,
        FieldKind.UUID,
    }:
        return
    if function in {AggFunction.SUM, AggFunction.MEAN, AggFunction.MEDIAN}:
        if kind is FieldKind.NUMBER and isinstance(
            type_, sa.Integer | sa.Numeric | sa.Float
        ):
            return
    if function in {AggFunction.MIN, AggFunction.MAX} and kind in {
        FieldKind.TEXT,
        FieldKind.NUMBER,
        FieldKind.TEMPORAL,
    }:
        return
    raise _aggregation_error(
        spec.field,
        f"{function.value} is not allowed for {kind.value} fields",
    )


def _compile_row_count(
    *,
    has_multi_valued_group: bool,
    entity_id: ColumnElement[Any] | None,
) -> ColumnElement[int]:
    if has_multi_valued_group:
        assert entity_id is not None
        return sa.func.count(sa.distinct(entity_id))
    return sa.func.count()


def _resolve_ordering(spec: AggregationSpec) -> tuple[str, SortDirection]:
    if spec.order_by is not None:
        default_direction = SortDirection.DESC
        key = spec.order_by
    else:
        bucket_group = next(
            (group for group in spec.group_by if group.bucket is not None), None
        )
        if bucket_group is not None:
            default_direction = SortDirection.ASC
            key = bucket_group.output_key
        else:
            default_direction = SortDirection.DESC
            key = spec.aggs[0].output_key
    return key, spec.sort or default_direction


def _ordered(
    expression: ColumnElement[Any], direction: SortDirection
) -> ColumnElement[Any]:
    ordered = expression.asc() if direction is SortDirection.ASC else expression.desc()
    return ordered.nulls_last()


def _aggregation_error(field: str | None, message: str) -> TracecatValidationError:
    if field is None:
        return TracecatValidationError(f"Invalid aggregation: {message}")
    return TracecatValidationError(
        f"Invalid aggregation for field {field!r}: {message}"
    )


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

    value = _validate_value_shape(condition)
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
        if value is None:
            normalized = None
        elif resolved.value_type is None:
            raise _condition_error(
                condition,
                "predicate factory requires a value type",
            )
        else:
            normalized = _normalize_value(
                condition, resolved.kind, resolved.value_type, value
            )
        return expression(condition.op, normalized)

    normalized = _normalize_value(
        condition, resolved.kind, column_expression.type, value
    )
    return _compile_expression(condition, column_expression, normalized)


def _validate_value_shape(condition: Condition) -> FilterValue | None:
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
        return value

    if isinstance(value, list):
        raise _condition_error(condition, "operation requires a scalar value")
    return value


def _normalize_value(
    condition: Condition,
    kind: FieldKind,
    value_type: TypeEngine[Any],
    value: FilterValue | None,
) -> NormalizedFilterValue | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [_normalize_scalar(condition, kind, value_type, item) for item in value]
    return _normalize_scalar(condition, kind, value_type, value)


def _normalize_scalar(
    condition: Condition,
    kind: FieldKind,
    value_type: TypeEngine[Any],
    value: FilterScalar,
) -> NormalizedFilterScalar:
    try:
        match kind:
            case FieldKind.NUMBER:
                return _normalize_number(value_type, value)
            case FieldKind.TEMPORAL:
                return _normalize_temporal(value_type, value)
            case FieldKind.ENUM:
                return _normalize_enum(value_type, value)
            case FieldKind.UUID:
                if isinstance(value, UUID):
                    return value
                if isinstance(value, str):
                    return UUID(value)
                raise TypeError
            case FieldKind.BOOLEAN:
                if not isinstance(value, bool):
                    raise TypeError
                return value
            case FieldKind.TEXT | FieldKind.TAG:
                if not isinstance(value, str) or "\x00" in value:
                    raise TypeError
                return value
    except (TypeError, ValueError) as exc:
        raise _condition_error(
            condition,
            f"value is invalid for {kind.value}",
        ) from exc


def _normalize_number(
    value_type: TypeEngine[Any], value: FilterScalar
) -> int | float | Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal | str):
        raise TypeError
    try:
        decimal_value = Decimal(value.strip() if isinstance(value, str) else str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError from exc
    if not decimal_value.is_finite():
        raise ValueError
    if decimal_value.is_zero():
        decimal_value = Decimal(0)
    if isinstance(value_type, sa.Integer):
        lower_bound, upper_bound = _integer_bounds(value_type)
        if not Decimal(lower_bound) <= decimal_value <= Decimal(upper_bound):
            raise ValueError
        if decimal_value != decimal_value.to_integral_value():
            raise ValueError
        return int(decimal_value)
    if isinstance(value_type, sa.Float):
        return _normalize_float(value_type, decimal_value)
    if isinstance(value_type, sa.Numeric):
        _validate_numeric_extent(decimal_value)
        # SQLAlchemy otherwise infers an integer literal as BIGINT, even when the
        # compared expression is NUMERIC. Decimal forces the correct bind type.
        return decimal_value
    return decimal_value if isinstance(value, str) else value


def _integer_bounds(type_: sa.Integer) -> tuple[int, int]:
    if isinstance(type_, sa.SmallInteger):
        return POSTGRES_SMALLINT_MIN, POSTGRES_SMALLINT_MAX
    if isinstance(type_, sa.BigInteger):
        return POSTGRES_BIGINT_MIN, POSTGRES_BIGINT_MAX
    return POSTGRES_INTEGER_MIN, POSTGRES_INTEGER_MAX


def _normalize_float(type_: sa.Float, value: Decimal) -> float:
    float_value = float(value)
    if not math.isfinite(float_value) or (float_value == 0 and not value.is_zero()):
        raise ValueError

    if isinstance(type_, sa.REAL) or (
        type_.precision is not None and type_.precision <= 24
    ):
        try:
            packed = struct.pack("!f", float_value)
        except OverflowError as exc:
            raise ValueError from exc
        float32_value = struct.unpack("!f", packed)[0]
        if not math.isfinite(float32_value) or (
            float32_value == 0 and not value.is_zero()
        ):
            raise ValueError
    return float_value


def _validate_numeric_extent(value: Decimal) -> None:
    decimal_tuple = value.as_tuple()
    exponent = decimal_tuple.exponent
    assert isinstance(exponent, int)
    whole_digits = 0 if value.is_zero() else max(value.adjusted() + 1, 0)
    fractional_digits = max(-exponent, 0)
    if (
        whole_digits > POSTGRES_NUMERIC_MAX_WHOLE_DIGITS
        or fractional_digits > POSTGRES_NUMERIC_MAX_FRACTIONAL_DIGITS
    ):
        raise ValueError


def _normalize_temporal(
    value_type: TypeEngine[Any], value: FilterScalar
) -> date | datetime:
    if not isinstance(value, str | date | datetime):
        raise TypeError
    if isinstance(value_type, sa.Date) and not isinstance(value_type, sa.DateTime):
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
    if isinstance(value_type, sa.DateTime) and not value_type.timezone:
        return parsed.replace(tzinfo=None)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _normalize_enum(
    value_type: TypeEngine[Any], value: FilterScalar
) -> NormalizedFilterScalar:
    if not isinstance(value, str):
        raise TypeError
    if "\x00" in value:
        raise ValueError
    if not isinstance(value_type, sa.Enum):
        return value

    if enum_class := value_type.enum_class:
        return enum_class(value)
    if value not in value_type.enums:
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
            return expression.in_(value)
        case FilterOp.NOT_IN:
            assert isinstance(value, list)
            return expression.not_in(value)
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
