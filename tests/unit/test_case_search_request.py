from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.constants import (
    MAX_PAYLOAD_FIELDS,
    MAX_PAYLOAD_PATH_KEYS,
    MAX_PAYLOAD_PATHS,
)
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.query import CaseFieldResolver, case_payload_projection
from tracecat.cases.search_adapters import (
    iter_case_filter_conditions,
    legacy_case_filters,
)
from tracecat.cases.search_schemas import CaseQuery, CaseSearchRequest
from tracecat.cases.search_service import CaseSearchService
from tracecat.db.models import Case
from tracecat.exceptions import TracecatValidationError
from tracecat.query.compiler import compile_filter
from tracecat.query.filters import (
    MAX_FILTER_CONDITIONS,
    MAX_FILTER_VALUES,
    AndClause,
    Condition,
    FilterOp,
)
from tracecat.tables.enums import SqlType

WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000001")
CASE_ID = UUID("00000000-0000-4000-8000-000000000002")


def test_request_compiles_nested_mixed_builtin_and_custom_query() -> None:
    request = CaseSearchRequest.model_validate(
        {
            "filters": {
                "or": [
                    {"field": "priority", "op": "eq", "value": "critical"},
                    {
                        "and": [
                            {"field": "fields.risk_score", "op": "gte", "value": 80},
                            {"not": {"field": "status", "op": "eq", "value": "closed"}},
                        ]
                    },
                ]
            }
        }
    )
    assert request.filters is not None
    resolver = CaseFieldResolver(WORKSPACE_ID, {"risk_score": {"type": "NUMERIC"}})
    statement = sa.select(Case.id).where(
        Case.workspace_id == WORKSPACE_ID, compile_filter(request.filters, resolver)
    )
    compiled = statement.compile(dialect=postgresql.dialect())

    assert " OR " in str(compiled)
    assert " AND " in str(compiled)
    assert "EXISTS" in str(compiled)
    assert WORKSPACE_ID in compiled.params.values()
    assert CasePriority.CRITICAL in compiled.params.values()
    assert 80 in compiled.params.values()
    assert CaseStatus.CLOSED in compiled.params.values()
    assert request.model_dump(mode="json")["filters"] == {
        "or": [
            {"field": "priority", "op": "eq", "value": "critical"},
            {
                "and": [
                    {"field": "fields.risk_score", "op": "gte", "value": 80},
                    {"not": {"field": "status", "op": "eq", "value": "closed"}},
                ]
            },
        ]
    }


@pytest.mark.parametrize("include_details", [False, True])
def test_returned_details_do_not_change_matching(include_details: bool) -> None:
    query = CaseQuery(
        case_id=CASE_ID,
        filters=Condition(field="summary", op=FilterOp.CONTAINS, value="synthetic"),
    )
    request = CaseSearchRequest(
        **query.model_dump(),
        limit=7,
        cursor="synthetic-cursor",
        reverse=True,
        order_by="priority",
        sort="asc",
        include_rows=include_details,
        field_ids=["risk_score"] if include_details else None,
        include_durations=include_details,
        include_payload=include_details,
        include_count=include_details,
    )

    assert request.filters == query.filters
    assert request.case_id == query.case_id
    assert request.hierarchy == "all"
    assert request.limit == 7


def test_request_includes_count_by_default() -> None:
    assert CaseSearchRequest().include_count is True
    assert CaseSearchRequest(include_count=False).include_count is False


@pytest.mark.parametrize("order_by", ["id", "short_id"])
def test_request_preserves_implemented_identity_sort_options(order_by: str) -> None:
    request = CaseSearchRequest.model_validate({"order_by": order_by})
    assert request.order_by == order_by


@pytest.mark.parametrize("field", ["workspace_id", "organization_id", "scope"])
def test_query_rejects_authorization_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CaseQuery.model_validate({field: str(WORKSPACE_ID)})


@pytest.mark.parametrize("field", ["search_term", "short_id"])
def test_query_rejects_nul_and_overlong_text(field: str) -> None:
    for value in ("synthetic\x00text", "x" * 1001):
        with pytest.raises(ValidationError):
            CaseQuery.model_validate({field: value})


@pytest.mark.parametrize("field", ["case_id", "parent_id"])
def test_query_rejects_invalid_uuid(field: str) -> None:
    with pytest.raises(ValidationError):
        CaseQuery.model_validate({field: "invalid-uuid"})


def test_query_rejects_incompatible_hierarchy_constraints() -> None:
    with pytest.raises(ValidationError, match="parent_id cannot be combined"):
        CaseQuery(parent_id=CASE_ID, hierarchy="top_level")


@pytest.mark.parametrize("limit", [0, config.TRACECAT__LIMIT_CURSOR_MAX + 1])
def test_request_retains_cursor_pagination_limits(limit: int) -> None:
    with pytest.raises(ValidationError):
        CaseSearchRequest(limit=limit)


@pytest.mark.parametrize("field", ["order_by", "sort", "hierarchy"])
def test_request_rejects_unsupported_finite_options(field: str) -> None:
    with pytest.raises(ValidationError):
        CaseSearchRequest.model_validate({field: "unsupported"})


def test_query_retains_shared_filter_and_projection_limits() -> None:
    with pytest.raises(ValidationError, match="maximum condition count"):
        CaseQuery.model_validate(
            {
                "filters": {
                    "or": [
                        {"field": "summary", "op": "eq", "value": "synthetic"}
                        for _ in range(MAX_FILTER_CONDITIONS + 1)
                    ]
                }
            }
        )
    with pytest.raises(ValidationError, match="maximum value count"):
        CaseQuery.model_validate(
            {
                "filters": {
                    "field": "tags",
                    "op": "in",
                    "value": ["tag"] * (MAX_FILTER_VALUES + 1),
                }
            }
        )

    fields = [f"synthetic_field_{index}" for index in range(MAX_PAYLOAD_FIELDS)]
    paths = [["synthetic"] * MAX_PAYLOAD_PATH_KEYS] * MAX_PAYLOAD_PATHS
    request = CaseSearchRequest(payload_fields=fields, payload_paths=paths)
    assert request.payload_fields == fields
    assert request.payload_paths == paths
    for selectors in (
        {"payload_fields": [*fields, "one_too_many"]},
        {"payload_paths": [*paths, ["one_too_many"]]},
        {"payload_paths": [[]]},
        {"payload_paths": [["synthetic"] * (MAX_PAYLOAD_PATH_KEYS + 1)]},
    ):
        with pytest.raises(ValidationError):
            CaseSearchRequest.model_validate(selectors)
    with pytest.raises(TracecatValidationError, match="payload fields"):
        case_payload_projection([*fields, "one_too_many"])


@pytest.mark.parametrize(
    ("sql_type", "value"),
    [
        (SqlType.INTEGER, 80),
        (SqlType.NUMERIC, "80.5"),
        (SqlType.BOOLEAN, True),
        (SqlType.TEXT, "synthetic"),
        (SqlType.DATE, "2026-01-01"),
        (SqlType.TIMESTAMPTZ, "2026-01-01T00:00:00Z"),
        (SqlType.SELECT, "synthetic-option"),
    ],
)
def test_query_uses_custom_fields_physical_types(
    sql_type: SqlType, value: str | int | bool
) -> None:
    query = CaseQuery(
        filters=Condition(field="fields.synthetic", op=FilterOp.EQ, value=value)
    )
    assert query.filters is not None
    resolver = CaseFieldResolver(WORKSPACE_ID, {"synthetic": {"type": sql_type.value}})
    assert compile_filter(query.filters, resolver) is not None


@pytest.mark.parametrize("field", ["unknown", "fields.unknown", "durations.total"])
def test_query_compiler_rejects_unknown_or_unsupported_leaves(field: str) -> None:
    query = CaseQuery(filters=Condition(field=field, op=FilterOp.EQ, value="synthetic"))
    assert query.filters is not None
    with pytest.raises(TracecatValidationError, match="unknown field"):
        compile_filter(query.filters, CaseFieldResolver(WORKSPACE_ID, {}))


def test_metadata_walk_visits_negated_conditions() -> None:
    query = CaseQuery.model_validate(
        {
            "filters": {
                "and": [
                    {"field": "tags", "op": "in", "value": ["synthetic-tag"]},
                    {
                        "or": [
                            {"field": "fields.risk_score", "op": "gte", "value": 80},
                            {"not": {"field": "dropdowns.verdict", "op": "is_null"}},
                        ]
                    },
                ]
            }
        }
    )

    assert [
        condition.field for condition in iter_case_filter_conditions(query.filters)
    ] == ["tags", "fields.risk_score", "dropdowns.verdict"]
    assert list(iter_case_filter_conditions(None)) == []


@pytest.mark.anyio
async def test_legacy_filters_do_not_add_a_level_to_the_explicit_tree() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=WORKSPACE_ID,
        organization_id=UUID("00000000-0000-4000-8000-000000000003"),
    )
    query = CaseQuery.model_validate(
        {
            "filters": {
                "not": {
                    "or": [
                        {
                            "and": [
                                {"field": "summary", "op": "eq", "value": "synthetic"}
                            ]
                        }
                    ]
                }
            }
        }
    )
    legacy = legacy_case_filters(status=CaseStatus.NEW)
    assert query.filters is not None and legacy is not None
    # The authored tree is already at the allowed depth; wrapping both trees
    # in another filter group would reject an otherwise valid request.
    with pytest.raises(ValidationError, match="maximum depth"):
        AndClause.model_validate({"and": [query.filters, legacy]})
    hierarchy = AsyncMock()
    hierarchy.resolve_filters.return_value = (None, False)
    service = CaseSearchService(
        AsyncMock(), role, fields=AsyncMock(), hierarchy=hierarchy
    )
    with patch(
        "tracecat.cases.search_service.compile_filter", wraps=compile_filter
    ) as compiler:
        predicates = await service.prepare_query(
            query, case_addons_enabled=False, legacy_filters=legacy
        )
    assert [call.args[0] for call in compiler.call_args_list] == [
        query.filters,
        legacy,
    ]
    assert len(predicates) == 3  # Workspace scope and two independent trees.
    statement = sa.select(Case.id).where(*predicates)
    compiled = statement.compile(dialect=postgresql.dialect())
    assert " AND " in str(compiled)
    assert "synthetic" in compiled.params.values()
    assert [CaseStatus.NEW] in compiled.params.values()


def test_legacy_empty_filters_remain_unfiltered() -> None:
    assert (
        legacy_case_filters(status=[], tag_refs=[], dropdown_filters={"verdict": []})
        is None
    )


def test_legacy_adapter_preserves_any_tag_and_assignee_or_semantics() -> None:
    adapted = legacy_case_filters(
        status=[CaseStatus.NEW, CaseStatus.NEW],
        priority=CasePriority.CRITICAL,
        severity=[CaseSeverity.HIGH],
        assignee_ids=[CASE_ID, CASE_ID],
        include_unassigned=True,
        tag_refs=["synthetic-tag", "other-tag"],
        dropdown_filters={"verdict": ["selected", "selected"]},
    )
    assert adapted is not None
    assert adapted.model_dump(mode="json") == {
        "and": [
            {"field": "status", "op": "in", "value": ["new"]},
            {"field": "priority", "op": "in", "value": ["critical"]},
            {"field": "severity", "op": "in", "value": ["high"]},
            {
                "or": [
                    {"field": "assignee_id", "op": "in", "value": [str(CASE_ID)]},
                    {"field": "assignee_id", "op": "is_null", "value": None},
                ]
            },
            {"field": "tags", "op": "in", "value": ["synthetic-tag", "other-tag"]},
            {"field": "dropdowns.verdict", "op": "in", "value": ["selected"]},
        ]
    }


def test_legacy_date_adapter_preserves_inclusive_bounds() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    adapted = legacy_case_filters(
        start_time=timestamp,
        end_time=timestamp,
        updated_after=timestamp,
        updated_before=timestamp,
    )
    conditions = list(iter_case_filter_conditions(adapted))
    assert [(condition.field, condition.op) for condition in conditions] == [
        ("created_at", FilterOp.GTE),
        ("created_at", FilterOp.LTE),
        ("updated_at", FilterOp.GTE),
        ("updated_at", FilterOp.LTE),
    ]
    assert all(condition.value == timestamp for condition in conditions)
