"""Semantic search platform routing and safe error boundaries."""

import threading
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from tracecat_registry.sdk.client import TracecatClient

from tracecat.api.app import (
    validation_exception_handler as api_validation_exception_handler,
)
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.executor.action_gateway.app import (
    create_app,
    validation_exception_handler,
)
from tracecat.search.embeddings.types import EmbeddingError, EmbeddingErrorCode
from tracecat.search.retrieval import TableRetrievalService
from tracecat.search.router import router as search_router
from tracecat.search.router import semantic_search
from tracecat.search.schemas import SearchRequest
from tracecat.search.types import SearchError, SearchErrorCode
from tracecat.tables.internal_router import router


@pytest.mark.anyio
async def test_semantic_route_registered_on_both_internal_surfaces():
    path = "/internal/tables/{table_name}/rows/semantic-search"
    backend = FastAPI()
    backend.include_router(router)
    for app in (backend, create_app()):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
        ) as http:
            response = await http.post(
                path.replace("{table_name}", "synthetic"), json={"query": "synthetic"}
            )
            assert response.status_code in {401, 403}

    client = TracecatClient(action_gateway_socket="/tmp/synthetic.sock")
    url, _ = client._request_url_and_transport("/tables/synthetic/rows/semantic-search")
    assert (
        url
        == "http://tracecat-action-gateway/internal/tables/synthetic/rows/semantic-search"
    )


@pytest.mark.anyio
async def test_redis_failure_is_sanitized_not_an_empty_page(test_role: Role):
    with patch(
        "tracecat.search.router.TableRetrievalService.search",
        new=AsyncMock(side_effect=RedisConnectionError("synthetic private details")),
    ):
        with pytest.raises(HTTPException) as error:
            await semantic_search(
                "synthetic", SearchRequest(query="synthetic"), test_role
            )
    assert error.value.status_code == 503
    assert error.value.detail == {"code": "UNAVAILABLE"}
    assert error.value.__context__ is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code,status,retryable",
    [
        (EmbeddingErrorCode.INPUT_INVALID, 422, False),
        (EmbeddingErrorCode.CREDENTIAL_INVALID, 400, False),
        (EmbeddingErrorCode.CONFIGURATION_INVALID, 400, False),
        (EmbeddingErrorCode.NOT_CONFIGURED, 400, False),
        (EmbeddingErrorCode.CONFIGURATION_CHANGED, 409, False),
        (EmbeddingErrorCode.RATE_LIMITED, 429, True),
        (EmbeddingErrorCode.TIMEOUT, 504, True),
        (EmbeddingErrorCode.UNAVAILABLE, 502, True),
        (EmbeddingErrorCode.RESPONSE_INVALID, 502, False),
    ],
)
async def test_embedding_errors_preserve_status_and_retry_metadata(
    test_role, code, status, retryable
):
    retry_after = 3.0 if retryable else None
    with patch(
        "tracecat.search.router.TableRetrievalService.search",
        new=AsyncMock(side_effect=EmbeddingError(code, retry_after)),
    ):
        with pytest.raises(HTTPException) as error:
            await semantic_search("synthetic", SearchRequest(query="query"), test_role)
    assert error.value.status_code == status
    assert error.value.detail == {
        "code": code.value,
        "retryable": retryable,
        "retry_after": retry_after,
    }
    assert error.value.__context__ is None


@pytest.mark.anyio
async def test_invalid_table_name_returns_safe_client_error(test_role):
    with patch(
        "tracecat.search.router.TableRetrievalService.search",
        new=AsyncMock(side_effect=SearchError(SearchErrorCode.INVALID_TABLE_NAME)),
    ):
        with pytest.raises(HTTPException) as error:
            await semantic_search(
                "missing-table", SearchRequest(query="query"), test_role
            )
    assert error.value.status_code == 400
    assert error.value.detail == {"code": "INVALID_TABLE_NAME"}
    assert error.value.__context__ is None


@pytest.mark.anyio
async def test_tokenizer_construction_and_counting_run_off_event_loop(test_role):
    event_loop_thread = threading.get_ident()
    worker_threads = []

    def count_tokens(query):
        worker_threads.append(threading.get_ident())
        return 513  # Stop at the query budget guard, before accessing the index.

    def make_counter(spec):
        worker_threads.append(threading.get_ident())
        return SimpleNamespace(count_tokens=count_tokens)

    tables = AsyncMock()
    tables.get_table_by_name.return_value.id = "synthetic"
    with (
        patch("tracecat.search.retrieval.TablesService.with_session") as session,
        patch("tracecat.search.retrieval.resolve_embedding_configuration") as resolve,
        patch("tracecat.search.retrieval.token_counter", side_effect=make_counter),
    ):
        session.return_value.__aenter__.return_value = tables
        resolve.return_value = SimpleNamespace(
            spec=SimpleNamespace(
                input_character_limit=1000,
                input_token_limit=8191,
                batch_token_limit=16000,
            )
        )
        with pytest.raises(EmbeddingError) as error:
            await TableRetrievalService(test_role).search(
                "synthetic", SearchRequest(query="query")
            )
    assert error.value.code == EmbeddingErrorCode.INPUT_INVALID
    assert len(worker_threads) == 2
    assert all(thread != event_loop_thread for thread in worker_threads)


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["construction", "counting"])
@pytest.mark.parametrize("failure", [ValueError, OSError])
async def test_tokenizer_failures_return_safe_unavailable(test_role, stage, failure):
    def fail_count(query):
        raise failure("synthetic private tokenizer details")

    def make_counter(spec):
        if stage == "construction":
            raise failure("synthetic private tokenizer details")
        return SimpleNamespace(count_tokens=fail_count)

    tables = AsyncMock()
    tables.get_table_by_name.return_value.id = "synthetic"
    with (
        patch("tracecat.search.retrieval.TablesService.with_session") as session,
        patch("tracecat.search.retrieval.resolve_embedding_configuration") as resolve,
        patch("tracecat.search.retrieval.token_counter", side_effect=make_counter),
        patch("tracecat.search.retrieval.embed_current") as embed,
    ):
        session.return_value.__aenter__.return_value = tables
        resolve.return_value = SimpleNamespace(
            spec=SimpleNamespace(input_character_limit=1000)
        )
        with pytest.raises(EmbeddingError) as domain_error:
            await TableRetrievalService(test_role).search(
                "synthetic", SearchRequest(query="query")
            )
        assert domain_error.value.code == EmbeddingErrorCode.UNAVAILABLE
        assert domain_error.value.__context__ is None
        with pytest.raises(HTTPException) as error:
            await semantic_search("synthetic", SearchRequest(query="query"), test_role)
    assert error.value.status_code == 502
    assert error.value.detail == {
        "code": "UNAVAILABLE",
        "retryable": True,
        "retry_after": None,
    }
    assert error.value.__context__ is None
    embed.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        rb'{"query":"\ud800"}',
        rb'{"query":"prefix\udfff"}',
        rb'{"query":"ok","limit":"\ud800"}',
        rb'{"query":"ok","cursor":"\ud800"}',
        rb'{"query":"ok","allow_partial":"\ud800"}',
        rb'{"query":{"nested":["\ud800"]}}',
        rb'{"query":"ok","limit":{"\ud800":0}}',
        rb'["\ud800"]',
    ],
)
@pytest.mark.parametrize("gateway", [False, True])
async def test_raw_surrogate_request_returns_safe_422(test_role, body, gateway):
    app = FastAPI()
    app.include_router(search_router)
    app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = lambda: (
        test_role
    )
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler if gateway else api_validation_exception_handler,
    )
    with patch("tracecat.search.router.TableRetrievalService.search") as search:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
        ) as http:
            response = await http.post(
                "/synthetic/rows/semantic-search",
                content=body,
                headers={"content-type": "application/json"},
            )
    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "INPUT_INVALID", "retryable": False, "retry_after": None}
    }
    search.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("body", [b"", b"{}", b'{"query":42}', b'{"query":'])
async def test_encoding_guard_preserves_normal_body_validation(test_role, body):
    app = FastAPI()
    app.include_router(search_router)
    app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = lambda: (
        test_role
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as http:
        response = await http.post(
            "/synthetic/rows/semantic-search",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_valid_escaped_surrogate_pair_reaches_search(test_role):
    app = FastAPI()
    app.include_router(search_router)
    app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = lambda: (
        test_role
    )
    with patch(
        "tracecat.search.router.TableRetrievalService.search",
        new=AsyncMock(side_effect=SearchError(SearchErrorCode.NOT_FOUND)),
    ) as search:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
        ) as http:
            response = await http.post(
                "/synthetic/rows/semantic-search",
                content=rb'{"query":"\ud83d\ude00"}',
                headers={"content-type": "application/json"},
            )
    assert response.status_code == 404
    assert search.call_args.args[1].query == "😀"
