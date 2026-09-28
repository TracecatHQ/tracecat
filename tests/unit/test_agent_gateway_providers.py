import pytest
from litellm.proxy._types import ProxyException

from tracecat.agent.config import PROVIDER_CREDENTIAL_CONFIGS, provider_display_rank
from tracecat.agent.gateway import _inject_provider_credentials
from tracecat.agent.gateway_providers import (
    OLLAMA_DEFAULT_BASE_URL,
    OPENROUTER_DEFAULT_BASE_URL,
    is_builtin_gateway_provider,
    is_gateway_provider,
    resolve_gateway_provider_config,
)
from tracecat.agent.llm_routing import get_litellm_route_model

pytestmark = pytest.mark.usefixtures("mock_org_id")


def test_provider_display_order_matches_requested_ordering() -> None:
    ordered = sorted(PROVIDER_CREDENTIAL_CONFIGS.keys(), key=provider_display_rank)
    assert ordered[:2] == ["openai", "anthropic"]
    assert ordered.index("bedrock") < ordered.index("azure_openai")
    assert ordered.index("gemini") < ordered.index("mistral")
    assert ordered[-5:] == [
        "ollama",
        "vllm",
        "litellm",
        "openrouter",
        "custom-model-provider",
    ]


def test_gateway_provider_classification() -> None:
    for provider in ("ollama", "vllm", "litellm", "openrouter"):
        assert is_builtin_gateway_provider(provider)
        assert is_gateway_provider(provider)
    assert is_gateway_provider("custom-model-provider")
    assert not is_builtin_gateway_provider("custom-model-provider")
    assert not is_gateway_provider("openai")


def test_resolve_ollama_defaults() -> None:
    runtime = resolve_gateway_provider_config("ollama", {})
    assert runtime is not None
    assert runtime.base_url == OLLAMA_DEFAULT_BASE_URL
    assert runtime.passthrough is False
    assert runtime.api_key is None


def test_resolve_openrouter_default_and_override() -> None:
    default = resolve_gateway_provider_config(
        "openrouter", {"OPENROUTER_API_KEY": "sk-or"}
    )
    assert default is not None
    assert default.base_url == OPENROUTER_DEFAULT_BASE_URL
    assert default.api_key == "sk-or"

    override = resolve_gateway_provider_config(
        "openrouter",
        {
            "OPENROUTER_API_KEY": "sk-or",
            "OPENROUTER_BASE_URL": "https://router.example.com/api/v1",
        },
    )
    assert override is not None
    assert override.base_url == "https://router.example.com/api/v1"


def test_resolve_litellm_defaults_to_passthrough() -> None:
    runtime = resolve_gateway_provider_config(
        "litellm", {"LITELLM_BASE_URL": "http://litellm:4000"}
    )
    assert runtime is not None
    assert runtime.passthrough is True

    disabled = resolve_gateway_provider_config(
        "litellm",
        {"LITELLM_BASE_URL": "http://litellm:4000", "LITELLM_PASSTHROUGH": "false"},
    )
    assert disabled is not None
    assert disabled.passthrough is False


def test_resolve_returns_none_for_non_gateway_provider() -> None:
    assert resolve_gateway_provider_config("openai", {}) is None


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("ollama", "llama3.1", "ollama_chat/llama3.1"),
        ("vllm", "meta-llama/Llama-3-8b", "hosted_vllm/meta-llama/Llama-3-8b"),
        ("litellm", "gpt-4o", "litellm_proxy/gpt-4o"),
        (
            "openrouter",
            "anthropic/claude-sonnet-4",
            "openrouter/anthropic/claude-sonnet-4",
        ),
        ("openrouter", "openrouter/openai/gpt-4o", "openrouter/openai/gpt-4o"),
    ],
)
def test_managed_route_prefixes(provider: str, model: str, expected: str) -> None:
    assert (
        get_litellm_route_model(model_provider=provider, model_name=model) == expected
    )


def test_passthrough_route_preserves_model_id() -> None:
    assert (
        get_litellm_route_model(
            model_provider="litellm", model_name="gpt-4o", passthrough=True
        )
        == "gpt-4o"
    )


def test_ollama_gateway_injection_strips_v1_and_uses_placeholder_key() -> None:
    data = {"model": "llama3.1"}

    _inject_provider_credentials(data, "ollama", {})

    assert data["api_base"] == "http://localhost:11434"
    assert data["api_key"] == "sk-no-auth"
    assert data["model"] == "ollama_chat/llama3.1"


def test_vllm_gateway_injection_requires_base_url() -> None:
    with pytest.raises(ProxyException):
        _inject_provider_credentials({"model": "x"}, "vllm", {})


def test_openrouter_gateway_injection_requires_api_key() -> None:
    with pytest.raises(ProxyException):
        _inject_provider_credentials(
            {"model": "anthropic/claude-sonnet-4"}, "openrouter", {}
        )

    data = {"model": "anthropic/claude-sonnet-4"}
    _inject_provider_credentials(data, "openrouter", {"OPENROUTER_API_KEY": "sk-or"})
    assert data["api_key"] == "sk-or"
    assert data["api_base"] == OPENROUTER_DEFAULT_BASE_URL
    assert data["model"] == "openrouter/anthropic/claude-sonnet-4"
