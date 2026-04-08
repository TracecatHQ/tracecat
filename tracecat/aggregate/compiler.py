"""Aggregate query compiler.

Translates an AggregateRequest into a SQLAlchemy SELECT statement
using a resource-specific FieldResolver.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Row

from tracecat.aggregate.resolver import FieldResolver
from tracecat.aggregate.schemas import (
    AggExpr,
    AggFn,
    AggregateRequest,
    AggregateResponse,
)

_SA_AGG_MAP: dict[AggFn, Any] = {
    AggFn.COUNT: None,  # handled specially (with/without field)
    AggFn.N_UNIQUE: None,  # handled specially (count(distinct(col)))
    AggFn.SUM: sa.func.sum,
    AggFn.MEAN: sa.func.avg,  # Polars 'mean' → SQL 'avg'
    AggFn.MIN: sa.func.min,
    AggFn.MAX: sa.func.max,
}


class AggregateCompiler:
    """Compiles AggregateRequest into a SQLAlchemy query using a FieldResolver."""

    def __init__(self, resolver: FieldResolver) -> None:
        self.resolver = resolver

    def compile(self, request: AggregateRequest) -> sa.Select[Any]:
        """Compile the request into a SQLAlchemy Select statement.

        Args:
            request: Validated aggregate request.

        Returns:
            SQLAlchemy Select statement ready for execution.

        Raises:
            ValueError: On invalid field names, incompatible agg/type combos.
        """
        # Validate all agg expressions
        for expr in request.agg:
            self.resolver.validate_agg(expr.func, expr.field)

        # Build GROUP BY columns
        group_cols: list[sa.Label[Any]] = []
        for field_name in request.group_by:
            col = self.resolver.resolve_field(field_name)
            group_cols.append(col.label(field_name))

        # Build aggregate SELECT expressions
        agg_cols: list[sa.Label[Any]] = []
        for expr in request.agg:
            sa_expr = _build_agg_expression(expr, self.resolver)
            agg_cols.append(sa_expr.label(expr.output_column_name()))

        # Build WHERE clause
        where_clauses: list[sa.ColumnElement[bool]] = list(
            self.resolver.get_mandatory_filters()
        )

        for fc in request.filter:
            where_clauses.append(
                self.resolver.resolve_filter(fc.field, fc.op, fc.value)
            )

        if request.search:
            search_clause = self.resolver.get_search_clause(request.search)
            if search_clause is not None:
                where_clauses.append(search_clause)

        # Assemble statement
        select_cols = [*group_cols, *agg_cols]
        stmt = sa.select(*select_cols).select_from(self.resolver.get_base_from())

        if where_clauses:
            stmt = stmt.where(sa.and_(*where_clauses))

        if group_cols:
            stmt = stmt.group_by(*group_cols)

        stmt = stmt.limit(request.limit)

        return stmt

    def format_response(
        self,
        request: AggregateRequest,
        rows: Sequence[Row[Any]],
    ) -> AggregateResponse:
        """Format raw query results into an AggregateResponse.

        Args:
            request: The original request.
            rows: Query result rows.

        Returns:
            AggregateResponse with items.
        """
        # Compute expected column names
        col_names: list[str] = list(request.group_by)
        for expr in request.agg:
            col_names.append(expr.output_column_name())

        items: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {}
            for i, name in enumerate(col_names):
                item[name] = row[i]
            items.append(item)

        return AggregateResponse(items=items)


def _build_agg_expression(
    expr: AggExpr,
    resolver: FieldResolver,
) -> sa.ColumnElement[Any]:
    """Build a SQLAlchemy aggregate expression from an AggExpr."""
    match expr.func:
        case AggFn.COUNT if expr.field is None:
            return sa.func.count()
        case AggFn.COUNT:
            col = resolver.resolve_field(expr.field)  # type: ignore[arg-type]
            return sa.func.count(col)
        case AggFn.N_UNIQUE:
            col = resolver.resolve_field(expr.field)  # type: ignore[arg-type]
            return sa.func.count(sa.distinct(col))
        case _:
            col = resolver.resolve_field(expr.field)  # type: ignore[arg-type]
            sa_fn = _SA_AGG_MAP[expr.func]
            return sa_fn(col)
