"""Aggregate API request/response schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from tracecat.core.schemas import Schema

# -- Enums --


class AggFn(StrEnum):
    """Supported aggregate functions (Polars naming)."""

    COUNT = "count"
    N_UNIQUE = "n_unique"
    SUM = "sum"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"


class FilterOp(StrEnum):
    """Supported filter operators."""

    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    ICONTAINS = "icontains"
    HAS_ANY = "has_any"
    HAS_ALL = "has_all"


# -- Request models --

_AGG_FN_ALIASES: dict[str, AggFn] = {
    "avg": AggFn.MEAN,
}


class AggExpr(Schema):
    """A single aggregate expression: {func, field?}."""

    func: AggFn
    field: str | None = Field(default=None)

    @field_validator("func", mode="before")
    @classmethod
    def _normalize_func(cls, v: Any) -> Any:
        """Accept 'avg' as alias for 'mean'."""
        if isinstance(v, str) and (alias := _AGG_FN_ALIASES.get(v.lower())):
            return alias
        return v

    @model_validator(mode="after")
    def _validate_field_required(self) -> AggExpr:
        if self.func != AggFn.COUNT and self.field is None:
            raise ValueError(f"field is required for func={self.func!r}")
        return self

    def output_column_name(self) -> str:
        """Compute the deterministic output column name.

        Returns:
            'func' when field is None (e.g. 'count'),
            'func_field' otherwise (e.g. 'mean_case_number').
        """
        if self.field is None:
            return self.func.value
        return f"{self.func.value}_{self.field}"


_VALUELESS_OPS = frozenset({FilterOp.IS_NULL, FilterOp.IS_NOT_NULL})


class FilterClause(Schema):
    """A single filter condition: {field, op, value?}."""

    field: str
    op: FilterOp
    value: Any | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_value(self) -> FilterClause:
        if self.op not in _VALUELESS_OPS and self.value is None:
            raise ValueError(f"value is required for op={self.op!r}")
        return self


class AggregateRequest(Schema):
    """Aggregate query request body."""

    group_by: list[str] = Field(default_factory=list)
    agg: list[AggExpr]
    filter: list[FilterClause] = Field(default_factory=list)
    search: str | None = Field(default=None)
    limit: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def _validate_request(self) -> AggregateRequest:
        if not self.agg:
            raise ValueError("agg must contain at least one expression")

        # Check output column name uniqueness
        names: list[str] = list(self.group_by)
        for expr in self.agg:
            names.append(expr.output_column_name())
        seen: set[str] = set()
        for name in names:
            if name in seen:
                raise ValueError(f"duplicate output column name: {name!r}")
            seen.add(name)

        return self


# -- Response models --


class AggregateResponse(Schema):
    """Aggregate query response."""

    items: list[dict[str, Any]]
