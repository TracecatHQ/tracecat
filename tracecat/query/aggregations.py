from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_GROUP_BY_FIELDS = 3
MAX_AGGREGATIONS = 8
POSTGRES_IDENTIFIER_MAX_BYTES = 63
IANA_TIMEZONES = frozenset(available_timezones())
type TimeBucket = Literal["hour", "day", "week", "month"]


class AggFunction(StrEnum):
    """Aggregate functions supported by the shared query compiler."""

    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    MEAN = "mean"
    MEDIAN = "median"
    MIN = "min"
    MAX = "max"


class SortDirection(StrEnum):
    """Sort directions supported by aggregation queries."""

    ASC = "asc"
    DESC = "desc"


class _AggregationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AggSpec(_AggregationModel):
    """One aggregate output requested by a query."""

    function: AggFunction
    field: str | None = Field(default=None, min_length=1)
    alias: str | None = Field(default=None, min_length=1)

    @field_validator("field", "alias")
    @classmethod
    def reject_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("must not contain a NUL character")
        return value

    @model_validator(mode="after")
    def validate_field_and_alias(self) -> Self:
        if self.function is not AggFunction.COUNT and self.field is None:
            raise ValueError(f"{self.function.value} requires a field")
        if self.alias is None:
            if self.field is None:
                self.alias = self.function.value
            else:
                leaf = self.field.rsplit(".", maxsplit=1)[-1]
                self.alias = f"{self.function.value}_{leaf}"
        return self

    @property
    def output_key(self) -> str:
        """Return the validated output key for this aggregate."""
        assert self.alias is not None
        return self.alias


class GroupBySpec(_AggregationModel):
    """One grouping dimension requested by a query."""

    field: str = Field(min_length=1)
    bucket: TimeBucket | None = Field(default=None)
    timezone: str | None = Field(default=None, min_length=1)
    alias: str | None = Field(default=None, min_length=1)

    @field_validator("field", "alias")
    @classmethod
    def reject_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("must not contain a NUL character")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value or value not in IANA_TIMEZONES:
            raise ValueError(f"unknown IANA timezone {value!r}")
        return value

    @model_validator(mode="after")
    def validate_bucket_and_alias(self) -> Self:
        if self.timezone is not None and self.bucket is None:
            raise ValueError("timezone requires a bucket")
        if self.alias is None:
            self.alias = self.field
        return self

    @property
    def output_key(self) -> str:
        """Return the validated output key for this grouping dimension."""
        assert self.alias is not None
        return self.alias


class AggregationSpec(_AggregationModel):
    """Shared group-by, aggregate, having, and ordering query shape."""

    group_by: list[GroupBySpec] = Field(max_length=MAX_GROUP_BY_FIELDS)
    aggs: list[AggSpec] = Field(
        default_factory=lambda: [AggSpec(function=AggFunction.COUNT)],
        min_length=1,
        max_length=MAX_AGGREGATIONS,
    )
    min_count: int | None = Field(default=None, ge=1)
    order_by: str | None = Field(default=None, min_length=1)
    sort: SortDirection | None = Field(default=None)

    @field_validator("group_by", mode="before")
    @classmethod
    def expand_group_by_shorthand(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [
            GroupBySpec(field=item) if isinstance(item, str) else item for item in value
        ]

    @field_validator("aggs", mode="before")
    @classmethod
    def default_null_aggs_to_count(cls, value: Any) -> Any:
        if value is None:
            return [AggSpec(function=AggFunction.COUNT)]
        return value

    @field_validator("order_by")
    @classmethod
    def reject_order_by_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("must not contain a NUL character")
        return value

    @model_validator(mode="after")
    def validate_output_keys(self) -> Self:
        output_keys = [group.output_key for group in self.group_by]
        output_keys.extend(agg.output_key for agg in self.aggs)
        overlong_keys = sorted(
            key
            for key in output_keys
            if len(key.encode("utf-8")) > POSTGRES_IDENTIFIER_MAX_BYTES
        )
        if overlong_keys:
            raise ValueError(
                "Aggregation output keys must not exceed PostgreSQL's 63-byte "
                "identifier limit; overlong keys: "
                + ", ".join(repr(key) for key in overlong_keys)
            )
        duplicates = sorted(
            key for key in set(output_keys) if output_keys.count(key) > 1
        )
        if duplicates:
            raise ValueError(
                "Aggregation output keys must be unique; duplicate keys: "
                + ", ".join(repr(key) for key in duplicates)
            )
        if self.order_by is not None and self.order_by not in output_keys:
            raise ValueError(
                f"order_by {self.order_by!r} does not match an aggregation output key"
            )
        return self
