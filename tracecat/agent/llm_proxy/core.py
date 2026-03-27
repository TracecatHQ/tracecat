"""Core execution engine for the Tracecat-owned LLM proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from fastapi import HTTPException
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from tracecat.agent.llm_proxy.credentials import (
    AgentManagementCredentialResolver,
    CredentialResolver,
)
from tracecat.agent.llm_proxy.providers import (
    AnthropicStreamingAdapter,
    PassthroughStreamAdapter,
    ProviderAdapter,
    ProviderRegistry,
    ProviderRetryAdapter,
)
from tracecat.agent.llm_proxy.requests import (
    extract_anthropic_request_parts,
    filter_allowed_model_settings,
    render_anthropic_stream_event,
    stream_anthropic_response,
)
from tracecat.agent.llm_proxy.types import (
    IngressFormat,
    NormalizedMessagesRequest,
    NormalizedResponse,
)
from tracecat.agent.tokens import LLMTokenClaims
from tracecat.logger import logger

MAX_LLM_PROXY_ATTEMPTS = 2


class _RetryableRequestError(RuntimeError):
    """Raised when a provider adapter rewrites a failed request for retry."""


@dataclass(slots=True)
class ProxyRuntimeState:
    """Lightweight runtime counters for health and observability."""

    active_requests: int = 0
    total_requests: int = 0
    total_errors: int = 0


@dataclass(slots=True)
class TracecatLLMProxy:
    """Tracecat-owned LLM proxy core."""

    credential_resolver: CredentialResolver
    provider_registry: ProviderRegistry = field(
        default_factory=ProviderRegistry.default
    )
    http_client: httpx.AsyncClient = field(
        default_factory=lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(connect=20.0, read=300.0, write=30.0, pool=10.0)
        )
    )
    state: ProxyRuntimeState = field(default_factory=ProxyRuntimeState)

    @classmethod
    def build(cls) -> TracecatLLMProxy:
        """Build the production proxy with service-backed credentials."""
        return cls(credential_resolver=AgentManagementCredentialResolver())

    def _track_start(self) -> None:
        self.state.active_requests += 1
        self.state.total_requests += 1

    def _track_end(self, *, error: bool = False) -> None:
        self.state.active_requests = max(0, self.state.active_requests - 1)
        if error:
            self.state.total_errors += 1

    async def close(self) -> None:
        await self.http_client.aclose()

    async def _resolve_credentials(
        self, claims: LLMTokenClaims
    ) -> dict[str, str] | None:
        return await self.credential_resolver.resolve(
            claims.provider,
            claims.workspace_id,
            claims.organization_id,
            claims.use_workspace_credentials,
        )

    def _normalize_messages_request(
        self,
        *,
        payload: dict[str, Any],
        claims: LLMTokenClaims,
        trace_request_id: str | None = None,
    ) -> NormalizedMessagesRequest:
        allowed_base_url = (
            claims.base_url
            if claims.provider in {"openai", "anthropic", "custom-model-provider"}
            else None
        )
        parts = extract_anthropic_request_parts(payload, provider=claims.provider)
        token_model_settings = filter_allowed_model_settings(
            claims.model_settings,
            provider=claims.provider,
        )
        parallel_tool_calls = None
        if isinstance(claims.model_settings.get("parallel_tool_calls"), bool):
            parallel_tool_calls = claims.model_settings["parallel_tool_calls"]
        response_format = None
        if isinstance(claims.model_settings.get("response_format"), dict):
            response_format = claims.model_settings["response_format"]
        return NormalizedMessagesRequest(
            provider=claims.provider,
            model=claims.model,
            messages=parts["messages"],
            output_format=IngressFormat.ANTHROPIC,
            stream=parts["stream"],
            base_url=allowed_base_url,
            use_workspace_credentials=claims.use_workspace_credentials,
            tools=parts["tools"],
            tool_choice=parts["tool_choice"],
            parallel_tool_calls=parallel_tool_calls,
            response_format=response_format,
            model_settings={
                **parts["model_settings"],
                **token_model_settings,
            },
            request_id=trace_request_id,
            workspace_id=claims.workspace_id,
            organization_id=claims.organization_id,
            session_id=claims.session_id,
            metadata=parts["metadata"],
        )

    async def _execute_request(
        self,
        *,
        request: NormalizedMessagesRequest,
        adapter: ProviderAdapter,
        credentials: dict[str, str],
    ) -> NormalizedResponse:
        outbound_request = replace(request, stream=False)
        outbound_ref = [adapter.prepare_request(outbound_request, credentials)]

        @retry(
            retry=retry_if_exception_type(_RetryableRequestError),
            stop=stop_after_attempt(MAX_LLM_PROXY_ATTEMPTS),
            reraise=True,
        )
        async def _send() -> NormalizedResponse:
            outbound = outbound_ref[0]
            response = await self.http_client.request(
                outbound.method,
                outbound.url,
                headers=outbound.headers,
                content=outbound.body,
                json=outbound.json_body,
            )
            if response.status_code < 400:
                return await adapter.parse_response(response, outbound_request)
            if not isinstance(adapter, ProviderRetryAdapter):
                return await adapter.parse_response(response, outbound_request)
            retry_request = adapter.prepare_retry_request(
                response=response,
                request=outbound_request,
                credentials=credentials,
                outbound_request=outbound,
            )
            if retry_request is None:
                return await adapter.parse_response(response, outbound_request)
            outbound_ref[0] = retry_request
            raise _RetryableRequestError

        try:
            return await _send()
        except Exception as exc:
            logger.exception(
                "LLM proxy request failed",
                provider=adapter.provider,
                model=request.model,
                error=str(exc),
            )
            raise

    async def stream_messages(
        self,
        *,
        payload: dict[str, Any],
        claims: LLMTokenClaims,
        trace_request_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        # Eagerly resolve credentials before constructing the lazy generator
        # so that HTTPException is raised before response headers are sent.
        credentials = await self._resolve_credentials(claims)
        if credentials is None:
            raise HTTPException(
                status_code=401,
                detail=(f"No credentials configured for provider '{claims.provider}'"),
            )
        adapter = self.provider_registry.get(claims.provider)
        error = False

        async def _event_stream() -> AsyncIterator[bytes]:
            nonlocal error
            self._track_start()
            try:
                # Passthrough: ingress format matches provider's native format.
                # Forward raw payload with auth + model settings, no normalization.
                # Pin the model to what the token authorizes to prevent callers
                # from overriding it in the body (e.g. requesting a costlier model).
                if (
                    isinstance(adapter, PassthroughStreamAdapter)
                    and adapter.native_format is IngressFormat.ANTHROPIC
                ):
                    async for chunk in adapter.passthrough_stream(
                        self.http_client,
                        payload,
                        credentials,
                        claims.model_settings,
                        model=claims.model,
                        base_url=claims.base_url,
                    ):
                        yield chunk
                    return

                # Other providers: normalize → adapt → render
                request = self._normalize_messages_request(
                    payload=payload,
                    claims=claims,
                    trace_request_id=trace_request_id,
                )
                if isinstance(adapter, AnthropicStreamingAdapter) and request.stream:
                    async for event in adapter.stream_anthropic(
                        self.http_client,
                        request,
                        credentials,
                    ):
                        yield render_anthropic_stream_event(event)
                    return

                normalized = await self._execute_request(
                    request=request,
                    adapter=adapter,
                    credentials=credentials,
                )
                for chunk in stream_anthropic_response(normalized):
                    yield chunk
            except Exception:
                error = True
                raise
            finally:
                self._track_end(error=error)

        return _event_stream()

    def readiness(self) -> dict[str, Any]:
        return {
            "ok": True,
            "active_requests": self.state.active_requests,
            "total_requests": self.state.total_requests,
            "total_errors": self.state.total_errors,
            "providers": sorted(self.provider_registry.adapters.keys()),
        }
