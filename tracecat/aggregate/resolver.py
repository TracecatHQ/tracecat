"""Field resolver protocol for aggregate queries."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import sqlalchemy as sa

from tracecat.aggregate.schemas import AggFn, FilterOp


@runtime_checkable
class FieldResolver(Protocol):
    """Resource-specific field resolution for aggregate queries.

    Each resource (cases, tables) implements this protocol to map logical
    field names to SQLAlchemy column expressions and produce WHERE clauses
    for filter operations.
    """

    def resolve_field(self, field_name: str) -> sa.ColumnElement[Any]:
        """Map a logical field name to a SQLAlchemy column expression.

        Args:
            field_name: User-facing field name (e.g. 'status', 'dropdown.severity_type').

        Returns:
            SQLAlchemy column or expression for GROUP BY / SELECT.

        Raises:
            ValueError: If the field name is unknown.
        """
        ...

    def resolve_filter(
        self,
        field_name: str,
        op: FilterOp,
        value: Any,
    ) -> sa.ColumnElement[bool]:
        """Build a SQLAlchemy WHERE clause for a filter condition.

        Args:
            field_name: Logical field name.
            op: Filter operator.
            value: Comparison value (None for is_null/is_not_null).

        Returns:
            SQLAlchemy boolean expression.

        Raises:
            ValueError: If the operator is not supported for the field.
        """
        ...

    def validate_agg(self, func: AggFn, field_name: str | None) -> None:
        """Validate that an aggregate function is compatible with a field.

        Args:
            func: Aggregate function.
            field_name: Field to aggregate (None for count(*)).

        Raises:
            ValueError: If the combination is invalid (e.g. sum on a text field).
        """
        ...

    def get_search_clause(self, search_term: str) -> sa.ColumnElement[bool] | None:
        """Return a search clause for text searching.

        Args:
            search_term: Text to search for.

        Returns:
            SQLAlchemy boolean expression, or None if no searchable columns.
        """
        ...

    def get_base_from(self) -> sa.FromClause:
        """Return the FROM clause for the resource."""
        ...

    def get_mandatory_filters(self) -> list[sa.ColumnElement[bool]]:
        """Return filters that are always applied (e.g. workspace scoping)."""
        ...

    def get_field_type(self, field_name: str) -> str:
        """Return the type name for a field (e.g. 'text', 'integer')."""
        ...
