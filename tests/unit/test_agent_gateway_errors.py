"""Failure classification through the managed gateway's actual endpoint."""

from typing import Literal
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
from litellm.router import Router
from litellm.types.router import RouterRateLimitError
from openai import AsyncOpenAI
from starlette.requests import Request

from tracecat.agent.gateway import TracecatCallbackHandler
from tracecat.agent.sandbox.llm_proxy import _http_error_classification
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)


@pytest.fixture(autouse=True)
def gateway_callbacks(monkeypatch: pytest.MonkeyPatch) -> ProxyLogging:
    # Exercise the real dispatcher too: it catches exceptions raised by hooks
    # and returns them, which the Anthropic endpoint currently ignores.
    proxy_logging = ProxyLogging(user_api_key_cache=UserApiKeyCache())
    proxy_logging.alert_types = []
    monkeypatch.setattr(litellm, "callbacks", [TracecatCallbackHandler()])
    # Router registration also mutates these process-global callback lists.
    for name in (
        "success_callback",
        "failure_callback",
        "_async_success_callback",
        "_async_failure_callback",
    ):
        monkeypatch.setattr(litellm, name, [])
    monkeypatch.setattr("litellm.proxy.proxy_server.proxy_logging_obj", proxy_logging)
    return proxy_logging


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


async def _gateway_failure(
    error: Exception,
    *,
    endpoint: Literal["anthropic", "openai"],
    proxy_logging: ProxyLogging,
    monkeypatch: pytest.MonkeyPatch,
) -> ProxyException:
    if endpoint == "anthropic":
        return await _anthropic_failure(error, monkeypatch)
    processor = ProxyBaseLLMRequestProcessing(data={})
    with pytest.raises(ProxyException) as failure:
        await processor._handle_llm_api_exception(
            error, UserAPIKeyAuth(), proxy_logging
        )
    return failure.value


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["anthropic", "openai"])
@pytest.mark.parametrize("cooldown_list", [[], ["synthetic-sensitive-deployment"]])
async def test_router_unavailability_is_not_provider_throttling_or_auth(
    endpoint: Literal["anthropic", "openai"],
    cooldown_list: list[str],
    gateway_callbacks: ProxyLogging,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RouterRateLimitError(
        model="synthetic-sensitive-model",
        cooldown_time=5,
        enable_pre_call_checks=False,
        cooldown_list=cooldown_list,
    )
    failure = await _gateway_failure(
        error,
        endpoint=endpoint,
        proxy_logging=gateway_callbacks,
        monkeypatch=monkeypatch,
    )
    assert failure.code == "503"
    body = orjson.dumps({"error": failure.to_dict()})
    assert failure.type == "tracecat_llm_deployment_unavailable"
    assert b"synthetic-sensitive" not in body
    classification = _http_error_classification(503, route_is_direct=False, body=body)
    assert classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.retry_disposition is RetryDisposition.RETRYABLE


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["anthropic", "openai"])
@pytest.mark.parametrize(
    "next_key",
    ["synthetic-invalid-key", "synthetic-other-key", "synthetic-rotated-key"],
)
async def test_provider_auth_cooldown_does_not_establish_next_callers_auth_failure(
    endpoint: Literal["anthropic", "openai"],
    next_key: str,
    gateway_callbacks: ProxyLogging,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicit deployments can enter cooldown in the pinned LiteLLM version;
    # the bundled provider wildcard routes are exempt. Exercise the real router
    # because it drops the auth exception on subsequent requests.
    router = Router(
        model_list=[
            {
                "model_name": "synthetic-route",
                "litellm_params": {"model": "openai/gpt-4o-mini"},
            }
        ],
        num_retries=0,
        retry_after=0,
    )
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            401,
            request=request,
            json={
                "error": {
                    "message": "Incorrect API key provided: synthetic-sensitive-key",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client = AsyncOpenAI(
            api_key="synthetic-invalid-key", http_client=http_client, max_retries=0
        )
        with pytest.raises(AuthenticationError) as auth_failure:
            await router.acompletion(
                model="synthetic-route",
                messages=[{"role": "user", "content": "synthetic prompt"}],
                client=client,
            )

        # Run LiteLLM's actual failure callback synchronously rather than race
        # its background logging task before the next request.
        assert router.deployment_callback_on_failure(
            kwargs={
                "exception": auth_failure.value,
                "litellm_params": {"model_info": {"id": router.get_model_ids()[0]}},
            },
            completion_response=None,
            start_time=None,
            end_time=None,
        )
        first = await _gateway_failure(
            auth_failure.value,
            endpoint=endpoint,
            proxy_logging=gateway_callbacks,
            monkeypatch=monkeypatch,
        )
        first_body = orjson.dumps({"error": first.to_dict()})
        assert first.code == "401"
        assert b"synthetic-sensitive" not in first_body
        auth_classification = _http_error_classification(
            401, route_is_direct=False, body=first_body
        )
        assert (
            auth_classification.kind is RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED
        )
        assert auth_classification.owner is RuntimeErrorOwner.USER
        assert auth_classification.retry_disposition is RetryDisposition.NON_RETRYABLE

        client = AsyncOpenAI(api_key=next_key, http_client=http_client, max_retries=0)
        with pytest.raises(RouterRateLimitError) as cooldown_failure:
            await router.acompletion(
                model="synthetic-route",
                messages=[{"role": "user", "content": "synthetic prompt"}],
                client=client,
            )
        assert calls == 1  # The next credential was never sent to the provider.

    failure = await _gateway_failure(
        cooldown_failure.value,
        endpoint=endpoint,
        proxy_logging=gateway_callbacks,
        monkeypatch=monkeypatch,
    )
    assert failure.code == "503"
    body = orjson.dumps({"error": failure.to_dict()})
    assert b"synthetic-sensitive" not in body
    classification = _http_error_classification(503, route_is_direct=False, body=body)
    assert classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNAVAILABLE
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.retry_disposition is RetryDisposition.RETRYABLE


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
        assert b"synthetic-sensitive-detail" not in body
        assert b"LLM provider rate limit exceeded; retry later" in body


@pytest.mark.anyio
async def test_retry_does_not_inherit_an_earlier_provider_quota_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            request=request,
            json={
                "error": {
                    "message": "synthetic provider failure",
                    "type": "requests",
                    "code": "insufficient_quota"
                    if calls == 1
                    else "rate_limit_exceeded",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client = AsyncOpenAI(
            api_key="synthetic-key", http_client=http_client, max_retries=0
        )

        async def call_model() -> None:
            await litellm.acompletion(
                model="openai/synthetic-model",
                messages=[{"role": "user", "content": "synthetic prompt"}],
                client=client,
                num_retries=0,
            )

        try:
            await call_model()
        except RateLimitError:
            # LiteLLM also retries inside its exception handler. The SDK hides
            # this earlier failure with `raise ... from None`, but Python still
            # retains it in __context__ for a naive chain walker to discover.
            with pytest.raises(RateLimitError) as retry_failure:
                await call_model()
        else:
            pytest.fail("Expected the initial quota failure")

    assert calls == 2
    failure = await _anthropic_failure(retry_failure.value, monkeypatch)
    assert failure.code == "429"
    classification = _http_error_classification(
        429,
        route_is_direct=False,
        body=orjson.dumps({"error": failure.to_dict()}),
    )
    assert classification.kind is RuntimeErrorKind.AGENT_LLM_RATE_LIMITED
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
