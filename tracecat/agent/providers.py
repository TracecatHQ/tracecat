import os
from urllib.parse import urlsplit

import orjson
from anthropic import AsyncAnthropic
from google.oauth2 import service_account
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from tracecat_registry import secrets
from tracecat_registry.integrations.aws_boto3 import get_sync_session

SOURCE_API_KEY = "TRACECAT_SOURCE_API_KEY"
SOURCE_API_KEY_HEADER = "TRACECAT_SOURCE_API_KEY_HEADER"
SOURCE_API_VERSION = "TRACECAT_SOURCE_API_VERSION"


def _source_header_overrides() -> dict[str, str]:
    api_key = secrets.get_or_default(SOURCE_API_KEY)
    api_key_header = secrets.get_or_default(SOURCE_API_KEY_HEADER)
    if not api_key or not api_key_header or api_key_header.lower() == "authorization":
        return {}
    return {api_key_header: api_key}


def _source_default_query() -> dict[str, str]:
    if api_version := secrets.get_or_default(SOURCE_API_VERSION):
        return {"api-version": api_version}
    return {}


def _masked_source_headers(
    default_headers: dict[str, str], *, auth_header: str
) -> dict[str, str]:
    headers = dict(default_headers)
    headers.setdefault(auth_header, "")
    return headers


def _azure_uses_base_url(base_url: str) -> bool:
    path = urlsplit(base_url).path.rstrip("/")
    return path.endswith("/openai") or "/openai/deployments/" in path


def _strip_provider_prefix(model_name: str, prefix: str) -> str:
    prefixed = f"{prefix}/"
    if model_name.startswith(prefixed):
        return model_name.removeprefix(prefixed)
    return model_name


def _azure_openai_runtime_names(
    *, model_name: str, deployment_name: str | None
) -> tuple[str, str]:
    stripped_model_name = _strip_provider_prefix(model_name, "azure")
    resolved_deployment_name = _strip_provider_prefix(
        deployment_name or model_name, "azure"
    )
    request_model_name = stripped_model_name.rsplit("/", 1)[-1]
    return request_model_name, resolved_deployment_name


def _build_openai_compatible_model(
    *,
    model_name: str,
    base_url: str | None,
    api_key_env: str = "OPENAI_API_KEY",
    default_api_key: str = "not-needed",
) -> OpenAIChatModel:
    default_headers = _source_header_overrides()
    default_query = _source_default_query()
    api_key = secrets.get_or_default(api_key_env, default_api_key)
    if default_headers or default_query:
        default_headers = _masked_source_headers(
            default_headers, auth_header="Authorization"
        )
        provider = OpenAIProvider(
            openai_client=AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                default_headers=default_headers or None,
                default_query=default_query or None,
            )
        )
    else:
        provider = OpenAIProvider(
            base_url=base_url,
            api_key=api_key,
        )
    return OpenAIChatModel(
        model_name=model_name,
        provider=provider,
    )


def _build_azure_openai_model(
    *,
    model_name: str,
    base_url: str | None,
) -> OpenAIChatModel:
    if base_url is None:
        raise ValueError("azure_openai requires a configured base URL")

    api_version = (
        secrets.get_or_default(SOURCE_API_VERSION)
        or secrets.get_or_default("AZURE_API_VERSION")
        or secrets.get_or_default("OPENAI_API_VERSION")
    )
    if api_version is None:
        raise ValueError("azure_openai requires AZURE_API_VERSION")

    request_model_name, deployment_name = _azure_openai_runtime_names(
        model_name=model_name,
        deployment_name=secrets.get_or_default("AZURE_DEPLOYMENT_NAME"),
    )
    client_kwargs = {
        "api_version": api_version,
        "api_key": secrets.get_or_default("AZURE_API_KEY"),
        "azure_ad_token": secrets.get_or_default("AZURE_AD_TOKEN"),
        "default_headers": _source_header_overrides() or None,
        "default_query": _source_default_query() or None,
    }
    if "/openai/deployments/" in urlsplit(base_url).path:
        client = AsyncAzureOpenAI(base_url=base_url, **client_kwargs)
    elif _azure_uses_base_url(base_url):
        client = AsyncAzureOpenAI(
            base_url=f"{base_url.rstrip('/')}/deployments/{deployment_name}",
            **client_kwargs,
        )
    else:
        client = AsyncAzureOpenAI(
            azure_endpoint=base_url,
            azure_deployment=deployment_name,
            **client_kwargs,
        )

    return OpenAIChatModel(
        model_name=request_model_name,
        provider=AzureProvider(openai_client=client),
    )


def _build_azure_ai_model(
    *,
    model_name: str,
    base_url: str | None,
) -> OpenAIChatModel:
    if base_url is None:
        raise ValueError("azure_ai requires a configured base URL")

    default_headers = _source_header_overrides()
    if not default_headers:
        if api_key := secrets.get_or_default("AZURE_API_KEY"):
            default_headers["api-key"] = api_key
        default_headers["Authorization"] = ""

    return OpenAIChatModel(
        model_name=_strip_provider_prefix(
            secrets.get_or_default("AZURE_AI_MODEL_NAME", model_name),
            "azure_ai",
        ),
        provider=OpenAIProvider(
            openai_client=AsyncOpenAI(
                base_url=base_url,
                api_key="not-needed",
                default_headers=default_headers or None,
                default_query=_source_default_query() or None,
            )
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
        case "openai_compatible_gateway" | "manual_custom" | "direct_endpoint":
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
        case "azure_openai":
            model = _build_azure_openai_model(
                model_name=model_name,
                base_url=base_url,
            )
        case "azure_ai":
            model = _build_azure_ai_model(
                model_name=model_name,
                base_url=base_url,
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
            default_headers = _source_header_overrides()
            default_query = _source_default_query()
            api_key = secrets.get("ANTHROPIC_API_KEY")
            if default_headers or default_query:
                default_headers = _masked_source_headers(
                    default_headers, auth_header="X-Api-Key"
                )
                provider = AnthropicProvider(
                    anthropic_client=AsyncAnthropic(
                        api_key="not-needed",
                        base_url=base_url,
                        default_headers=default_headers or None,
                        default_query=default_query or None,
                    )
                )
            else:
                provider = AnthropicProvider(
                    api_key=api_key,
                    base_url=base_url,
                )
            model = AnthropicModel(
                model_name=model_name,
                provider=provider,
                settings=settings,
            )
        case "gemini":
            model = GoogleModel(
                model_name=model_name,
                provider=GoogleProvider(
                    api_key=secrets.get("GEMINI_API_KEY"),
                    base_url=base_url,
                ),
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
