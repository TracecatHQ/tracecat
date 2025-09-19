import orjson
from google.oauth2 import service_account
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from tracecat_registry import RegistrySecret, secrets
from tracecat_registry.integrations.aws_boto3 import get_sync_session

anthropic_secret = RegistrySecret(
    name="anthropic",
    optional_keys=["ANTHROPIC_API_KEY"],
    optional=True,
)
"""Anthropic API key.

- name: `anthropic`
- optional_keys:
    - `ANTHROPIC_API_KEY`: Optional Anthropic API key.
"""

openai_secret = RegistrySecret(
    name="openai",
    optional_keys=["OPENAI_API_KEY"],
    optional=True,
)
"""OpenAI API key.

- name: `openai`
- optional_keys:
    - `OPENAI_API_KEY`: Optional OpenAI API key.
"""

gemini_secret = RegistrySecret(
    name="gemini",
    optional_keys=["GEMINI_API_KEY"],
    optional=True,
)
"""Gemini API key.

- name: `gemini`
- optional_keys:
    - `GEMINI_API_KEY`: Optional Gemini API key.
"""


bedrock_secret = RegistrySecret(
    name="amazon_bedrock",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AWS_SESSION_TOKEN",
    ],
    optional=True,
)
"""AWS credentials.

- name: `amazon_bedrock`
- optional_keys:
    Either:
        - `AWS_ACCESS_KEY_ID`
        - `AWS_SECRET_ACCESS_KEY`
        - `AWS_REGION`
    Or:
        - `AWS_PROFILE`
    Or:
        - `AWS_ROLE_ARN`
        - `AWS_ROLE_SESSION_NAME` (optional)
    Or:
        - `AWS_SESSION_TOKEN`
"""


google_secret = RegistrySecret(
    name="google",
    optional_keys=["GOOGLE_API_CREDENTIALS"],
    optional=True,
)
"""Google API credentials.

- name: `google`
- optional_keys:
    - `GOOGLE_API_CREDENTIALS`: Optional Google API credentials.

Note: `GOOGLE_API_CREDENTIALS` should be a JSON string of the service account credentials.
"""


custom_model_provider_secret = RegistrySecret(
    name="custom-model-provider",
    optional_keys=[
        "CUSTOM_MODEL_PROVIDER_API_KEY",
        "CUSTOM_MODEL_PROVIDER_MODEL_NAME",
        "CUSTOM_MODEL_PROVIDER_BASE_URL",
    ],
    optional=True,
)
"""Custom model provider credentials.

- name: `custom-model-provider`
- optional_keys:
    - `CUSTOM_MODEL_PROVIDER_API_KEY`: Optional custom model provider API key.
    - `CUSTOM_MODEL_PROVIDER_MODEL_NAME`: Optional custom model provider model name.
    - `CUSTOM_MODEL_PROVIDER_BASE_URL`: Optional custom model provider base URL.
"""

langfuse_secret = RegistrySecret(
    name="langfuse",
    optional_keys=[
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ],
    optional=True,
)
"""Langfuse observability configuration.

- name: `langfuse`
- optional_keys:
    - `LANGFUSE_HOST`: Optional Langfuse host URL.
    - `LANGFUSE_PUBLIC_KEY`: Optional Langfuse public key.
    - `LANGFUSE_SECRET_KEY`: Optional Langfuse secret key.
"""

PYDANTIC_AI_REGISTRY_SECRETS = [
    anthropic_secret,
    openai_secret,
    gemini_secret,
    bedrock_secret,
    custom_model_provider_secret,
    langfuse_secret,
]


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
        case "custom-model-provider":
            # Expect custom models to follow the OpenAI API format
            effective_base_url = base_url or secrets.get(
                "CUSTOM_MODEL_PROVIDER_BASE_URL"
            )
            effective_model_name = secrets.get_or_default(
                "CUSTOM_MODEL_PROVIDER_MODEL_NAME", model_name
            )

            model = OpenAIChatModel(
                model_name=effective_model_name,
                provider=OpenAIProvider(
                    base_url=effective_base_url,
                    api_key=secrets.get_or_default("CUSTOM_MODEL_PROVIDER_API_KEY"),
                ),
            )
        case "openai":
            model = OpenAIChatModel(
                model_name=model_name,
                provider=OpenAIProvider(
                    base_url=base_url, api_key=secrets.get("OPENAI_API_KEY")
                ),
            )
        case "openai_responses":
            model = OpenAIResponsesModel(
                model_name=model_name,
                provider=OpenAIProvider(
                    base_url=base_url, api_key=secrets.get("OPENAI_API_KEY")
                ),
            )
        case "anthropic":
            model = AnthropicModel(
                model_name=model_name,
                provider=AnthropicProvider(api_key=secrets.get("ANTHROPIC_API_KEY")),
            )
        case "gemini":
            model = GoogleModel(
                model_name=model_name,
                provider=GoogleProvider(api_key=secrets.get("GEMINI_API_KEY")),
            )
        case "gemini_vertex":
            credentials = service_account.Credentials.from_service_account_info(
                orjson.loads(secrets.get("GOOGLE_API_CREDENTIALS")),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            model = GoogleModel(
                model_name=model_name,
                provider=GoogleProvider(credentials=credentials),
            )
        case "bedrock":
            session = get_sync_session()
            client = session.client(service_name="bedrock-runtime")
            model = BedrockConverseModel(
                model_name=model_name,
                provider=BedrockProvider(bedrock_client=client),
            )
        case _:
            raise ValueError(
                f"Unsupported model configuration: provider={model_provider}, model={model_name}"
            )

    return model
