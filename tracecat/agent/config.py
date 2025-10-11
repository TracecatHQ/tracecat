from tracecat.agent.models import (
    ModelConfig,
    ProviderCredentialConfig,
    ProviderCredentialField,
)

# https://ai.pydantic.dev/api/models/base/
MODEL_CONFIGS = {
    # Maps the pydantic-ai model name to the Tracecat model config
    "gpt-4o-mini": ModelConfig(
        name="gpt-4o-mini",
        provider="openai",
        org_secret_name="agent-openai-credentials",
        secrets={
            "required": ["openai"],
        },
    ),
    "claude-3-5-sonnet-20240620": ModelConfig(
        name="claude-3-5-sonnet-20240620",
        provider="anthropic",
        org_secret_name="agent-anthropic-credentials",
        secrets={
            "required": ["anthropic"],
        },
    ),
    "us.anthropic.claude-sonnet-4-20250514-v1:0": ModelConfig(
        name="us.anthropic.claude-sonnet-4-20250514-v1:0",
        provider="bedrock",
        org_secret_name="agent-bedrock-credentials",
        secrets={
            "required": ["bedrock"],
        },
    ),
    "custom": ModelConfig(
        name="custom",
        provider="custom-model-provider",
        org_secret_name="agent-custom-model-credentials",
        secrets={
            "required": ["custom-model-provider"],
        },
    ),
}

PROVIDER_CREDENTIAL_CONFIGS = {
    "openai": ProviderCredentialConfig(
        provider="openai",
        label="OpenAI",
        fields=[
            ProviderCredentialField(
                key="OPENAI_API_KEY",
                label="API Key",
                type="password",
                description="Your OpenAI API key from the provider's dashboard.",
            )
        ],
    ),
    "anthropic": ProviderCredentialConfig(
        provider="anthropic",
        label="Anthropic",
        fields=[
            ProviderCredentialField(
                key="ANTHROPIC_API_KEY",
                label="API Key",
                type="password",
                description="Your Anthropic API key from the provider's dashboard.",
            )
        ],
    ),
    "bedrock": ProviderCredentialConfig(
        provider="bedrock",
        label="Anthropic (Bedrock)",
        fields=[
            ProviderCredentialField(
                key="AWS_ACCESS_KEY_ID",
                label="Access Key ID",
                type="text",
                description="Your AWS access key ID for Bedrock access.",
            ),
            ProviderCredentialField(
                key="AWS_SECRET_ACCESS_KEY",
                label="Secret Access Key",
                type="password",
                description="Your AWS secret access key for Bedrock access.",
            ),
            ProviderCredentialField(
                key="AWS_REGION",
                label="Region",
                type="text",
                description="The AWS region where you want to use Bedrock (e.g., us-east-1).",
            ),
        ],
    ),
    "custom-model-provider": ProviderCredentialConfig(
        provider="custom-model-provider",
        label="Custom LLM Provider",
        fields=[
            ProviderCredentialField(
                key="CUSTOM_MODEL_PROVIDER_API_KEY",
                label="API Key",
                type="password",
                description="Your custom model provider API key.",
                required=False,
            ),
            ProviderCredentialField(
                key="CUSTOM_MODEL_PROVIDER_BASE_URL",
                label="Base URL",
                type="text",
                description="The base URL for your custom model provider.",
            ),
            ProviderCredentialField(
                key="CUSTOM_MODEL_PROVIDER_MODEL_NAME",
                label="Model Name",
                type="text",
                description="The name of the model to use from your custom model provider.",
            ),
        ],
    ),
}
