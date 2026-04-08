"""Cases field resolver for aggregate queries."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import cast, func, or_, select

from tracecat.aggregate.schemas import AggFn, FilterOp
from tracecat.cases.filters import UNASSIGNED_ASSIGNEE_IDENTIFIERS
from tracecat.db.models import (
    Case,
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseDropdownValue,
    CaseTagLink,
)

_DROPDOWN_PREFIX = "dropdown."

_CASE_COLUMN_MAP: dict[str, Any] = {
    "status": Case.status,
    "priority": Case.priority,
    "severity": Case.severity,
    "assignee_id": Case.assignee_id,
    "case_number": Case.case_number,
    "created_at": Case.created_at,
    "updated_at": Case.updated_at,
}

_CASE_TYPE_MAP: dict[str, str] = {
    "status": "text",
    "priority": "text",
    "severity": "text",
    "assignee_id": "text",
    "case_number": "integer",
    "created_at": "timestamptz",
    "updated_at": "timestamptz",
}

_NUMERIC_FIELDS = frozenset({"case_number"})

_FILTER_ONLY_FIELDS = frozenset({"tags"})


def _op_clause(
    col: sa.ColumnElement[Any], op: FilterOp, value: Any
) -> sa.ColumnElement[bool]:
    """Apply a standard filter operator to a column."""
    match op:
        case FilterOp.EQ:
            return col == value
        case FilterOp.NE:
            return col != value
        case FilterOp.IN:
            return col.in_(value)
        case FilterOp.NOT_IN:
            return ~col.in_(value)
        case FilterOp.GT:
            return col > value
        case FilterOp.GTE:
            return col >= value
        case FilterOp.LT:
            return col < value
        case FilterOp.LTE:
            return col <= value
        case FilterOp.IS_NULL:
            return col.is_(None)
        case FilterOp.IS_NOT_NULL:
            return col.isnot(None)
        case FilterOp.ICONTAINS:
            return col.ilike(func.concat("%", value, "%"))
        case _:
            raise ValueError(f"Unsupported operator {op!r} for this field")


class CasesFieldResolver:
    """Field resolver for Case aggregate queries."""

    def __init__(self, workspace_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id

    def resolve_field(self, field_name: str) -> sa.ColumnElement[Any]:
        """Map a logical field name to a SQLAlchemy expression."""
        if field_name in _FILTER_ONLY_FIELDS:
            raise ValueError(
                f"{field_name!r} can only be used in filters, not in group_by or agg"
            )

        if field_name in _CASE_COLUMN_MAP:
            return _CASE_COLUMN_MAP[field_name]

        if field_name.startswith(_DROPDOWN_PREFIX):
            def_ref = field_name[len(_DROPDOWN_PREFIX) :]
            return self._dropdown_subquery(def_ref)

        raise ValueError(f"Unknown case field: {field_name!r}")

    def resolve_filter(
        self,
        field_name: str,
        op: FilterOp,
        value: Any,
    ) -> sa.ColumnElement[bool]:
        """Build a WHERE clause for a filter condition."""
        # Tags: filter-only, supports has_any / has_all
        if field_name == "tags":
            return self._resolve_tag_filter(op, value)

        # Assignee: handle unassigned sentinel
        if field_name == "assignee_id":
            return self._resolve_assignee_filter(op, value)

        # Dropdown fields
        if field_name.startswith(_DROPDOWN_PREFIX):
            def_ref = field_name[len(_DROPDOWN_PREFIX) :]
            return self._resolve_dropdown_filter(def_ref, op, value)

        # Standard fields
        if field_name not in _CASE_COLUMN_MAP:
            raise ValueError(f"Unknown case field: {field_name!r}")

        col = _CASE_COLUMN_MAP[field_name]
        return _op_clause(col, op, value)

    def validate_agg(self, func: AggFn, field_name: str | None) -> None:
        """Validate aggregate function compatibility."""
        if field_name is None:
            return  # count(*) is always valid

        if field_name in _FILTER_ONLY_FIELDS:
            raise ValueError(f"{field_name!r} cannot be used in aggregate expressions")

        if func in (AggFn.SUM, AggFn.MEAN) and field_name not in _NUMERIC_FIELDS:
            raise ValueError(
                f"{func.value} is not supported for field {field_name!r} "
                f"(only numeric fields: {sorted(_NUMERIC_FIELDS)})"
            )

    def get_search_clause(self, search_term: str) -> sa.ColumnElement[bool] | None:
        """Return ILIKE search on summary, description, and short_id."""
        if len(search_term) > 1000:
            raise ValueError("Search term cannot exceed 1000 characters")
        if "\x00" in search_term:
            raise ValueError("Search term cannot contain null bytes")

        search_pattern = sa.func.concat("%", search_term, "%")
        short_id_expr = sa.func.concat(
            "CASE-", sa.func.lpad(cast(Case.case_number, sa.String), 4, "0")
        )
        return or_(
            Case.summary.ilike(search_pattern),
            Case.description.ilike(search_pattern),
            short_id_expr.ilike(search_pattern),
        )

    def get_base_from(self) -> sa.FromClause:
        """Return the Case table as FROM clause."""
        return Case.__table__

    def get_mandatory_filters(self) -> list[sa.ColumnElement[bool]]:
        """Return workspace scoping filter."""
        return [Case.workspace_id == self.workspace_id]

    def get_field_type(self, field_name: str) -> str:
        """Return the type name for a field."""
        if field_name in _CASE_TYPE_MAP:
            return _CASE_TYPE_MAP[field_name]
        if field_name.startswith(_DROPDOWN_PREFIX):
            return "text"
        raise ValueError(f"Unknown case field: {field_name!r}")

    # -- Private helpers --

    def _dropdown_subquery(self, def_ref: str) -> sa.ScalarSelect[Any]:
        """Build a correlated scalar subquery for a dropdown field value."""
        return (
            select(CaseDropdownOption.ref)
            .join(
                CaseDropdownValue,
                CaseDropdownValue.option_id == CaseDropdownOption.id,
            )
            .join(
                CaseDropdownDefinition,
                CaseDropdownValue.definition_id == CaseDropdownDefinition.id,
            )
            .where(
                CaseDropdownDefinition.ref == def_ref,
                CaseDropdownValue.case_id == Case.id,
            )
            .correlate(Case)
            .scalar_subquery()
        )

    def _resolve_tag_filter(self, op: FilterOp, value: Any) -> sa.ColumnElement[bool]:
        """Resolve tag filter with has_any/has_all semantics."""
        if op == FilterOp.HAS_ANY:
            tag_ids = [uuid.UUID(v) if isinstance(v, str) else v for v in value]
            return Case.id.in_(
                select(CaseTagLink.case_id).where(CaseTagLink.tag_id.in_(tag_ids))
            )
        if op == FilterOp.HAS_ALL:
            tag_ids = [uuid.UUID(v) if isinstance(v, str) else v for v in value]
            return sa.and_(
                *[
                    Case.id.in_(
                        select(CaseTagLink.case_id).where(CaseTagLink.tag_id == tid)
                    )
                    for tid in tag_ids
                ]
            )
        raise ValueError(f"tags only supports has_any/has_all operators, got {op!r}")

    def _resolve_assignee_filter(
        self, op: FilterOp, value: Any
    ) -> sa.ColumnElement[bool]:
        """Resolve assignee filter, handling the 'unassigned' sentinel."""
        col = Case.assignee_id

        # Handle unassigned sentinel for eq/in operators
        if op == FilterOp.EQ:
            if (
                isinstance(value, str)
                and value.lower() in UNASSIGNED_ASSIGNEE_IDENTIFIERS
            ):
                return col.is_(None)
            return col == value

        if op == FilterOp.IN:
            if isinstance(value, list):
                unassigned = [
                    v
                    for v in value
                    if isinstance(v, str)
                    and v.lower() in UNASSIGNED_ASSIGNEE_IDENTIFIERS
                ]
                regular = [v for v in value if v not in unassigned]
                conditions: list[sa.ColumnElement[bool]] = []
                if regular:
                    conditions.append(col.in_(regular))
                if unassigned:
                    conditions.append(col.is_(None))
                if not conditions:
                    return sa.false()
                return conditions[0] if len(conditions) == 1 else or_(*conditions)
            return col.in_(value)

        return _op_clause(_CASE_COLUMN_MAP["assignee_id"], op, value)

    def _resolve_dropdown_filter(
        self, def_ref: str, op: FilterOp, value: Any
    ) -> sa.ColumnElement[bool]:
        """Resolve dropdown field filter via subquery."""
        if op == FilterOp.EQ:
            return Case.id.in_(
                select(CaseDropdownValue.case_id)
                .join(
                    CaseDropdownDefinition,
                    CaseDropdownValue.definition_id == CaseDropdownDefinition.id,
                )
                .join(
                    CaseDropdownOption,
                    CaseDropdownValue.option_id == CaseDropdownOption.id,
                )
                .where(
                    CaseDropdownDefinition.ref == def_ref,
                    CaseDropdownOption.ref == value,
                )
            )
        if op == FilterOp.IN:
            return Case.id.in_(
                select(CaseDropdownValue.case_id)
                .join(
                    CaseDropdownDefinition,
                    CaseDropdownValue.definition_id == CaseDropdownDefinition.id,
                )
                .join(
                    CaseDropdownOption,
                    CaseDropdownValue.option_id == CaseDropdownOption.id,
                )
                .where(
                    CaseDropdownDefinition.ref == def_ref,
                    CaseDropdownOption.ref.in_(value),
                )
            )
        if op == FilterOp.NE:
            return ~Case.id.in_(
                select(CaseDropdownValue.case_id)
                .join(
                    CaseDropdownDefinition,
                    CaseDropdownValue.definition_id == CaseDropdownDefinition.id,
                )
                .join(
                    CaseDropdownOption,
                    CaseDropdownValue.option_id == CaseDropdownOption.id,
                )
                .where(
                    CaseDropdownDefinition.ref == def_ref,
                    CaseDropdownOption.ref == value,
                )
            )
        if op == FilterOp.NOT_IN:
            return ~Case.id.in_(
                select(CaseDropdownValue.case_id)
                .join(
                    CaseDropdownDefinition,
                    CaseDropdownValue.definition_id == CaseDropdownDefinition.id,
                )
                .join(
                    CaseDropdownOption,
                    CaseDropdownValue.option_id == CaseDropdownOption.id,
                )
                .where(
                    CaseDropdownDefinition.ref == def_ref,
                    CaseDropdownOption.ref.in_(value),
                )
            )
        if op == FilterOp.IS_NULL:
            return ~Case.id.in_(
                select(CaseDropdownValue.case_id)
                .join(
                    CaseDropdownDefinition,
                    CaseDropdownValue.definition_id == CaseDropdownDefinition.id,
                )
                .where(CaseDropdownDefinition.ref == def_ref)
            )
        if op == FilterOp.IS_NOT_NULL:
            return Case.id.in_(
                select(CaseDropdownValue.case_id)
                .join(
                    CaseDropdownDefinition,
                    CaseDropdownValue.definition_id == CaseDropdownDefinition.id,
                )
                .where(CaseDropdownDefinition.ref == def_ref)
            )
        raise ValueError(
            f"Unsupported operator {op!r} for dropdown field dropdown.{def_ref}"
        )
