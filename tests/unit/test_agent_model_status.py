"""Model status checks and the chat's structured model error contract."""

from __future__ import annotations

import asyncio
import gzip
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import orjson
import pytest

from tracecat.agent.adapter.vercel import sse_vercel
from tracecat.agent.diagnostics import MAX_LLM_ERROR_BODY_BYTES
from tracecat.agent.error_policy import (
    agent_llm_model_not_enabled,
    agent_llm_provider_auth_failed,
    agent_llm_rate_limited,
    user_agent_execution_failed,
)
from tracecat.agent.model_status import service as status_module
from tracecat.agent.model_status.errors import model_error_detail
from tracecat.agent.model_status.schemas import ModelErrorDetail
from tracecat.agent.model_status.service import (
    ModelStatusService,
    ProbeTarget,
    build_probe_target,
    clear_status_cache,
    credentials_incomplete,
    probe_model,
)
from tracecat.agent.model_status.types import ModelErrorCode
from tracecat.agent.sandbox.llm_proxy import _upstream_request_id
from tracecat.agent.stream.connector import _parse_error_detail
from tracecat.agent.stream.events import StreamEnd, StreamError, StreamEvent
from tracecat.auth.types import Role
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
            assert detail.provider_error_code in (None, "x")

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
    def test_echoed_trace_id_is_not_the_providers(self) -> None:
        """An upstream echoing the sandbox-chosen trace ID reports no ID."""
        echoed = httpx.Headers({"x-request-id": "req_fromTheSandbox"})
        assert _upstream_request_id(echoed, sent="req_fromTheSandbox") is None

    def test_providers_own_id_is_kept(self) -> None:
        own = httpx.Headers({"request-id": "req_providerOwn123"})
        assert _upstream_request_id(own, sent="trace-1") == "req_providerOwn123"


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

    def test_malformed_detail_is_dropped(self) -> None:
        assert _parse_error_detail({"code": "not-a-code"}) is None
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


class TestProbeTarget:
    def test_openai_retrieves_the_model_with_bearer_key(self) -> None:
        target = build_probe_target(
            "openai", "openai/gpt-test", {"OPENAI_API_KEY": "sk-test"}
        )
        assert target == ProbeTarget(
            url="https://api.openai.com/v1/models/gpt-test",
            headers={"Authorization": "Bearer sk-test"},
        )

    def test_anthropic_base_url_version_is_normalized(self) -> None:
        target = build_probe_target(
            "anthropic",
            "claude-test",
            {
                "ANTHROPIC_API_KEY": "key",
                "ANTHROPIC_BASE_URL": "https://proxy.example.com/v1",
            },
        )
        assert target is not None
        assert target.url == "https://proxy.example.com/v1/models/claude-test"
        assert target.headers["x-api-key"] == "key"

    def test_gateway_provider_lists_models(self) -> None:
        target = build_probe_target(
            "vllm",
            "llama",
            {"VLLM_BASE_URL": "https://vllm.example.com/v1", "VLLM_API_KEY": "k"},
        )
        assert target is not None
        assert target.listed_model == "llama"
        assert target.url == "https://vllm.example.com/v1/models"

    @pytest.mark.parametrize("provider", ["bedrock", "vertex_ai", "azure_openai"])
    def test_cloud_providers_have_no_probe(self, provider: str) -> None:
        assert build_probe_target(provider, "m", {"X": "y"}) is None

    @pytest.mark.parametrize(
        ("provider", "credentials", "incomplete"),
        [
            ("openai", {"OPENAI_BASE_URL": "https://proxy.example.com/v1"}, True),
            ("openai", {"OPENAI_API_KEY": "sk"}, False),
            ("openrouter", {}, True),
            ("vllm", {"VLLM_BASE_URL": "https://vllm.example.com/v1"}, False),
            ("custom-model-provider", {"CUSTOM_MODEL_PROVIDER_API_KEY": "k"}, True),
        ],
    )
    def test_incomplete_credentials_match_the_gateway(
        self, provider: str, credentials: dict[str, str], incomplete: bool
    ) -> None:
        """A key or base URL every model request needs counts as missing."""
        assert credentials_incomplete(provider, credentials) is incomplete


def _mock_outbound(
    monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["origin_url"], "probe must bind the client to one origin"

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler.handle_request(request)

        return httpx.AsyncClient(
            transport=httpx.MockTransport(record), timeout=kwargs["timeout"]
        )

    monkeypatch.setattr(status_module, "create_outbound_http_client", factory)
    return seen


class TestProbeModel:
    TARGET = ProbeTarget(
        url="https://api.openai.com/v1/models/gpt-test",
        headers={"Authorization": "Bearer sk-test"},
    )

    @pytest.mark.anyio
    async def test_success_has_no_problem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _mock_outbound(
            monkeypatch, httpx.MockTransport(lambda _: httpx.Response(200))
        )
        assert await probe_model(self.TARGET, provider="openai", model="m") is None
        assert seen[0].method == "GET"

    @pytest.mark.anyio
    async def test_401_is_auth_failure_with_request_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = {"error": {"type": "invalid_request_error", "code": "invalid_api_key"}}
        _mock_outbound(
            monkeypatch,
            httpx.MockTransport(
                lambda _: httpx.Response(
                    401, json=body, headers={"x-request-id": "req_abcdef123"}
                )
            ),
        )
        detail = await probe_model(self.TARGET, provider="openai", model="m")
        assert detail is not None
        assert detail.code is ModelErrorCode.PROVIDER_AUTH_FAILED
        assert (detail.status_code, detail.request_id) == (401, "req_abcdef123")
        assert detail.provider_error_code == "invalid_api_key"

    @pytest.mark.anyio
    async def test_transport_error_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        _mock_outbound(monkeypatch, httpx.MockTransport(fail))
        detail = await probe_model(self.TARGET, provider="openai", model="m")
        assert detail is not None
        assert detail.code is ModelErrorCode.PROVIDER_UNREACHABLE

    @pytest.mark.anyio
    async def test_large_error_body_is_not_buffered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An oversized error body is cut at the parse bound and still classified."""
        _mock_outbound(
            monkeypatch,
            httpx.MockTransport(
                lambda _: httpx.Response(401, content=b"x" * (10 * 1024 * 1024))
            ),
        )
        detail = await probe_model(self.TARGET, provider="openai", model="m")
        assert detail is not None
        assert detail.code is ModelErrorCode.PROVIDER_AUTH_FAILED

    LIST_TARGET = ProbeTarget(
        url="https://vllm.example.com/v1/models", headers={}, listed_model="llama"
    )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("listing", "expected"),
        [
            ({"data": [{"id": "llama"}, {"id": "other"}]}, None),
            ({"data": [{"id": "other"}]}, ModelErrorCode.MODEL_UNAVAILABLE),
            ({"unexpected": True}, None),
        ],
    )
    async def test_gateway_listing_must_contain_the_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        listing: dict[str, Any],
        expected: ModelErrorCode | None,
    ) -> None:
        """A gateway that no longer serves the model is not ready."""
        _mock_outbound(
            monkeypatch,
            httpx.MockTransport(lambda _: httpx.Response(200, json=listing)),
        )
        detail = await probe_model(self.LIST_TARGET, provider="vllm", model="llama")
        assert (detail.code if detail else None) is expected

    @pytest.mark.anyio
    async def test_compressed_error_body_is_cut_at_the_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One large decompressed chunk is still cut at the bound."""
        seen: list[int] = []
        original = status_module._bounded_body

        async def spy(response: httpx.Response, limit: int) -> bytes:
            body = await original(response, limit)
            seen.append(len(body))
            return body

        monkeypatch.setattr(status_module, "_bounded_body", spy)
        payload = gzip.compress(b"x" * (10 * 1024 * 1024))
        _mock_outbound(
            monkeypatch,
            httpx.MockTransport(
                lambda _: httpx.Response(
                    401, content=payload, headers={"content-encoding": "gzip"}
                )
            ),
        )
        detail = await probe_model(self.TARGET, provider="openai", model="m")
        assert detail is not None
        assert seen == [MAX_LLM_ERROR_BODY_BYTES + 1]

    @pytest.mark.anyio
    async def test_list_probe_404_means_wrong_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_outbound(monkeypatch, httpx.MockTransport(lambda _: httpx.Response(404)))
        target = ProbeTarget(
            url="https://vllm.example.com/v1/models", headers={}, listed_model="m"
        )
        detail = await probe_model(target, provider="vllm", model="m")
        assert detail is not None
        assert detail.code is ModelErrorCode.PROVIDER_UNREACHABLE


class TestModelStatusService:
    """Service flow with catalog and credential lookups stubbed."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        clear_status_cache()

    @pytest.fixture
    def role(self) -> Role:
        return Role(
            type="user",
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            service_id="tracecat-api",
        )

    def _stub(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        provider: str = "openai",
        enabled: bool = True,
        credentials: dict[str, str] | None = None,
        has_cloud_credentials: bool = True,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "credentials": credentials
            if credentials is not None
            else {"OPENAI_API_KEY": "sk-1"},
            "probes": 0,
            "probe_result": None,
        }
        row = SimpleNamespace(
            id=uuid.uuid4(), model_provider=provider, model_name="gpt-test"
        )

        async def catalog_row(self: Any, **_: Any) -> Any:
            return row

        async def is_enabled(self: Any, *_: Any, **__: Any) -> bool:
            return enabled

        async def get_credentials(self: Any, _catalog_id: uuid.UUID) -> Any:
            return state["credentials"]

        async def check_credentials(self: Any, _provider: str) -> bool:
            return has_cloud_credentials

        async def probe(*_: Any, **__: Any) -> ModelErrorDetail | None:
            state["probes"] += 1
            return state["probe_result"]

        monkeypatch.setattr(ModelStatusService, "_catalog_row", catalog_row)
        monkeypatch.setattr(
            status_module.AgentModelAccessService, "is_catalog_enabled", is_enabled
        )
        monkeypatch.setattr(
            status_module.AgentManagementService,
            "get_catalog_credentials",
            get_credentials,
        )
        monkeypatch.setattr(
            status_module.AgentManagementService,
            "check_provider_credentials",
            check_credentials,
        )
        monkeypatch.setattr(status_module, "probe_model", probe)
        return state

    def _service(self, role: Role) -> ModelStatusService:
        return ModelStatusService(session=AsyncMock(), role=role)

    @pytest.mark.anyio
    async def test_ready_result_is_cached_until_credentials_change(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        state = self._stub(monkeypatch)
        service = self._service(role)
        first = await service.get_status(catalog_id=uuid.uuid4())
        second = await service.get_status(catalog_id=uuid.uuid4())
        assert first.state == second.state == "ready"
        assert state["probes"] == 1

        state["credentials"] = {"OPENAI_API_KEY": "sk-rotated"}
        await service.get_status(catalog_id=uuid.uuid4())
        assert state["probes"] == 2

        # A refresh right after a check returns that check.
        await service.get_status(catalog_id=uuid.uuid4(), refresh=True)
        assert state["probes"] == 2

        # Past the minimum interval, a refresh probes again.
        monkeypatch.setattr(status_module, "MIN_REFRESH_INTERVAL_SECONDS", 0)
        await service.get_status(catalog_id=uuid.uuid4(), refresh=True)
        assert state["probes"] == 3

    @pytest.mark.anyio
    async def test_concurrent_checks_share_one_probe(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        """Simultaneous checks for one key start a single provider request."""
        state = self._stub(monkeypatch)
        release = asyncio.Event()

        async def slow_probe(*_: Any, **__: Any) -> ModelErrorDetail | None:
            state["probes"] += 1
            await release.wait()
            return None

        monkeypatch.setattr(status_module, "probe_model", slow_probe)
        service = self._service(role)
        checks = [
            asyncio.create_task(
                service.get_status(catalog_id=uuid.uuid4(), refresh=True)
            )
            for _ in range(20)
        ]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*checks)
        assert state["probes"] == 1
        assert {r.state for r in results} == {"ready"}

    @pytest.mark.anyio
    async def test_forced_rechecks_are_budgeted_per_caller(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        """Cycling refresh across models can't exceed the caller's budget."""
        state = self._stub(monkeypatch)
        monkeypatch.setattr(status_module, "MIN_REFRESH_INTERVAL_SECONDS", 0)
        service = self._service(role)
        await service.get_status(catalog_id=uuid.uuid4())
        for _ in range(status_module.REFRESHES_PER_MINUTE + 4):
            await service.get_status(catalog_id=uuid.uuid4(), refresh=True)
        assert state["probes"] == 1 + status_module.REFRESHES_PER_MINUTE

    @pytest.mark.anyio
    async def test_metadata_permission_denial_is_unverified(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        """A 403 on model metadata doesn't mean inference is refused."""
        state = self._stub(monkeypatch)
        state["probe_result"] = ModelErrorDetail(
            code=ModelErrorCode.PROVIDER_AUTH_FAILED, status_code=403
        )
        result = await self._service(role).get_status(catalog_id=uuid.uuid4())
        assert result.state == "unverified"
        assert result.problem is None

    @pytest.mark.anyio
    async def test_preset_falls_back_to_workspace_credentials(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        """A preset runs on workspace keys when its catalog row has none."""
        state = self._stub(monkeypatch)
        state["credentials"] = None

        async def workspace_credentials(self: Any, _provider: str) -> Any:
            return {"OPENAI_API_KEY": "sk-workspace"}

        monkeypatch.setattr(
            status_module.AgentManagementService,
            "get_workspace_runtime_provider_credentials",
            workspace_credentials,
        )
        service = self._service(role)
        default = await service.get_status(catalog_id=uuid.uuid4())
        assert default.problem is not None
        assert default.problem.code is ModelErrorCode.CREDENTIALS_MISSING
        preset = await service.get_status(catalog_id=uuid.uuid4(), preset=True)
        assert preset.state == "ready"

    @pytest.mark.anyio
    async def test_unlinked_preset_uses_workspace_keys_first(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        """A preset with no catalog link runs on workspace keys even when the
        matching catalog row has its own, so the check probes those."""
        state = self._stub(monkeypatch)
        state["credentials"] = {"OPENAI_API_KEY": "sk-org"}
        probed: list[str] = []

        async def workspace_credentials(self: Any, _provider: str) -> Any:
            return {"OPENAI_API_KEY": "sk-workspace"}

        async def probe(target: ProbeTarget, **_: Any) -> None:
            probed.append(target.headers["Authorization"])

        monkeypatch.setattr(
            status_module.AgentManagementService,
            "get_workspace_runtime_provider_credentials",
            workspace_credentials,
        )
        monkeypatch.setattr(status_module, "probe_model", probe)
        await self._service(role).get_status(
            model_provider="openai", model_name="gpt-test", preset=True
        )
        assert probed == ["Bearer sk-workspace"]

    @pytest.mark.anyio
    async def test_probe_problem_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        state = self._stub(monkeypatch)
        state["probe_result"] = ModelErrorDetail(
            code=ModelErrorCode.PROVIDER_AUTH_FAILED, status_code=401
        )
        result = await self._service(role).get_status(catalog_id=uuid.uuid4())
        assert result.state == "problem"
        assert result.problem is not None
        assert result.problem.code is ModelErrorCode.PROVIDER_AUTH_FAILED

    @pytest.mark.anyio
    async def test_disabled_model_is_not_probed(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        state = self._stub(monkeypatch, enabled=False)
        result = await self._service(role).get_status(catalog_id=uuid.uuid4())
        assert result.problem is not None
        assert result.problem.code is ModelErrorCode.MODEL_NOT_ENABLED
        assert state["probes"] == 0

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "credentials",
        [{}, {"OPENAI_BASE_URL": "https://proxy.example.com/v1"}],
    )
    async def test_missing_credentials(
        self,
        monkeypatch: pytest.MonkeyPatch,
        role: Role,
        credentials: dict[str, str],
    ) -> None:
        """No stored credentials, or no API key among them, is a problem."""
        self._stub(monkeypatch, credentials=credentials)
        result = await self._service(role).get_status(catalog_id=uuid.uuid4())
        assert result.problem is not None
        assert result.problem.code is ModelErrorCode.CREDENTIALS_MISSING

    @pytest.mark.anyio
    async def test_cloud_provider_is_unverified_without_a_probe(
        self, monkeypatch: pytest.MonkeyPatch, role: Role
    ) -> None:
        state = self._stub(monkeypatch, provider="bedrock")
        result = await self._service(role).get_status(catalog_id=uuid.uuid4())
        assert result.state == "unverified"
        assert result.problem is None
        assert state["probes"] == 0
