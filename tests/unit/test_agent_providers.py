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


def test_get_model_supports_azure_openai_with_native_azure_provider() -> None:
    token = secrets.set_context(
        {
            "AZURE_API_KEY": "azure-key",
            "AZURE_API_VERSION": "2024-02-15-preview",
        }
    )
    try:
        model = get_model(
            model_name="azure/eu/gpt-5.1",
            model_provider="azure_openai",
            base_url="https://example.openai.azure.com",
        )
    finally:
        secrets.reset_context(token)

    assert isinstance(model, OpenAIChatModel)
    assert model.system == "azure"
    assert (
        model.base_url
        == "https://example.openai.azure.com/openai/deployments/eu/gpt-5.1/"
    )
    assert model.model_name == "gpt-5.1"
    assert model.client.default_query == {"api-version": "2024-02-15-preview"}


def test_get_model_supports_expanded_azure_openai_base_url() -> None:
    token = secrets.set_context(
        {
            "AZURE_AD_TOKEN": "entra-token",
            "AZURE_API_VERSION": "2024-02-15-preview",
        }
    )
    try:
        model = get_model(
            model_name="azure/eu/gpt-5.1",
            model_provider="azure_openai",
            base_url="https://example.openai.azure.com/openai",
        )
    finally:
        secrets.reset_context(token)

    assert isinstance(model, OpenAIChatModel)
    assert model.system == "azure"
    assert (
        model.base_url
        == "https://example.openai.azure.com/openai/deployments/eu/gpt-5.1/"
    )
    assert model.model_name == "gpt-5.1"
    assert model.client.default_query == {"api-version": "2024-02-15-preview"}


def test_get_model_supports_azure_ai_with_api_key_header() -> None:
    token = secrets.set_context({"AZURE_API_KEY": "azure-ai-key"})
    try:
        model = get_model(
            model_name="claude-sonnet-4-5",
            model_provider="azure_ai",
            base_url="https://example.services.ai.azure.com/models",
        )
    finally:
        secrets.reset_context(token)

    assert isinstance(model, OpenAIChatModel)
    assert model.system == "openai"
    assert model.base_url == "https://example.services.ai.azure.com/models/"
    assert model.model_name == "claude-sonnet-4-5"
    assert model.client.default_headers["api-key"] == "azure-ai-key"
    assert model.client.default_headers["Authorization"] == ""


def test_get_model_uses_azure_deployment_override_without_losing_model_profile_name() -> (
    None
):
    token = secrets.set_context(
        {
            "AZURE_API_KEY": "azure-key",
            "AZURE_API_VERSION": "2024-02-15-preview",
            "AZURE_DEPLOYMENT_NAME": "prod-gpt-5",
        }
    )
    try:
        model = get_model(
            model_name="azure/eu/gpt-5.1",
            model_provider="azure_openai",
            base_url="https://example.openai.azure.com",
        )
    finally:
        secrets.reset_context(token)

    assert isinstance(model, OpenAIChatModel)
    assert (
        model.base_url
        == "https://example.openai.azure.com/openai/deployments/prod-gpt-5/"
    )
    assert model.model_name == "gpt-5.1"
