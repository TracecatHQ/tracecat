from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from tracecat_registry import secrets

from tracecat.agent.providers import get_model


def test_get_model_supports_direct_endpoint_provider() -> None:
    model = get_model(
        model_name="qwen2.5:7b",
        model_provider="direct_endpoint",
        base_url="http://localhost:11434/v1",
    )

    assert isinstance(model, OpenAIChatModel)


def test_get_model_passes_base_url_to_anthropic_provider() -> None:
    token = secrets.set_context({"ANTHROPIC_API_KEY": "test-key"})
    try:
        model = get_model(
            model_name="claude-3-7-sonnet",
            model_provider="anthropic",
            base_url="https://anthropic.gateway.example",
        )
    finally:
        secrets.reset_context(token)

    assert isinstance(model, AnthropicModel)
    assert model.base_url == "https://anthropic.gateway.example"


def test_get_model_passes_base_url_to_gemini_provider() -> None:
    token = secrets.set_context({"GEMINI_API_KEY": "test-key"})
    try:
        model = get_model(
            model_name="gemini-2.5-flash",
            model_provider="gemini",
            base_url="https://gemini.gateway.example",
        )
    finally:
        secrets.reset_context(token)

    assert isinstance(model, GoogleModel)
    assert model.base_url == "https://gemini.gateway.example"


def test_get_model_applies_source_query_and_header_overrides_to_openai_client() -> None:
    token = secrets.set_context(
        {
            "TRACECAT_SOURCE_API_KEY": "source-key",
            "TRACECAT_SOURCE_API_KEY_HEADER": "X-Api-Key",
            "TRACECAT_SOURCE_API_VERSION": "2024-06-01",
        }
    )
    try:
        model = get_model(
            model_name="gpt-5",
            model_provider="openai_compatible_gateway",
            base_url="https://gateway.example/v1",
        )
    finally:
        secrets.reset_context(token)

    assert isinstance(model, OpenAIChatModel)
    assert model.base_url == "https://gateway.example/v1/"
    assert model.client.default_headers["X-Api-Key"] == "source-key"
    assert model.client.default_headers["Authorization"] == ""
    assert model.client.default_query == {"api-version": "2024-06-01"}
