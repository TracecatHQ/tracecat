"""The chat's structured model error contract."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import orjson
import pytest

from tracecat.agent.adapter.vercel import sse_vercel
from tracecat.agent.error_policy import (
    agent_executor_unclassified,
    agent_llm_gateway_auth_failed,
    agent_llm_model_not_enabled,
    agent_llm_provider_auth_failed,
    agent_llm_rate_limited,
    user_agent_execution_failed,
)
from tracecat.agent.model_errors import (
    ModelErrorCode,
    ModelErrorDetail,
    model_error_detail,
)
from tracecat.agent.sandbox.llm_proxy import _upstream_request_id
from tracecat.agent.stream.connector import _parse_error_detail
from tracecat.agent.stream.events import StreamEnd, StreamError, StreamEvent
from tracecat.chat.constants import MODEL_ERROR_DATA_PART_TYPE
from tracecat.runtime.errors import RuntimeErrorKind


class TestModelErrorDetail:
    def test_provider_auth_rejection_names_provider_and_model(self) -> None:
        """A rejected key maps to provider_auth_failed and keeps safe facts."""
        detail = model_error_detail(
            agent_llm_provider_auth_failed(),
            provider="openai",
            model="gpt-test",
            status_code=401,
            request_id="req_abc12345",
            provider_error_code="invalid_api_key",
        )
        assert detail.code is ModelErrorCode.PROVIDER_AUTH_FAILED
        assert detail.kind is RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED
        assert (detail.provider, detail.model) == ("openai", "gpt-test")
        assert (detail.status_code, detail.request_id) == (401, "req_abc12345")

    def test_gateway_error_type_beats_auth_status(self) -> None:
        """The gateway's machine-readable type wins over a 403 status."""
        detail = model_error_detail(
            agent_llm_model_not_enabled(),
            provider="openai",
            model="gpt-test",
            status_code=403,
            provider_error_code="403",
            provider_error_type="tracecat_llm_model_not_enabled",
        )
        assert detail.code is ModelErrorCode.MODEL_NOT_ENABLED

    @pytest.mark.parametrize(
        ("status_code", "error_code", "expected"),
        [
            (404, None, ModelErrorCode.MODEL_UNAVAILABLE),
            (400, "context_length_exceeded", ModelErrorCode.CONTEXT_TOO_LONG),
            (400, None, ModelErrorCode.REQUEST_REJECTED),
            (503, None, ModelErrorCode.PROVIDER_UNREACHABLE),
            (None, None, ModelErrorCode.UNKNOWN),
        ],
    )
    def test_generic_failures_map_by_status_and_code(
        self,
        status_code: int | None,
        error_code: str | None,
        expected: ModelErrorCode,
    ) -> None:
        detail = model_error_detail(
            user_agent_execution_failed(),
            provider="openai",
            model="gpt-test",
            status_code=status_code,
            provider_error_code=error_code,
        )
        assert detail.code is expected

    def test_gateway_token_failure_never_blames_the_provider_key(self) -> None:
        """A refused internal gateway token is not the user's provider key."""
        detail = model_error_detail(
            agent_llm_gateway_auth_failed(),
            provider="openai",
            model="gpt-test",
            status_code=401,
            provider_error_type="tracecat_llm_token_invalid",
        )
        assert detail.code is ModelErrorCode.UNKNOWN

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_unrecognized_gateway_auth_refusal_is_not_the_provider_key(
        self, status_code: int
    ) -> None:
        """A managed 401/403 the proxy couldn't attribute is platform-owned, so
        the chat never tells the user to replace their provider key."""
        detail = model_error_detail(
            agent_executor_unclassified(
                detail=f"LLM gateway returned HTTP {status_code}"
            ),
            provider="openai",
            model="gpt-test",
            status_code=status_code,
        )
        assert detail.code is ModelErrorCode.UNKNOWN
        assert detail.status_code == status_code

    def test_rate_limit_kind_maps_without_status(self) -> None:
        detail = model_error_detail(
            agent_llm_rate_limited(route_is_direct=True),
            provider="anthropic",
            model="claude-test",
        )
        assert detail.code is ModelErrorCode.RATE_LIMITED

    def test_unreachable_direct_route_maps_to_provider_unreachable(self) -> None:
        detail = model_error_detail(
            user_agent_execution_failed(),
            provider="openai",
            model="gpt-test",
            provider_unreachable=True,
        )
        assert detail.code is ModelErrorCode.PROVIDER_UNREACHABLE

    @pytest.mark.parametrize(
        ("request_id", "error_code", "kept"),
        [
            ("req_abc12345", "invalid_api_key", True),
            ("0b6c5f9e-2d1a-4c8e-9f3b-7a1d2e3f4a5b", "authentication_error", True),
            ("sk-proj-AbC123xyz", "sk-proj-AbC123xyz", False),
            (
                "c0ffee0123456789abcdef0123456789",
                "c0ffee0123456789abcdef0123456789",
                False,
            ),
            ("nvapi-AbC123", "Mixed-Case", False),
            ("tracecat.support.example.upload-your-key", "x", True),
        ],
    )
    def test_only_known_id_and_code_shapes_reach_the_chat(
        self, request_id: str, error_code: str, kept: bool
    ) -> None:
        """An upstream that echoes a key, or any free text, in an ID or code
        field is dropped; real provider IDs and error codes are kept."""
        detail = model_error_detail(
            agent_llm_provider_auth_failed(),
            provider="openai",
            model="gpt-test",
            request_id=request_id,
            provider_error_code=error_code,
        )
        if kept and error_code != "x":
            assert (detail.request_id, detail.provider_error_code) == (
                request_id,
                error_code,
            )
        else:
            assert detail.request_id is None
            assert detail.provider_error_code == ("x" if error_code == "x" else None)

    def test_long_model_names_are_accepted(self) -> None:
        """Catalog names may be long; building the detail must never fail."""
        detail = model_error_detail(
            agent_llm_provider_auth_failed(), provider="openai", model="m" * 500
        )
        assert detail.model == "m" * 500

    def test_free_text_never_passes_as_an_id_or_code(self) -> None:
        """Provider-controlled strings that aren't tokens are dropped."""
        detail = model_error_detail(
            agent_llm_provider_auth_failed(),
            provider="openai",
            model="gpt-test",
            request_id="Incorrect API key provided: sk-abc",
            provider_error_code="key sk-abc is invalid",
        )
        assert detail.request_id is None
        assert detail.provider_error_code is None


class TestUpstreamRequestId:
    @pytest.mark.parametrize(
        "forwarded",
        [
            {"X-Request-ID": "req_fromTheSandbox"},
            {"request-id": "req_fromTheSandbox"},
            {"X-Request-ID": "req_other123", "x-request-id": "req_fromTheSandbox"},
            {"request-id": "req_fromTheSandbox, req_other123"},
        ],
    )
    def test_echoed_request_ids_are_not_the_providers(
        self, forwarded: dict[str, str]
    ) -> None:
        """Any request ID the sandbox sent, in any spelling, isn't the provider's."""
        echoed = httpx.Headers({"request-id": "req_fromTheSandbox"})
        assert _upstream_request_id(echoed, forwarded=forwarded) is None

    def test_providers_own_id_is_kept(self) -> None:
        own = httpx.Headers({"request-id": "req_providerOwn123"})
        forwarded = {"X-Request-ID": "trace-1"}
        assert _upstream_request_id(own, forwarded=forwarded) == "req_providerOwn123"


class TestStreamContract:
    def test_detail_round_trips_through_the_stream_entry(self) -> None:
        detail = model_error_detail(
            agent_llm_provider_auth_failed(),
            provider="openai",
            model="gpt-test",
            status_code=401,
        )
        raw = orjson.loads(
            orjson.dumps(detail.model_dump(mode="json", exclude_none=True))
        )
        assert _parse_error_detail(raw) == detail

    def test_unknown_code_from_a_newer_backend_reads_as_unknown(self) -> None:
        detail = _parse_error_detail(
            {"code": "a_future_code", "provider": "openai", "status_code": 401}
        )
        assert detail == ModelErrorDetail(
            code=ModelErrorCode.UNKNOWN, provider="openai", status_code=401
        )

    def test_malformed_detail_is_dropped(self) -> None:
        assert _parse_error_detail({"code": "unknown", "status_code": 99}) is None
        assert _parse_error_detail("not-a-detail") is None
        assert _parse_error_detail({"code": []}) is None
        assert _parse_error_detail({"code": {}}) is None
        assert _parse_error_detail(None) is None

    @pytest.mark.anyio
    async def test_vercel_stream_sends_detail_before_error(self) -> None:
        """The UI reads the transient data part, then the error chunk."""
        detail = ModelErrorDetail(
            code=ModelErrorCode.PROVIDER_AUTH_FAILED,
            provider="openai",
            model="gpt-test",
        )

        async def events() -> AsyncIterator[StreamEvent]:
            yield StreamError(error="model_error", detail=detail)
            yield StreamEnd(id="1-0")

        frames = [
            orjson.loads(line.removeprefix("data: "))
            async for chunk in sse_vercel(events(), message_id="m")
            for line in chunk.splitlines()
            if line.startswith("data: {")
        ]
        types = [frame["type"] for frame in frames]
        assert MODEL_ERROR_DATA_PART_TYPE in types
        data_index = types.index(MODEL_ERROR_DATA_PART_TYPE)
        assert data_index < types.index("error")
        data_frame = frames[data_index]
        assert data_frame["transient"] is True
        assert data_frame["data"]["code"] == "provider_auth_failed"

    def test_basic_sse_includes_detail(self) -> None:
        detail = ModelErrorDetail(code=ModelErrorCode.RATE_LIMITED)
        sse = StreamError(error="model_error", detail=detail).sse()
        payload = orjson.loads(sse.split("data: ", 1)[1])
        assert payload == {"error": "model_error", "detail": {"code": "rate_limited"}}
