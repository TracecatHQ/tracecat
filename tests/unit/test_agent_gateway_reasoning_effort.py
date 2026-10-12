"""Reasoning effort reaches each managed-gateway provider in its own format.

Each case runs the real gateway pre-call hook on a Claude Code-shaped
``/v1/messages`` request, then LiteLLM's real Anthropic-messages translation,
and captures the provider request body at the HTTP layer. Claude Code sends a
level as ``thinking: {"type": "adaptive"}`` plus ``output_config.effort``; an
"off" selection arrives without ``thinking``.
"""

import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import litellm
import pytest
import respx
from litellm.anthropic_interface import messages as anthropic_messages
from litellm.caching.dual_cache import DualCache
from litellm.llms.vertex_ai.vertex_llm_base import VertexBase
from litellm.proxy._types import UserAPIKeyAuth

from tracecat.agent.gateway import TracecatCallbackHandler

type BodyField = Callable[[dict[str, Any]], object]


def _top_level(key: str) -> BodyField:
    return lambda body: body.get(key)


def _openai_reasoning(body: dict[str, Any]) -> object:
    return (body.get("reasoning") or {}).get("effort")


def _gemini_thinking_budget(body: dict[str, Any]) -> object:
    thinking_config = body.get("generationConfig", {}).get("thinkingConfig", {})
    return thinking_config.get("thinkingBudget")


def _anthropic_effort(body: dict[str, Any]) -> object:
    return (body.get("output_config") or {}).get("effort")


def _summarized(effort: str) -> dict[str, str]:
    # litellm_config.yaml enables reasoning_auto_summary.
    return {"effort": effort, "summary": "detailed"}


# (provider, credentials, provider-facing field, expected value for low/high/max)
PROVIDER_CASES: list[tuple[str, dict[str, str], BodyField, tuple[object, ...]]] = [
    (
        "openai",
        {"OPENAI_API_KEY": "sk-test"},
        _openai_reasoning,
        ("low", "high", "xhigh"),
    ),
    (
        "azure_openai",
        {
            "AZURE_API_BASE": "https://example.openai.azure.com",
            "AZURE_API_VERSION": "2025-04-01-preview",
            "AZURE_DEPLOYMENT_NAME": "gpt-5-mini",
            "AZURE_API_KEY": "test-key",
        },
        _top_level("reasoning_effort"),
        ("low", "high", "high"),
    ),
    (
        "gemini",
        {"GEMINI_API_KEY": "test-key"},
        _gemini_thinking_budget,
        (1024, 4096, 4096),
    ),
    (
        "vertex_ai",
        {
            "GOOGLE_API_CREDENTIALS": "{}",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "VERTEX_AI_MODEL": "gemini-2.5-flash",
        },
        _gemini_thinking_budget,
        (1024, 4096, 4096),
    ),
    (
        "vertex_ai",
        {
            "GOOGLE_API_CREDENTIALS": "{}",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-east5",
            "VERTEX_AI_MODEL": "claude-opus-5",
        },
        _anthropic_effort,
        ("low", "high", "max"),
    ),
    (
        "openrouter",
        {"OPENROUTER_API_KEY": "test-key"},
        _top_level("reasoning_effort"),
        (_summarized("low"), _summarized("high"), _summarized("high")),
    ),
    (
        "vllm",
        {"VLLM_BASE_URL": "http://vllm.example.invalid/v1", "VLLM_API_KEY": "k"},
        _top_level("reasoning_effort"),
        (_summarized("low"), _summarized("high"), _summarized("high")),
    ),
    (
        "litellm",
        {"LITELLM_BASE_URL": "http://litellm.example.invalid", "LITELLM_API_KEY": "k"},
        _top_level("reasoning_effort"),
        (_summarized("low"), _summarized("high"), _summarized("high")),
    ),
    (
        "custom-model-provider",
        {
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "http://custom.example.invalid/v1",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "qwen3",
        },
        _top_level("reasoning_effort"),
        (_summarized("low"), _summarized("high"), _summarized("high")),
    ),
    # The gateway drops thinking for Ollama, whose `think` flag is on/off only.
    # LiteLLM sends no reasoning parameter for Mistral, or for Azure AI models it
    # does not know to support one. The model default applies to all three.
    (
        "ollama",
        {"OLLAMA_BASE_URL": "http://ollama.example.invalid"},
        _top_level("think"),
        (None, None, None),
    ),
    (
        "mistral",
        {"MISTRAL_API_KEY": "test-key"},
        _top_level("reasoning_effort"),
        (None, None, None),
    ),
    (
        "azure_ai",
        {
            "AZURE_API_BASE": "https://example.services.ai.azure.com/models",
            "AZURE_AI_MODEL_NAME": "grok-4",
            "AZURE_API_KEY": "test-key",
        },
        _top_level("reasoning_effort"),
        (None, None, None),
    ),
]

MODELS = {
    "openai": "gpt-5.4-mini",
    "openrouter": "google/gemini-2.5-flash",
    "gemini": "gemini-2.5-flash",
    "mistral": "magistral-medium-latest",
    "vllm": "qwen3",
    "litellm": "gpt-5",
    "ollama": "qwen3",
}


@pytest.fixture(autouse=True)
def litellm_gateway_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror tracecat/agent/litellm_config.yaml and route LiteLLM over httpx."""
    monkeypatch.setattr(litellm, "drop_params", True)
    monkeypatch.setattr(litellm, "reasoning_auto_summary", True)
    monkeypatch.setattr(litellm, "disable_aiohttp_transport", True)
    # LiteLLM may load its model map remotely; the bundled copy lacks this
    # flag, and drop_params then drops the effort. Pin it so runs are offline-safe.
    openrouter_model = "openrouter/google/gemini-2.5-flash"
    monkeypatch.setitem(
        litellm.model_cost,
        openrouter_model,
        {**litellm.model_cost.get(openrouter_model, {}), "supports_reasoning": True},
    )

    def access_token(*_: object, **__: object) -> tuple[str, str]:
        return "test-token", "test-project"

    async def access_token_async(*_: object, **__: object) -> tuple[str, str]:
        return access_token()

    monkeypatch.setattr(VertexBase, "_ensure_access_token", access_token)
    monkeypatch.setattr(VertexBase, "_ensure_access_token_async", access_token_async)


async def _pre_call(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    credentials: dict[str, str],
    request: dict[str, Any],
) -> dict[str, Any]:
    async def get_provider_credentials(**_: object) -> dict[str, str]:
        return credentials

    monkeypatch.setattr(
        "tracecat.agent.gateway.get_provider_credentials", get_provider_credentials
    )
    auth = UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "organization_id": "00000000-0000-0000-0000-000000000002",
            "model": MODELS.get(provider, "unused"),
            "provider": provider,
            "model_settings": {},
            "use_workspace_credentials": True,
        },
    )
    return await TracecatCallbackHandler().async_pre_call_hook(
        user_api_key_dict=auth,
        cache=cast(DualCache, object()),
        data=request,
        call_type="anthropic_messages",
    )


async def _provider_request_body(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    credentials: dict[str, str],
    request: dict[str, Any],
) -> dict[str, Any]:
    data = await _pre_call(
        monkeypatch, provider=provider, credentials=credentials, request=request
    )
    # The guarded outbound client only changes transport, not the request body.
    data.pop("client", None)

    bodies: list[dict[str, Any]] = []

    def capture(outbound: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(outbound.content))
        return httpx.Response(400, json={"error": {"message": "captured"}})

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=capture)
        with pytest.raises(Exception, match="captured"):
            await anthropic_messages.acreate(**data)
    # Some adapters look the model up first (Ollama's /api/show); the
    # generation request is always the last one sent.
    return bodies[-1]


def _cli_request(
    *, thinking: dict[str, str] | None, effort: str | None
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": "claude-code-route",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if thinking is not None:
        request["thinking"] = thinking
    if effort is not None:
        request["output_config"] = {"effort": effort, "task_budget": 2048}
    return request


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "credentials", "field", "expected"),
    PROVIDER_CASES,
    ids=[f"{case[0]}-{index}" for index, case in enumerate(PROVIDER_CASES)],
)
async def test_effort_levels_reach_provider_in_native_format(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    credentials: dict[str, str],
    field: BodyField,
    expected: tuple[object, ...],
) -> None:
    """Low, high, and max stay distinct wherever the provider supports levels.

    Max maps to the provider's top level (`xhigh` on OpenAI, `high` elsewhere);
    providers without levels fall back as their cases note.
    """
    actual = []
    for effort in ("low", "high", "max"):
        body = await _provider_request_body(
            monkeypatch,
            provider=provider,
            credentials=credentials,
            request=_cli_request(thinking={"type": "adaptive"}, effort=effort),
        )
        actual.append(field(body))

    assert tuple(actual) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "credentials", "field"),
    [
        (provider, credentials, field)
        for provider, credentials, field, _ in PROVIDER_CASES
        if provider != "vertex_ai"
    ],
)
async def test_off_without_thinking_sends_no_reasoning_parameter(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    credentials: dict[str, str],
    field: BodyField,
) -> None:
    """Claude Code drops disabled thinking for non-Claude models, so "off"
    reaches non-Anthropic providers without a reasoning parameter and the
    model default applies."""
    body = await _provider_request_body(
        monkeypatch,
        provider=provider,
        credentials=credentials,
        request=_cli_request(thinking=None, effort="low"),
    )

    assert field(body) is None
    assert "output_config" not in body


@pytest.mark.anyio
async def test_non_anthropic_provider_keeps_only_output_config_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Effort survives the gateway; other Anthropic-only output_config keys don't."""
    data = await _pre_call(
        monkeypatch,
        provider="openai",
        credentials={"OPENAI_API_KEY": "sk-test"},
        request=_cli_request(thinking={"type": "adaptive"}, effort="high"),
    )

    assert data["output_config"] == {"effort": "high"}


@pytest.mark.anyio
async def test_bedrock_drops_reasoning_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bedrock keeps its existing behavior: no thinking or effort is forwarded."""
    data = await _pre_call(
        monkeypatch,
        provider="bedrock",
        credentials={
            "AWS_ACCESS_KEY_ID": "test-access-key",
            "AWS_SECRET_ACCESS_KEY": "test-secret-key",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-opus-5",
        },
        request=_cli_request(thinking={"type": "adaptive"}, effort="high"),
    )

    assert "thinking" not in data
    assert "output_config" not in data
