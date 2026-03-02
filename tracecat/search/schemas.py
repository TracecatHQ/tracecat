from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from tracecat.core.schemas import Schema

DEFAULT_BUCKET_LIMIT = 100
MAX_BUCKET_LIMIT = 1000


class SearchAggFunction(StrEnum):
    COUNT = "count"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    MODE = "mode"
    N_UNIQUE = "n_unique"


type SearchAggregationValue = int | float | str | bool | None


class SearchAggregationBucket(Schema):
    key: dict[str, Any]
    value: SearchAggregationValue


class SearchAggregationResult(Schema):
    agg: SearchAggFunction
    agg_field: str | None = None
    group_by: list[str] = Field(default_factory=list)
    value: SearchAggregationValue
    buckets: list[SearchAggregationBucket] = Field(default_factory=list)
    bucket_limit: int = Field(default=DEFAULT_BUCKET_LIMIT)
    truncated: bool = False


class SearchRequestValidationError(ValueError):
    def __init__(
        self,
        *,
        code: str,
        field: str,
        message: str,
        value: Any | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        full_detail: dict[str, Any] = {
            "code": code,
            "field": field,
            "message": message,
        }
        if value is not None:
            full_detail["value"] = value
        if detail:
            full_detail.update(detail)
        self.detail = full_detail
        super().__init__(message)


def normalize_agg_function(
    value: str | SearchAggFunction | None,
) -> SearchAggFunction | None:
    if value is None:
        return None
    if isinstance(value, SearchAggFunction):
        return value

    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "avg":
        return SearchAggFunction.MEAN
    if normalized == "value_counts":
        raise SearchRequestValidationError(
            code="unsupported_aggregation",
            field="agg",
            message="Aggregation function 'value_counts' is not supported in this release.",
            value=value,
        )
    try:
        return SearchAggFunction(normalized)
    except ValueError as exc:
        supported = [member.value for member in SearchAggFunction]
        raise SearchRequestValidationError(
            code="invalid_aggregation",
            field="agg",
            message=f"Unsupported aggregation function: {value!r}.",
            value=value,
            detail={"supported": supported + ["avg"]},
        ) from exc
