"""Failure classification through the managed gateway's actual endpoint."""

from unittest.mock import AsyncMock

import httpx
import litellm
import orjson
import pytest
from fastapi import Response
from litellm.exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from litellm.proxy._types import ProxyException, UserAPIKeyAuth
from litellm.proxy.anthropic_endpoints.endpoints import anthropic_response
from litellm.proxy.common_request_processing import ProxyBaseLLMRequestProcessing
from litellm.proxy.common_utils.user_api_key_cache import UserApiKeyCache
from litellm.proxy.utils import ProxyLogging
from starlette.requests import Request

from tracecat.agent.gateway import TracecatCallbackHandler
from tracecat.agent.sandbox.llm_proxy import _http_error_classification
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)


@pytest.fixture(autouse=True)
def gateway_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the real dispatcher too: it catches exceptions raised by hooks
    # and returns them, which the Anthropic endpoint currently ignores.
    proxy_logging = ProxyLogging(user_api_key_cache=UserApiKeyCache())
    proxy_logging.alert_types = []
    monkeypatch.setattr(litellm, "callbacks", [TracecatCallbackHandler()])
    monkeypatch.setattr("litellm.proxy.proxy_server.proxy_logging_obj", proxy_logging)


async def _anthropic_failure(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> ProxyException:
    monkeypatch.setattr(
        "litellm.proxy.anthropic_endpoints.endpoints._read_request_body",
        AsyncMock(return_value={"model": "synthetic-model", "stream": True}),
    )
    monkeypatch.setattr(
        ProxyBaseLLMRequestProcessing,
        "base_process_llm_request",
        AsyncMock(side_effect=error),
    )
    request = Request(
        {"type": "http", "method": "POST", "path": "/v1/messages", "headers": []}
    )
    with pytest.raises(ProxyException) as failure:
        await anthropic_response(Response(), request, UserAPIKeyAuth())
    return failure.value


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_anthropic_endpoint_preserves_provider_auth_classification(
    status_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = httpx.Response(
        status_code,
        request=httpx.Request("POST", "https://provider.example.invalid/v1/messages"),
    )
    error_class = AuthenticationError if status_code == 401 else PermissionDeniedError
    error = error_class(
        message="synthetic-sensitive-detail",
        llm_provider="synthetic-provider",
        model="synthetic-model",
        response=response,
    )

    failure = await _anthropic_failure(error, monkeypatch)

    assert failure.code == str(status_code)
    body = orjson.dumps({"error": failure.to_dict()})
    classification = _http_error_classification(
        status_code, route_is_direct=False, body=body
    )
    assert classification.kind is RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert b"synthetic-sensitive-detail" not in body


@pytest.mark.anyio
@pytest.mark.parametrize("quota_exceeded", [True, False])
async def test_anthropic_endpoint_distinguishes_provider_quota_from_throttling(
    quota_exceeded: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://provider.example.invalid/v1/messages"),
        json={
            "error": {
                "code": "insufficient_quota"
                if quota_exceeded
                else "rate_limit_exceeded"
            }
        },
    )
    error = RateLimitError(
        message="synthetic-sensitive-detail",
        llm_provider="synthetic-provider",
        model="synthetic-model",
    )
    error.__cause__ = httpx.HTTPStatusError(
        "synthetic-sensitive-detail", request=response.request, response=response
    )

    failure = await _anthropic_failure(error, monkeypatch)

    assert failure.code == "429"
    body = orjson.dumps({"error": failure.to_dict()})
    classification = _http_error_classification(429, route_is_direct=False, body=body)
    if quota_exceeded:
        assert classification.kind is RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED
        assert classification.owner is RuntimeErrorOwner.USER
        assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
        assert b"synthetic-sensitive-detail" not in body
    else:
        assert classification.kind is RuntimeErrorKind.AGENT_LLM_RATE_LIMITED
        assert classification.owner is RuntimeErrorOwner.PLATFORM
        assert classification.retry_disposition is RetryDisposition.RETRYABLE
