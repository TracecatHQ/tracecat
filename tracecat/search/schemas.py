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
