import os

import orjson
from google.oauth2 import service_account
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from tracecat_registry import secrets
from tracecat_registry.integrations.aws_boto3 import get_sync_session


def _build_openai_compatible_model(
    *,
    model_name: str,
    base_url: str | None,
    api_key_env: str = "OPENAI_API_KEY",
    default_api_key: str = "not-needed",
) -> OpenAIChatModel:
    return OpenAIChatModel(
        model_name=model_name,
        provider=OpenAIProvider(
            base_url=base_url,
            api_key=secrets.get_or_default(api_key_env, default_api_key),
        ),
    )


def get_model(
    model_name: str, model_provider: str, base_url: str | None = None
) -> Model:
    """Get a pydantic-ai Model instance for the specified provider and model.

    Uses Tracecat secrets manager environment sandbox to securely retrieve
    API credentials and authentication information for each provider.

    Args:
        model_name: The specific model identifier (e.g., "gpt-4o", "claude-3-sonnet")
        model_provider: The provider name ("openai", "anthropic", "gemini", etc.)
        base_url: Optional custom base URL for the provider's API endpoint

    Returns:
        A configured Model instance ready for use with pydantic-ai agents
    """
    match model_provider:
        case "openai_compatible_gateway" | "manual_custom":
            model = _build_openai_compatible_model(
                model_name=model_name,
                base_url=base_url,
            )
        case "custom-model-provider":
            # Expect custom models to follow the OpenAI API format
            effective_base_url = base_url or secrets.get(
                "CUSTOM_MODEL_PROVIDER_BASE_URL"
            )
            effective_model_name = secrets.get_or_default(
                "CUSTOM_MODEL_PROVIDER_MODEL_NAME", model_name
            )

            model = _build_openai_compatible_model(
                model_name=effective_model_name,
                base_url=effective_base_url,
                api_key_env="CUSTOM_MODEL_PROVIDER_API_KEY",
            )
        case "openai":
            model = _build_openai_compatible_model(
                model_name=model_name,
                base_url=base_url,
                default_api_key=secrets.get("OPENAI_API_KEY"),
            )
        case "ollama":
            model = OpenAIChatModel(
                model_name=model_name,
                provider=OllamaProvider(base_url=base_url),
            )
        case "openai_responses":
            model = OpenAIResponsesModel(
                model_name=model_name,
                provider=OpenAIProvider(
                    base_url=base_url, api_key=secrets.get("OPENAI_API_KEY")
                ),
            )
        case "anthropic":
            settings = AnthropicModelSettings(
                anthropic_thinking={
                    "type": "enabled",
                    "budget_tokens": 1024,
                }
            )
            model = AnthropicModel(
                model_name=model_name,
                provider=AnthropicProvider(api_key=secrets.get("ANTHROPIC_API_KEY")),
                settings=settings,
            )
        case "gemini":
            model = GoogleModel(
                model_name=model_name,
                provider=GoogleProvider(api_key=secrets.get("GEMINI_API_KEY")),
            )
        case "gemini_vertex" | "vertex_ai":
            credentials = service_account.Credentials.from_service_account_info(
                orjson.loads(secrets.get("GOOGLE_API_CREDENTIALS")),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            model = GoogleModel(
                model_name=model_name,
                provider=GoogleProvider(credentials=credentials),
            )
        case "bedrock":
            bearer_token = secrets.get_or_default("AWS_BEARER_TOKEN_BEDROCK")
            if bearer_token:
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = bearer_token

            session = get_sync_session()
            client = session.client(service_name="bedrock-runtime")
            settings = None
            if "anthropic" in model_name:
                settings = BedrockModelSettings(
                    bedrock_additional_model_requests_fields={
                        "thinking": {"type": "enabled", "budget_tokens": 1024}
                    }
                )
            model = BedrockConverseModel(
                model_name=model_name,
                provider=BedrockProvider(bedrock_client=client),
                settings=settings,
            )
        case _:
            raise ValueError(
                f"Unsupported model configuration: provider={model_provider}, model={model_name}"
            )

    return model
