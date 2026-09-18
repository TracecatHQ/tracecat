"""Semantic search platform routing and safe error boundaries."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError
from tracecat_registry.sdk.client import TracecatClient

from tracecat.auth.types import Role
from tracecat.executor.action_gateway.app import create_app
from tracecat.search.router import semantic_search
from tracecat.search.schemas import SearchRequest
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
