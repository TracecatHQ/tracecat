"""Tests for the Cases SDK client."""

from __future__ import annotations

import ast
import inspect
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import orjson
import pytest
from tracecat_registry.sdk.cases import CasesClient
from tracecat_registry.sdk.client import TracecatClient


@pytest.fixture
def mock_tracecat_client() -> MagicMock:
    """Create a mock TracecatClient."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.patch = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.fixture
def cases_client(mock_tracecat_client: MagicMock) -> CasesClient:
    """Create a CasesClient with mocked HTTP client."""
    return CasesClient(mock_tracecat_client)


@pytest.mark.anyio
async def test_aggregate_cases_uses_exact_gateway_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = {
        "group_by": [{"field": "fields.amount", "alias": "amount"}],
        "filters": {"or": [{"field": "fields.region", "op": "is_null"}]},
        "aggs": [{"function": "count"}],
    }
    response = {
        "groups": [{"amount": "12345678901234567890.123", "count": 2}],
        "truncated": True,
    }
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert (
            str(request.url)
            == "http://tracecat-action-gateway/internal/cases/aggregate"
        )
        assert orjson.loads(request.content) == spec
        return httpx.Response(200, json=response)

    def create_transport(*, uds: str) -> httpx.MockTransport:
        assert uds == "/tmp/aggregate-cases-test.sock"
        return httpx.MockTransport(handle)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", create_transport)
    client = TracecatClient(action_gateway_socket="/tmp/aggregate-cases-test.sock")
    assert await client.cases.aggregate_cases(spec) == response
    assert len(requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("order_by", ["assignee", "tags"])
async def test_relationship_sort_uses_gateway_query(
    monkeypatch: pytest.MonkeyPatch,
    order_by: str,
) -> None:
    """The SDK forwards case sort keys on the existing gateway protocol."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        values = (
            orjson.loads(request.content)
            if request.method == "POST"
            else request.url.params
        )
        assert values["order_by"] == order_by
        assert values["sort"] == "asc"
        return httpx.Response(200, json={"items": [], "has_more": False})

    def create_transport(*, uds: str) -> httpx.MockTransport:
        return httpx.MockTransport(handle)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", create_transport)
    client = TracecatClient(action_gateway_socket="/tmp/synthetic-case-sort.sock")
    await client.cases.list_cases(order_by=order_by, sort="asc")
    await client.cases.search_cases(order_by=order_by, sort="asc")
    assert [request.url.path for request in requests] == [
        "/internal/cases",
        "/internal/cases/search",
    ]


@pytest.mark.anyio
async def test_create_case_serializes_dropdown_uuid_values(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    """UUID dropdown ids are serialized to strings for JSON transport."""
    definition_id = uuid4()
    option_id = uuid4()
    mock_tracecat_client.post.return_value = {"id": "case-id"}

    await cases_client.create_case(
        summary="Case summary",
        description="Case description",
        dropdown_values=[
            {"definition_id": definition_id, "option_id": option_id},
            {"definition_ref": "environment", "option_ref": "prod"},
        ],
    )

    mock_tracecat_client.post.assert_called_once_with(
        "/cases",
        json={
            "summary": "Case summary",
            "description": "Case description",
            "status": "new",
            "priority": "medium",
            "severity": "medium",
            "dropdown_values": [
                {"definition_id": str(definition_id), "option_id": str(option_id)},
                {"definition_ref": "environment", "option_ref": "prod"},
            ],
        },
    )


@pytest.mark.anyio
async def test_update_case_simple_serializes_and_keeps_null_option(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    """UUID dropdown ids are stringified while explicit null option_id is retained."""
    definition_id = uuid4()
    mock_tracecat_client.patch.return_value = {"id": "case-id"}

    await cases_client.update_case_simple(
        "case-id",
        dropdown_values=[{"definition_id": definition_id, "option_id": None}],
    )

    mock_tracecat_client.patch.assert_called_once_with(
        "/cases/case-id/simple",
        json={
            "dropdown_values": [
                {"definition_id": str(definition_id), "option_id": None},
            ]
        },
    )


@pytest.mark.anyio
async def test_list_case_rows_uses_client_internal_prefix_once(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.get.return_value = {"items": [], "next_cursor": None}

    await cases_client.list_case_rows("case-id", limit=10)

    mock_tracecat_client.get.assert_called_once_with(
        "/cases/case-id/rows",
        params={"limit": 10},
    )


@pytest.mark.anyio
async def test_link_case_row_uses_client_internal_prefix_once(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.post.return_value = {"case_id": "case-id"}

    await cases_client.link_case_row("case-id", table_id="table-id", row_id="row-id")

    mock_tracecat_client.post.assert_called_once_with(
        "/cases/case-id/rows",
        json={"table_id": "table-id", "row_id": "row-id"},
    )


@pytest.mark.anyio
async def test_unlink_case_row_uses_client_internal_prefix_once(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    await cases_client.unlink_case_row("case-id", table_id="table-id", row_id="row-id")

    mock_tracecat_client.delete.assert_called_once_with(
        "/cases/case-id/rows/table-id/row-id"
    )


@pytest.mark.anyio
async def test_insert_case_row_uses_client_internal_prefix_once(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.post.return_value = {"case_id": "case-id"}

    await cases_client.insert_case_row(
        "case-id",
        table_id="table-id",
        row={"customer": "acme"},
    )

    mock_tracecat_client.post.assert_called_once_with(
        "/cases/case-id/rows/insert",
        json={"table_id": "table-id", "row": {"data": {"customer": "acme"}}},
    )


@pytest.mark.anyio
async def test_list_comment_threads_uses_client_internal_prefix_once(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.get.return_value = []

    await cases_client.list_comment_threads("case-id")

    mock_tracecat_client.get.assert_called_once_with("/cases/case-id/comments/threads")


@pytest.mark.anyio
async def test_get_comment_thread_uses_client_internal_prefix_once(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.get.return_value = {"comment": {}, "replies": []}

    await cases_client.get_comment_thread("comment-id")

    mock_tracecat_client.get.assert_called_once_with("/comments/comment-id/thread")


@pytest.mark.anyio
async def test_update_comment_by_id_only_sends_content(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.patch.return_value = {"id": "comment-id"}

    await cases_client.update_comment_by_id(
        "comment-id",
        content="Updated content",
    )

    mock_tracecat_client.patch.assert_called_once_with(
        "/comments/comment-id",
        json={"content": "Updated content"},
    )


@pytest.mark.anyio
async def test_reply_to_comment_uses_udf_simple_endpoint_once(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.post.return_value = {"id": "comment-id"}

    await cases_client.reply_to_comment(
        "case-id",
        parent_comment_id="parent-id",
        content="Reply content",
    )

    mock_tracecat_client.post.assert_called_once_with(
        "/cases/case-id/comments/simple",
        json={"content": "Reply content", "parent_id": "parent-id"},
    )


@pytest.mark.anyio
async def test_update_comment_simple_only_sends_content(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.patch.return_value = {"id": "comment-id"}

    await cases_client.update_comment_simple(
        "comment-id",
        content="Updated content",
    )

    mock_tracecat_client.patch.assert_called_once_with(
        "/comments/comment-id/simple",
        json={"content": "Updated content"},
    )


@pytest.mark.anyio
async def test_list_linked_cases_uses_table_row_path(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    mock_tracecat_client.get.return_value = {"items": [], "next_cursor": None}

    await cases_client.list_linked_cases(
        "table-id", "row-id", limit=5, exclude_case_id="CASE-0001", sort="asc"
    )

    mock_tracecat_client.get.assert_called_once_with(
        "/tables/table-id/rows/row-id/cases",
        params={"limit": 5, "sort": "asc", "exclude_case_id": "CASE-0001"},
    )


@pytest.mark.anyio
async def test_search_cases_canonical_gateway_body_and_detail_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry forwards the server-owned AST and exact relative path."""
    workspace_case_id = str(uuid4())
    parent_id = str(uuid4())
    filters = {
        "or": [
            {"field": "priority", "op": "eq", "value": "critical"},
            {"field": "fields.risk_score", "op": "gte", "value": 80},
        ]
    }
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert (
            str(request.url) == "http://tracecat-action-gateway/internal/cases/search"
        )
        assert orjson.loads(request.content) == {
            "limit": 7,
            "search_term": "powershell encoded",
            "search_mode": "full_text",
            "search_columns": ["description"],
            "short_id": "CASE-0042",
            "case_id": workspace_case_id,
            "parent_id": parent_id,
            "hierarchy": "all",
            "filters": filters,
            "include_rows": True,
            "include_payload": True,
            "include_durations": True,
            "include_count": False,
            "field_ids": ["risk_score"],
            "assignee_id": ["unassigned"],
            "tags": ["synthetic-tag"],
            "dropdown": ["verdict:selected"],
            "order_by": "short_id",
            "sort": "asc",
        }
        return httpx.Response(200, json={"items": [], "total_estimate": None})

    def create_transport(*, uds: str) -> httpx.MockTransport:
        return httpx.MockTransport(handle)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", create_transport)
    client = TracecatClient(action_gateway_socket="/tmp/synthetic-case-search.sock")
    response = await client.cases.search_cases(
        limit=7,
        search_term="powershell encoded",
        search_mode="full_text",
        search_columns=["description"],
        short_id="CASE-0042",
        case_id=workspace_case_id,
        parent_id=parent_id,
        hierarchy="all",
        filters=filters,
        include_rows=True,
        include_payload=True,
        include_durations=True,
        include_count=False,
        field_ids=["risk_score"],
        assignee_id=["unassigned"],
        tags=["synthetic-tag"],
        dropdown=["verdict:selected"],
        order_by="short_id",
        sort="asc",
    )
    assert response["items"] == [] and response["total_estimate"] is None
    assert len(requests) == 1


@pytest.mark.anyio
async def test_search_cases_serializes_native_filter_scalars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real HTTP encoding preserves nested scalar values without mutating input."""
    timestamp = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    day = date(2026, 1, 2)
    assignee = uuid4()
    amount = Decimal("12345678901234567890.123456789")
    filters = {
        "and": [
            {"field": "created_at", "op": "gte", "value": timestamp},
            {
                "or": [
                    {"field": "updated_at", "op": "eq", "value": day},
                    {
                        "not": {
                            "field": "assignee",
                            "op": "in",
                            "value": [assignee],
                        }
                    },
                ]
            },
            {"field": "fields.amount", "op": "gte", "value": amount},
            {"field": "assignee", "op": "is_null", "value": None},
        ]
    }
    original = deepcopy(filters)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        clauses = orjson.loads(request.content)["filters"]["and"]
        assert datetime.fromisoformat(clauses[0]["value"]) == timestamp
        alternatives = clauses[1]["or"]
        assert date.fromisoformat(alternatives[0]["value"]) == day
        assert alternatives[1]["not"]["value"] == [str(assignee)]
        assert clauses[2]["value"] == str(amount)
        assert clauses[3] == {
            "field": "assignee",
            "op": "is_null",
            "value": None,
        }
        return httpx.Response(200, json={"items": [], "has_more": False})

    def create_transport(*, uds: str) -> httpx.MockTransport:
        return httpx.MockTransport(handle)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", create_transport)
    client = TracecatClient(action_gateway_socket="/tmp/synthetic-case-search.sock")
    await client.cases.search_cases(filters=filters)
    assert len(requests) == 1
    assert filters == original


def test_case_search_sdk_stub_signatures_match_client() -> None:
    """Both synchronous and asynchronous declarations expose every SDK option."""
    module = ast.parse(
        (
            Path(__file__).parents[2]
            / "packages/tracecat-registry/tracecat_registry/ctx.pyi"
        ).read_text()
    )
    declarations = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == "search_cases"
    ]
    assert len(declarations) == 2
    source_signature = inspect.signature(CasesClient.search_cases)
    source_names = list(source_signature.parameters)
    for declaration in declarations:
        assert [
            arg.arg for arg in (*declaration.args.args, *declaration.args.kwonlyargs)
        ] == source_names
        for arg in declaration.args.kwonlyargs:
            assert arg.annotation is not None
            assert (
                ast.unparse(arg.annotation)
                == source_signature.parameters[arg.arg].annotation
            )
        assert declaration.returns is not None
        assert ast.unparse(declaration.returns) == source_signature.return_annotation
