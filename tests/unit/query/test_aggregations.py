from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from tracecat.exceptions import TracecatValidationError
from tracecat.query.aggregations import (
    MAX_AGGREGATIONS,
    MAX_GROUP_BY_FIELDS,
    AggFunction,
    AggregationSpec,
    AggSpec,
    GroupBySpec,
    SortDirection,
    TimeBucket,
)
from tracecat.query.compiler import compile_aggregation
from tracecat.query.resolver import FieldKind, ResolvedAggregationField


def _sql(statement: Select[Any]) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _base_statement() -> Select[Any]:
    return (
        sa.select(sa.literal_column("seed"))
        .select_from(sa.table("events", sa.column("tenant_id", sa.String())))
        .where(sa.column("tenant_id", sa.String()) == "workspace-1")
    )


def _compile(
    spec: AggregationSpec,
    fields: Mapping[str, ResolvedAggregationField],
    *,
    entity_id: sa.ColumnElement[Any] | None = None,
    limit: int = 100,
) -> str:
    return _sql(
        compile_aggregation(
            _base_statement(),
            spec,
            fields,
            limit=limit,
            entity_id=entity_id,
        )
    )


def _field(
    name: str,
    type_: sa.types.TypeEngine[Any],
    kind: FieldKind,
    *,
    is_multi_valued: bool = False,
) -> ResolvedAggregationField:
    return ResolvedAggregationField(
        expr=sa.column(name, type_),
        kind=kind,
        is_multi_valued=is_multi_valued,
    )


def test_agg_function_values_are_stable() -> None:
    assert [function.value for function in AggFunction] == [
        "count",
        "count_distinct",
        "sum",
        "mean",
        "median",
        "min",
        "max",
    ]


@pytest.mark.parametrize(
    "function",
    [
        AggFunction.COUNT_DISTINCT,
        AggFunction.SUM,
        AggFunction.MEAN,
        AggFunction.MEDIAN,
        AggFunction.MIN,
        AggFunction.MAX,
    ],
)
def test_only_count_allows_an_omitted_field(function: AggFunction) -> None:
    with pytest.raises(ValidationError, match="requires a field"):
        AggSpec(function=function)


def test_aggregate_default_aliases() -> None:
    assert AggSpec(function=AggFunction.COUNT).output_key == "count"
    assert (
        AggSpec(function=AggFunction.COUNT, field="fields.region").output_key
        == "count_region"
    )
    assert (
        AggSpec(function=AggFunction.SUM, field="fields.bytes").output_key
        == "sum_bytes"
    )


def test_group_by_defaults_alias_to_full_field_address() -> None:
    group = GroupBySpec(field="fields.region")

    assert group.output_key == "fields.region"


def test_group_by_validates_timezone_and_bucket_relationship() -> None:
    assert (
        GroupBySpec(
            field="created_at", bucket="day", timezone="America/New_York"
        ).timezone
        == "America/New_York"
    )

    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        GroupBySpec(field="created_at", bucket="day", timezone="Mars/Olympus")
    with pytest.raises(ValidationError, match="timezone requires a bucket"):
        GroupBySpec(field="created_at", timezone="UTC")


def test_group_by_rejects_unknown_bucket() -> None:
    with pytest.raises(ValidationError):
        GroupBySpec.model_validate({"field": "created_at", "bucket": "minute"})


def test_aggregation_spec_expands_bare_group_by_strings() -> None:
    spec = AggregationSpec.model_validate(
        {
            "group_by": ["status", {"field": "created_at", "bucket": "day"}],
        }
    )

    assert spec.group_by == [
        GroupBySpec(field="status"),
        GroupBySpec(field="created_at", bucket="day"),
    ]
    assert spec.aggs == [AggSpec(function=AggFunction.COUNT)]


def test_aggregation_spec_treats_null_aggs_as_default_count() -> None:
    spec = AggregationSpec.model_validate({"group_by": [], "aggs": None})

    assert spec.aggs == [AggSpec(function=AggFunction.COUNT)]


def test_aggregation_spec_enforces_structural_limits() -> None:
    with pytest.raises(ValidationError):
        AggregationSpec(
            group_by=[GroupBySpec(field=f"field_{index}") for index in range(4)]
        )
    with pytest.raises(ValidationError):
        AggregationSpec(
            group_by=[],
            aggs=[
                AggSpec(function=AggFunction.COUNT, alias=f"count_{index}")
                for index in range(9)
            ],
        )

    assert MAX_GROUP_BY_FIELDS == 3
    assert MAX_AGGREGATIONS == 8


@pytest.mark.parametrize(
    "data",
    [
        {
            "group_by": [
                {"field": "status", "alias": "dimension"},
                {"field": "priority", "alias": "dimension"},
            ]
        },
        {
            "group_by": [{"field": "status", "alias": "count"}],
            "aggs": [{"function": "count"}],
        },
        {
            "group_by": [],
            "aggs": [
                {"function": "count", "alias": "total"},
                {"function": "count", "field": "status", "alias": "total"},
            ],
        },
    ],
)
def test_aggregation_spec_rejects_output_key_collisions(
    data: Mapping[str, object],
) -> None:
    with pytest.raises(ValidationError, match="output keys must be unique"):
        AggregationSpec.model_validate(data)


def test_aggregation_spec_rejects_unknown_order_key() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        AggregationSpec(group_by=[GroupBySpec(field="status")], order_by="missing")


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (AggSpec, {"function": "count", "alias": "bad\x00alias"}),
        (GroupBySpec, {"field": "bad\x00field"}),
        (AggregationSpec, {"group_by": [], "order_by": "bad\x00key"}),
    ],
)
def test_models_reject_nul_in_sql_labels_or_addresses(
    model: type[BaseModel],
    data: Mapping[str, object],
) -> None:
    with pytest.raises(ValidationError, match="NUL"):
        model.model_validate(data)


def test_compiles_plain_group_and_count() -> None:
    spec = AggregationSpec(group_by=[GroupBySpec(field="priority")])
    sql = _compile(
        spec,
        {"priority": _field("priority", sa.String(), FieldKind.TEXT)},
        limit=25,
    )

    assert "SELECT left(priority, 256) AS priority, count(*) AS count" in sql
    assert "FROM events" in sql
    assert "WHERE tenant_id = 'workspace-1'" in sql
    assert "GROUP BY left(priority, 256)" in sql
    assert "ORDER BY count DESC NULLS LAST, priority DESC NULLS LAST" in sql
    assert "LIMIT 26" in sql


@pytest.mark.parametrize("bucket", ["hour", "day", "week", "month"])
def test_compiles_timestamptz_buckets_with_timezone(bucket: TimeBucket) -> None:
    spec = AggregationSpec(
        group_by=[
            GroupBySpec(
                field="created_at",
                bucket=bucket,
                timezone="America/New_York",
            )
        ]
    )
    sql = _compile(
        spec,
        {
            "created_at": _field(
                "created_at", sa.DateTime(timezone=True), FieldKind.TEMPORAL
            )
        },
    )

    assert (
        f"date_trunc('{bucket}', created_at, 'America/New_York') AS created_at" in sql
    )
    assert "ORDER BY created_at ASC NULLS LAST" in sql


def test_timestamptz_bucket_defaults_to_utc() -> None:
    spec = AggregationSpec(group_by=[GroupBySpec(field="created_at", bucket="day")])
    sql = _compile(
        spec,
        {
            "created_at": _field(
                "created_at", sa.DateTime(timezone=True), FieldKind.TEMPORAL
            )
        },
    )

    assert "date_trunc('day', created_at, 'UTC')" in sql


def test_compiles_date_bucket_without_session_timezone_dependency() -> None:
    spec = AggregationSpec(group_by=[GroupBySpec(field="event_date", bucket="month")])
    sql = _compile(
        spec,
        {"event_date": _field("event_date", sa.Date(), FieldKind.TEMPORAL)},
    )

    assert (
        "CAST(date_trunc('month', CAST(event_date AS TIMESTAMP WITHOUT TIME ZONE)) "
        "AS DATE) AS event_date" in sql
    )


def test_rejects_timezone_on_date_bucket() -> None:
    spec = AggregationSpec(
        group_by=[GroupBySpec(field="event_date", bucket="day", timezone="UTC")]
    )

    with pytest.raises(TracecatValidationError, match="timezone.*DATE"):
        _compile(
            spec,
            {"event_date": _field("event_date", sa.Date(), FieldKind.TEMPORAL)},
        )


@pytest.mark.parametrize(
    ("spec", "field", "message"),
    [
        (
            AggregationSpec(group_by=[GroupBySpec(field="created_at")]),
            _field("created_at", sa.DateTime(timezone=True), FieldKind.TEMPORAL),
            "require a bucket",
        ),
        (
            AggregationSpec(group_by=[GroupBySpec(field="status", bucket="day")]),
            _field("status", sa.String(), FieldKind.TEXT),
            "bucket is not allowed",
        ),
        (
            AggregationSpec(group_by=[GroupBySpec(field="created_at", bucket="day")]),
            _field("created_at", sa.DateTime(timezone=False), FieldKind.TEMPORAL),
            "DATE or TIMESTAMPTZ",
        ),
    ],
)
def test_rejects_invalid_group_bucket_combinations(
    spec: AggregationSpec,
    field: ResolvedAggregationField,
    message: str,
) -> None:
    with pytest.raises(TracecatValidationError, match=message):
        _compile(spec, {spec.group_by[0].field: field})


def test_text_and_select_groups_are_bounded_but_native_enums_are_not() -> None:
    native_status = sa.Enum("OPEN", "CLOSED", name="status")
    spec = AggregationSpec(
        group_by=[
            GroupBySpec(field="message"),
            GroupBySpec(field="category"),
            GroupBySpec(field="status"),
        ]
    )
    sql = _compile(
        spec,
        {
            "message": _field("message", sa.Text(), FieldKind.TEXT),
            "category": _field("category", sa.String(), FieldKind.ENUM),
            "status": _field("status", native_status, FieldKind.ENUM),
        },
    )

    assert "left(message, 256) AS message" in sql
    assert "left(category, 256) AS category" in sql
    assert "left(status" not in sql
    assert "status AS status" in sql


@pytest.mark.parametrize(
    ("function", "column_type", "kind", "allowed"),
    [
        (function, column_type, kind, function in allowed_functions)
        for column_type, kind, allowed_functions in [
            (
                sa.Text(),
                FieldKind.TEXT,
                {
                    AggFunction.COUNT,
                    AggFunction.COUNT_DISTINCT,
                    AggFunction.MIN,
                    AggFunction.MAX,
                },
            ),
            (sa.BigInteger(), FieldKind.NUMBER, set(AggFunction)),
            (sa.Numeric(), FieldKind.NUMBER, set(AggFunction)),
            (
                sa.Boolean(),
                FieldKind.BOOLEAN,
                {AggFunction.COUNT, AggFunction.COUNT_DISTINCT},
            ),
            (
                sa.Date(),
                FieldKind.TEMPORAL,
                {
                    AggFunction.COUNT,
                    AggFunction.COUNT_DISTINCT,
                    AggFunction.MIN,
                    AggFunction.MAX,
                },
            ),
            (
                sa.DateTime(timezone=True),
                FieldKind.TEMPORAL,
                {
                    AggFunction.COUNT,
                    AggFunction.COUNT_DISTINCT,
                    AggFunction.MIN,
                    AggFunction.MAX,
                },
            ),
            (
                sa.String(),
                FieldKind.ENUM,
                {AggFunction.COUNT, AggFunction.COUNT_DISTINCT},
            ),
            (
                sa.Uuid(),
                FieldKind.UUID,
                {AggFunction.COUNT, AggFunction.COUNT_DISTINCT},
            ),
            (sa.String(), FieldKind.TAG, {AggFunction.COUNT}),
        ]
        for function in AggFunction
    ],
)
def test_aggregate_function_type_matrix(
    function: AggFunction,
    column_type: sa.types.TypeEngine[Any],
    kind: FieldKind,
    allowed: bool,
) -> None:
    spec = AggregationSpec(
        group_by=[],
        aggs=[AggSpec(function=function, field="value")],
    )
    if not allowed:
        with pytest.raises(TracecatValidationError, match="not allowed"):
            _compile(spec, {"value": _field("value", column_type, kind)})
        return

    assert " AS " in _compile(spec, {"value": _field("value", column_type, kind)})


@pytest.mark.parametrize(
    ("function", "column_type", "sql_fragment"),
    [
        (AggFunction.SUM, sa.BigInteger(), "CAST(sum(value) AS BIGINT)"),
        (AggFunction.SUM, sa.Numeric(), "CAST(sum(value) AS DOUBLE PRECISION)"),
        (AggFunction.MEAN, sa.BigInteger(), "CAST(avg(value) AS DOUBLE PRECISION)"),
        (AggFunction.MEAN, sa.Numeric(), "CAST(avg(value) AS DOUBLE PRECISION)"),
        (
            AggFunction.MEDIAN,
            sa.BigInteger(),
            "CAST(percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS DOUBLE PRECISION)",
        ),
        (
            AggFunction.MEDIAN,
            sa.Numeric(),
            "CAST(percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS DOUBLE PRECISION)",
        ),
        (AggFunction.MIN, sa.Numeric(), "CAST(min(value) AS DOUBLE PRECISION)"),
        (AggFunction.MAX, sa.Numeric(), "CAST(max(value) AS DOUBLE PRECISION)"),
    ],
)
def test_numeric_aggregate_output_casts(
    function: AggFunction,
    column_type: sa.types.TypeEngine[Any],
    sql_fragment: str,
) -> None:
    spec = AggregationSpec(
        group_by=[],
        aggs=[AggSpec(function=function, field="value")],
    )

    assert sql_fragment in _compile(
        spec, {"value": _field("value", column_type, FieldKind.NUMBER)}
    )


def test_count_field_counts_non_null_values() -> None:
    spec = AggregationSpec(
        group_by=[],
        aggs=[AggSpec(function=AggFunction.COUNT, field="value")],
    )
    sql = _compile(spec, {"value": _field("value", sa.String(), FieldKind.TEXT)})

    assert "count(value) AS count_value" in sql


def test_multi_valued_group_rewrites_all_row_counts_and_having() -> None:
    spec = AggregationSpec(
        group_by=[GroupBySpec(field="tags")],
        aggs=[
            AggSpec(function=AggFunction.COUNT),
            AggSpec(function=AggFunction.COUNT, field="summary"),
        ],
        min_count=3,
    )
    sql = _compile(
        spec,
        {
            "tags": _field("tag_ref", sa.String(), FieldKind.TAG, is_multi_valued=True),
            "summary": _field("summary", sa.String(), FieldKind.TEXT),
        },
        entity_id=sa.column("case_id", sa.Uuid()),
    )

    assert "count(DISTINCT case_id) AS count" in sql
    assert (
        "count(DISTINCT case_id) FILTER (WHERE summary IS NOT NULL) AS count_summary"
        in sql
    )
    assert "HAVING count(DISTINCT case_id) >= 3" in sql


@pytest.mark.parametrize(
    "function", [AggFunction.SUM, AggFunction.MEAN, AggFunction.MEDIAN]
)
def test_multi_valued_group_rejects_additive_aggregates(
    function: AggFunction,
) -> None:
    spec = AggregationSpec(
        group_by=[GroupBySpec(field="tags")],
        aggs=[AggSpec(function=function, field="value")],
    )

    with pytest.raises(TracecatValidationError, match="multi-valued"):
        _compile(
            spec,
            {
                "tags": _field(
                    "tag_ref", sa.String(), FieldKind.TAG, is_multi_valued=True
                ),
                "value": _field("value", sa.BigInteger(), FieldKind.NUMBER),
            },
            entity_id=sa.column("case_id", sa.Uuid()),
        )


def test_multi_valued_group_requires_entity_id() -> None:
    spec = AggregationSpec(group_by=[GroupBySpec(field="tags")])

    with pytest.raises(TracecatValidationError, match="requires an entity id"):
        _compile(
            spec,
            {
                "tags": _field(
                    "tag_ref", sa.String(), FieldKind.TAG, is_multi_valued=True
                )
            },
        )


def test_default_ordering_uses_first_bucket_even_when_not_first_group() -> None:
    spec = AggregationSpec(
        group_by=[
            GroupBySpec(field="status"),
            GroupBySpec(field="created_at", bucket="day", alias="day"),
        ]
    )
    sql = _compile(
        spec,
        {
            "status": _field("status", sa.String(), FieldKind.TEXT),
            "created_at": _field(
                "created_at", sa.DateTime(timezone=True), FieldKind.TEMPORAL
            ),
        },
    )

    assert "ORDER BY day ASC NULLS LAST, status ASC NULLS LAST" in sql


def test_explicit_order_and_sort_use_labels_with_group_tiebreakers() -> None:
    spec = AggregationSpec(
        group_by=[GroupBySpec(field="status")],
        aggs=[AggSpec(function=AggFunction.COUNT, alias="total")],
        order_by="status",
        sort=SortDirection.DESC,
    )
    sql = _compile(
        spec,
        {"status": _field("status", sa.String(), FieldKind.TEXT)},
    )

    assert "ORDER BY status DESC NULLS LAST" in sql
    assert "ORDER BY left(" not in sql


def test_empty_group_by_compiles_grand_total() -> None:
    spec = AggregationSpec(group_by=[])
    sql = _compile(spec, {})

    assert "SELECT count(*) AS count" in sql
    assert "GROUP BY" not in sql
    assert "ORDER BY count DESC NULLS LAST" in sql


def test_unknown_aggregation_field_is_a_semantic_error() -> None:
    spec = AggregationSpec(group_by=[GroupBySpec(field="missing")])

    with pytest.raises(TracecatValidationError, match="Unknown.*'missing'"):
        _compile(spec, {})


def test_limit_must_be_positive() -> None:
    with pytest.raises(TracecatValidationError, match="at least 1"):
        _compile(AggregationSpec(group_by=[]), {}, limit=0)
