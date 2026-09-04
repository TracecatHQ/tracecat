from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, assert_never, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import FromClause, TableClause

from tracecat.cases.constants import RESERVED_CASE_FIELDS
from tracecat.cases.enums import (
    CaseFieldKind,
    CasePriority,
    CaseSeverity,
)
from tracecat.db.models import Case
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.query.filters import FilterOp
from tracecat.query.resolver import (
    FieldKind,
    NormalizedFilterValue,
    PredicateFactory,
    ResolvedAggregationField,
    ResolvedField,
)
from tracecat.tables.common import (
    is_internal_column_name,
    sa_type_for_column,
    sanitize_identifier,
    validate_identifier,
)
from tracecat.tables.enums import SqlType

CUSTOM_FIELD_PREFIX = "fields."
CUSTOM_FIELDS_JOIN_KEY = "case_fields"

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
RANKED_ENUM_OPS = ENUM_OPS | frozenset(
    {FilterOp.GT, FilterOp.GTE, FilterOp.LT, FilterOp.LTE}
)
UUID_OPS = frozenset(
    {
        FilterOp.EQ,
        FilterOp.NE,
        FilterOp.IN,
        FilterOp.NOT_IN,
        FilterOp.IS_NULL,
    }
)

_PRIORITY_RANKS: Mapping[CasePriority, int] = {
    CasePriority.LOW: 0,
    CasePriority.MEDIUM: 1,
    CasePriority.HIGH: 2,
    CasePriority.CRITICAL: 3,
}
_SEVERITY_RANKS: Mapping[CaseSeverity, int] = {
    CaseSeverity.INFORMATIONAL: 0,
    CaseSeverity.LOW: 1,
    CaseSeverity.MEDIUM: 2,
    CaseSeverity.HIGH: 3,
    CaseSeverity.CRITICAL: 4,
    CaseSeverity.FATAL: 5,
}
_RANGE_OPS = frozenset({FilterOp.GT, FilterOp.GTE, FilterOp.LT, FilterOp.LTE})
_CUSTOM_FIELD_RESERVED_NAMES = frozenset(RESERVED_CASE_FIELDS)


@dataclass(frozen=True, slots=True)
class CaseFieldJoinSpec:
    """A join required to use a case field in an aggregation."""

    key: str
    target: FromClause
    onclause: ColumnElement[bool]
    is_outer: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedCaseAggregationField:
    """A case aggregation field and its optional backing-table join."""

    field: ResolvedAggregationField
    join: CaseFieldJoinSpec | None = None


@dataclass(frozen=True, slots=True)
class _CustomFieldDefinition:
    """Validated custom-field metadata used to construct SQL expressions."""

    name: str
    physical_name: str
    sql_type: SqlType
    kind: CaseFieldKind | None


_FIXED_FILTER_FIELDS: Mapping[str, ResolvedField] = {
    "status": ResolvedField(
        expr=Case.status,
        kind=FieldKind.ENUM,
        allowed_ops=ENUM_OPS,
    ),
    "assignee_id": ResolvedField(
        expr=Case.assignee_id,
        kind=FieldKind.UUID,
        allowed_ops=UUID_OPS,
    ),
    "created_at": ResolvedField(
        expr=Case.created_at,
        kind=FieldKind.TEMPORAL,
        allowed_ops=TEMPORAL_OPS,
    ),
    "updated_at": ResolvedField(
        expr=Case.updated_at,
        kind=FieldKind.TEMPORAL,
        allowed_ops=TEMPORAL_OPS,
    ),
    "summary": ResolvedField(
        expr=Case.summary,
        kind=FieldKind.TEXT,
        allowed_ops=TEXT_OPS,
    ),
    "description": ResolvedField(
        expr=Case.description,
        kind=FieldKind.TEXT,
        allowed_ops=TEXT_OPS,
    ),
    "case_number": ResolvedField(
        expr=Case.case_number,
        kind=FieldKind.NUMBER,
        allowed_ops=NUMBER_OPS,
    ),
}

_FIXED_AGGREGATION_FIELDS: Mapping[str, ResolvedAggregationField] = {
    "status": ResolvedAggregationField(expr=Case.status, kind=FieldKind.ENUM),
    "priority": ResolvedAggregationField(expr=Case.priority, kind=FieldKind.ENUM),
    "severity": ResolvedAggregationField(expr=Case.severity, kind=FieldKind.ENUM),
    "assignee_id": ResolvedAggregationField(
        expr=Case.assignee_id,
        kind=FieldKind.UUID,
    ),
    "created_at": ResolvedAggregationField(
        expr=Case.created_at,
        kind=FieldKind.TEMPORAL,
    ),
    "updated_at": ResolvedAggregationField(
        expr=Case.updated_at,
        kind=FieldKind.TEMPORAL,
    ),
}


class CaseFieldResolver:
    """Resolve user-facing case field addresses to trusted SQL expressions."""

    def __init__(
        self,
        workspace_id: UUID,
        field_schema: Mapping[str, object],
    ) -> None:
        self._custom_fields = _parse_custom_field_schema(field_schema)
        self._custom_fields_by_physical_name = {
            field.physical_name: field for field in self._custom_fields.values()
        }

        schema_name = f"custom_fields_{WorkspaceUUID.new(workspace_id).short()}"
        columns = [sa.column("case_id", postgresql.UUID(as_uuid=True))]
        columns.extend(
            sa.column(field.physical_name, sa_type_for_column(field.sql_type))
            for field in self._custom_fields.values()
        )
        self._custom_fields_table = sa.table(
            CUSTOM_FIELDS_JOIN_KEY,
            *columns,
            schema=schema_name,
        )
        self._custom_fields_join = CaseFieldJoinSpec(
            key=CUSTOM_FIELDS_JOIN_KEY,
            target=self._custom_fields_table,
            onclause=self._custom_fields_table.c.case_id == Case.id,
        )

    def resolve(self, field: str) -> ResolvedField | None:
        """Return the filter field, or ``None`` when the address is unknown."""
        if field == "priority":
            return _ranked_enum_field(
                field,
                Case.priority,
                _PRIORITY_RANKS,
            )
        if field == "severity":
            return _ranked_enum_field(
                field,
                Case.severity,
                _SEVERITY_RANKS,
            )
        if resolved := _FIXED_FILTER_FIELDS.get(field):
            return resolved
        if custom_field := self._resolve_custom_field_address(field):
            expression, kind, allowed_ops = self._custom_field_expression(custom_field)
            return ResolvedField(
                expr=_custom_field_predicate_factory(
                    self._custom_fields_table,
                    expression,
                ),
                kind=kind,
                allowed_ops=allowed_ops,
                value_type=expression.type,
            )
        return None

    def resolve_aggregation(self, field: str) -> ResolvedCaseAggregationField | None:
        """Return an aggregation field and any join needed to use it."""
        if resolved := _FIXED_AGGREGATION_FIELDS.get(field):
            return ResolvedCaseAggregationField(field=resolved)
        if custom_field := self._resolve_custom_field_address(field):
            expression, kind, _ = self._custom_field_expression(custom_field)
            return ResolvedCaseAggregationField(
                field=ResolvedAggregationField(expr=expression, kind=kind),
                join=self._custom_fields_join,
            )
        return None

    def _resolve_custom_field_address(
        self, field: str
    ) -> _CustomFieldDefinition | None:
        if not field.startswith(CUSTOM_FIELD_PREFIX):
            return None
        name = field.removeprefix(CUSTOM_FIELD_PREFIX)
        if not name or is_internal_column_name(name):
            return None
        try:
            normalized_name = validate_identifier(name)
        except ValueError:
            return None
        if normalized_name in _CUSTOM_FIELD_RESERVED_NAMES:
            return None
        return self._custom_fields.get(
            name
        ) or self._custom_fields_by_physical_name.get(normalized_name)

    def _custom_field_expression(
        self,
        field: _CustomFieldDefinition,
    ) -> tuple[ColumnElement[Any], FieldKind, frozenset[FilterOp]]:
        column = self._custom_fields_table.c[field.physical_name]

        if field.kind is CaseFieldKind.URL:
            if field.sql_type is not SqlType.JSONB:
                raise _invalid_custom_field(
                    field,
                    "URL fields must use JSONB storage",
                )
            return column["url"].as_string(), FieldKind.TEXT, TEXT_OPS
        if field.kind is CaseFieldKind.LONG_TEXT and field.sql_type is not SqlType.TEXT:
            raise _invalid_custom_field(
                field,
                "LONG_TEXT fields must use TEXT storage",
            )

        match field.sql_type:
            case SqlType.TEXT:
                return column, FieldKind.TEXT, TEXT_OPS
            case SqlType.INTEGER | SqlType.NUMERIC:
                return column, FieldKind.NUMBER, NUMBER_OPS
            case SqlType.BOOLEAN:
                return column, FieldKind.BOOLEAN, BOOLEAN_OPS
            case SqlType.DATE | SqlType.TIMESTAMPTZ:
                return column, FieldKind.TEMPORAL, TEMPORAL_OPS
            case SqlType.SELECT:
                return column, FieldKind.ENUM, ENUM_OPS
            case SqlType.JSONB | SqlType.MULTI_SELECT:
                raise _invalid_custom_field(
                    field,
                    f"{field.sql_type.value} fields cannot be used in queries",
                )
            case _ as unreachable:
                assert_never(unreachable)


def _parse_custom_field_schema(
    field_schema: Mapping[str, object],
) -> dict[str, _CustomFieldDefinition]:
    parsed: dict[str, _CustomFieldDefinition] = {}
    for name, metadata in field_schema.items():
        try:
            physical_name = validate_identifier(name)
        except ValueError as exc:
            raise TracecatValidationError(
                f"Case custom field {name!r} has an invalid name"
            ) from exc
        if physical_name in _CUSTOM_FIELD_RESERVED_NAMES or is_internal_column_name(
            physical_name
        ):
            raise TracecatValidationError(
                f"Case custom field {name!r} uses a reserved name"
            )

        if not isinstance(metadata, Mapping):
            raise TracecatValidationError(
                f"Case custom field {name!r} has invalid schema metadata"
            )

        raw_type = metadata.get("type")
        try:
            sql_type = SqlType(raw_type) if isinstance(raw_type, str) else None
        except ValueError as exc:
            raise TracecatValidationError(
                f"Case custom field {name!r} has invalid type {raw_type!r}"
            ) from exc
        if sql_type is None:
            raise TracecatValidationError(
                f"Case custom field {name!r} has invalid type {raw_type!r}"
            )

        raw_kind = metadata.get("kind")
        try:
            kind = CaseFieldKind(raw_kind) if isinstance(raw_kind, str) else None
        except ValueError as exc:
            raise TracecatValidationError(
                f"Case custom field {name!r} has invalid kind {raw_kind!r}"
            ) from exc
        parsed[name] = _CustomFieldDefinition(
            name=name,
            physical_name=sanitize_identifier(name),
            sql_type=sql_type,
            kind=kind,
        )
    return parsed


def _ranked_enum_field[
    EnumT: Enum,
](
    field: str,
    column: InstrumentedAttribute[Any],
    ranks: Mapping[EnumT, int],
) -> ResolvedField:
    def predicate(
        op: FilterOp,
        value: NormalizedFilterValue | None,
    ) -> ColumnElement[bool]:
        if op not in _RANGE_OPS:
            return _compile_predicate(column.expression, op, value)

        if not isinstance(value, Enum):
            raise TracecatValidationError(
                f"Invalid range value {value!r} for case field {field!r}"
            )
        ranked_value = cast(EnumT, value)
        if ranked_value not in ranks:
            raise TracecatValidationError(
                f"Invalid range value {value!r} for case field {field!r}"
            )
        rank_expression = sa.case(
            *((column == member, rank) for member, rank in ranks.items()),
            else_=None,
        )
        return _compile_predicate(rank_expression, op, ranks[ranked_value])

    return ResolvedField(
        expr=predicate,
        kind=FieldKind.ENUM,
        allowed_ops=RANKED_ENUM_OPS,
        value_type=column.type,
    )


def _custom_field_predicate_factory(
    table: TableClause,
    expression: ColumnElement[Any],
) -> PredicateFactory:
    def predicate(
        op: FilterOp,
        value: NormalizedFilterValue | None,
    ) -> ColumnElement[bool]:
        if op is FilterOp.IS_NULL:
            return sa.not_(
                sa.exists(
                    sa.select(1)
                    .select_from(table)
                    .where(
                        table.c.case_id == Case.id,
                        expression.is_not(None),
                    )
                    .correlate(Case)
                )
            )
        return sa.exists(
            sa.select(1)
            .select_from(table)
            .where(
                table.c.case_id == Case.id,
                _compile_predicate(expression, op, value),
            )
            .correlate(Case)
        )

    return predicate


def _compile_predicate(
    expression: ColumnElement[Any],
    op: FilterOp,
    value: NormalizedFilterValue | None,
) -> ColumnElement[bool]:
    match op:
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


def _invalid_custom_field(
    field: _CustomFieldDefinition,
    message: str,
) -> TracecatValidationError:
    return TracecatValidationError(
        f"Case custom field {field.name!r} is invalid: {message}"
    )
