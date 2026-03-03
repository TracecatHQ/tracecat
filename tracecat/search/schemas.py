from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from tracecat import config
from tracecat.core.schemas import Schema


class SearchAggFunction(StrEnum):
    """Supported aggregation functions for search endpoints."""

    COUNT = "count"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    MODE = "mode"
    N_UNIQUE = "n_unique"
    AVG = "avg"

    @classmethod
    def parse(cls, raw: str | SearchAggFunction) -> SearchAggFunction:
        """Parse a user-provided function and normalize aliases."""
        if isinstance(raw, cls):
            agg = raw
        else:
            agg = cls(raw.lower().strip())
        if agg is cls.AVG:
            return cls.MEAN
        return agg


def parse_search_agg_function(raw: str | SearchAggFunction | None) -> SearchAggFunction:
    """Parse and validate aggregation functions used by search endpoints."""
    if raw is None:
        return SearchAggFunction.COUNT

    normalized = raw.strip().lower() if isinstance(raw, str) else raw
    if normalized == "value_counts":
        raise ValueError("Aggregation function 'value_counts' is not supported")

    try:
        return SearchAggFunction.parse(normalized)
    except ValueError as exc:
        raise ValueError(
            f"Unsupported aggregation function '{raw}'. "
            "Expected one of: count, sum, min, max, mean, median, mode, n_unique, avg."
        ) from exc


class SearchAggregationBucket(Schema):
    """A grouped aggregation bucket."""

    key: dict[str, Any]
    value: Any


class SearchAggregationResult(Schema):
    """Canonical aggregation response object."""

    agg: SearchAggFunction
    agg_field: str | None = Field(default=None)
    group_by: list[str] = Field(default_factory=list)
    value: Any
    buckets: list[SearchAggregationBucket] = Field(default_factory=list)
    bucket_limit: int = Field(
        default=config.TRACECAT__LIMIT_AGG_BUCKET_DEFAULT,
        ge=1,
        le=config.TRACECAT__LIMIT_AGG_BUCKET_MAX,
    )
    truncated: bool = Field(default=False)


class SearchAggregationParams(Schema):
    """Shared aggregation request parameters for search endpoints."""

    group_by: list[str] | None = Field(default=None)
    agg: str | None = Field(default=None)
    agg_field: str | None = Field(default=None)
    bucket_limit: int = Field(
        default=config.TRACECAT__LIMIT_AGG_BUCKET_DEFAULT,
        ge=1,
        le=config.TRACECAT__LIMIT_AGG_BUCKET_MAX,
    )
