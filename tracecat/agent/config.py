from tracecat.agent.schemas import (
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
    "gpt-5-mini": ModelConfig(
        name="gpt-5-mini",
        provider="openai",
        org_secret_name="agent-openai-credentials",
        secrets={
            "required": ["openai"],
        },
    ),
    "gpt-5-nano": ModelConfig(
        name="gpt-5-nano",
        provider="openai",
        org_secret_name="agent-openai-credentials",
        secrets={
            "required": ["openai"],
        },
    ),
    "gpt-5": ModelConfig(
        name="gpt-5",
        provider="openai",
        org_secret_name="agent-openai-credentials",
        secrets={
            "required": ["openai"],
        },
    ),
    "gpt-5.2": ModelConfig(
        name="gpt-5.2",
        provider="openai",
        org_secret_name="agent-openai-credentials",
        secrets={
            "required": ["openai"],
        },
    ),
    "claude-sonnet-4-5-20250929": ModelConfig(
        name="claude-sonnet-4-5-20250929",
        provider="anthropic",
        org_secret_name="agent-anthropic-credentials",
        secrets={
            "required": ["anthropic"],
        },
    ),
    "claude-haiku-4-5-20251001": ModelConfig(
        name="claude-haiku-4-5-20251001",
        provider="anthropic",
        org_secret_name="agent-anthropic-credentials",
        secrets={
            "required": ["anthropic"],
        },
    ),
    "claude-opus-4-5-20251101": ModelConfig(
        name="claude-opus-4-5-20251101",
        provider="anthropic",
        org_secret_name="agent-anthropic-credentials",
        secrets={
            "required": ["anthropic"],
        },
    ),
    "gemini-2.5-flash": ModelConfig(
        name="gemini-2.5-flash",
        provider="gemini",
        org_secret_name="agent-gemini-credentials",
        secrets={
            "required": ["gemini"],
        },
    ),
    "gemini-2.5-pro": ModelConfig(
        name="gemini-2.5-pro",
        provider="gemini",
        org_secret_name="agent-gemini-credentials",
        secrets={
            "required": ["gemini"],
        },
    ),
    "gemini-3-flash-preview": ModelConfig(
        name="gemini-3-flash-preview",
        provider="gemini",
        org_secret_name="agent-gemini-credentials",
        secrets={
            "required": ["gemini"],
        },
    ),
    "gemini-3-pro-preview": ModelConfig(
        name="gemini-3-pro-preview",
        provider="gemini",
        org_secret_name="agent-gemini-credentials",
        secrets={
            "required": ["gemini"],
        },
    ),
    "vertex_ai": ModelConfig(
        name="vertex_ai",  # Placeholder; model name from VERTEX_AI_MODEL will be used at runtime
        provider="vertex_ai",
        org_secret_name="agent-vertex_ai-credentials",
        secrets={
            "required": ["vertex_ai"],
        },
    ),
    "bedrock": ModelConfig(
        name="bedrock",  # Placeholder; actual model ID from AWS_MODEL_ID credential will be used at runtime
        provider="bedrock",
        org_secret_name="agent-bedrock-credentials",
        secrets={
            "required": ["bedrock"],
        },
    ),
    "azure_openai": ModelConfig(
        name="azure_openai",  # Placeholder; deployment name from AZURE_DEPLOYMENT_NAME will be used at runtime
        provider="azure_openai",
        org_secret_name="agent-azure_openai-credentials",
        secrets={
            "required": ["azure_openai"],
        },
    ),
    "azure_ai": ModelConfig(
        name="azure_ai",  # Placeholder; model name from AZURE_AI_MODEL_NAME will be used at runtime
        provider="azure_ai",
        org_secret_name="agent-azure_ai-credentials",
        secrets={
            "required": ["azure_ai"],
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
            ),
            ProviderCredentialField(
                key="OPENAI_BASE_URL",
                label="Base URL override",
                type="text",
                description="Optional OpenAI-compatible base URL override for discovery and runtime.",
                required=False,
            ),
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
            ),
            ProviderCredentialField(
                key="ANTHROPIC_BASE_URL",
                label="Base URL override",
                type="text",
                description="Optional Anthropic-compatible base URL override for discovery and runtime.",
                required=False,
            ),
        ],
    ),
    "bedrock": ProviderCredentialConfig(
        provider="bedrock",
        label="AWS Bedrock",
        fields=[
            ProviderCredentialField(
                key="AWS_ACCESS_KEY_ID",
                label="Access Key ID",
                type="text",
                description="Optional static AWS access key. Leave blank to use ambient IAM credentials.",
                required=False,
            ),
            ProviderCredentialField(
                key="AWS_SECRET_ACCESS_KEY",
                label="Secret Access Key",
                type="password",
                description="Optional static AWS secret key paired with the access key.",
                required=False,
            ),
            ProviderCredentialField(
                key="AWS_BEARER_TOKEN_BEDROCK",
                label="Bearer token",
                type="password",
                description="Optional Bedrock bearer token instead of AWS access keys.",
                required=False,
            ),
            ProviderCredentialField(
                key="AWS_MODEL_ID",
                label="Default model ID override",
                type="text",
                description="Optional fallback target for legacy Bedrock models. Leave empty to use the selected built-in model.",
                required=False,
            ),
            ProviderCredentialField(
                key="AWS_INFERENCE_PROFILE_ID",
                label="Default inference profile override",
                type="text",
                description="Optional fallback target for newer Bedrock models. Leave empty to use the selected built-in model.",
                required=False,
            ),
            ProviderCredentialField(
                key="AWS_REGION",
                label="Region",
                type="text",
                description="AWS region for Bedrock requests, for example `us-east-1`.",
            ),
        ],
    ),
    "gemini": ProviderCredentialConfig(
        provider="gemini",
        label="Gemini API",
        fields=[
            ProviderCredentialField(
                key="GEMINI_API_KEY",
                label="API Key",
                type="password",
                description="Your Gemini API key from Google AI Studio.",
            )
        ],
    ),
    "vertex_ai": ProviderCredentialConfig(
        provider="vertex_ai",
        label="Google Vertex AI",
        fields=[
            ProviderCredentialField(
                key="GOOGLE_API_CREDENTIALS",
                label="Service account JSON",
                type="password",
                description="Service account JSON key with Vertex AI permissions.",
            ),
            ProviderCredentialField(
                key="GOOGLE_CLOUD_PROJECT",
                label="Google Cloud project",
                type="text",
                description="Google Cloud project ID used for Vertex AI requests.",
            ),
            ProviderCredentialField(
                key="VERTEX_AI_MODEL",
                label="Default model override",
                type="text",
                description="Optional fallback model. Leave empty to use the selected built-in model.",
                required=False,
            ),
            ProviderCredentialField(
                key="GOOGLE_CLOUD_LOCATION",
                label="Location",
                type="text",
                description="Vertex AI region (e.g., us-central1).",
                required=False,
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
    "azure_openai": ProviderCredentialConfig(
        provider="azure_openai",
        label="Azure OpenAI",
        fields=[
            ProviderCredentialField(
                key="AZURE_API_BASE",
                label="API Base URL",
                type="text",
                description="Your Azure OpenAI resource endpoint (e.g., https://<resource>.openai.azure.com).",
            ),
            ProviderCredentialField(
                key="AZURE_API_VERSION",
                label="API Version",
                type="text",
                description="The Azure OpenAI API version (e.g., 2024-02-15-preview).",
            ),
            ProviderCredentialField(
                key="AZURE_DEPLOYMENT_NAME",
                label="Default deployment override",
                type="text",
                description="Optional fallback deployment name. Leave empty to use the selected model name as the deployment name.",
                required=False,
            ),
            ProviderCredentialField(
                key="AZURE_API_KEY",
                label="API Key",
                type="password",
                description="Your Azure OpenAI API key. Required if not using Entra token.",
                required=False,
            ),
            ProviderCredentialField(
                key="AZURE_AD_TOKEN",
                label="Entra Token",
                type="password",
                description="Your Azure Entra (AD) token. Required if not using API key.",
                required=False,
            ),
        ],
    ),
    "azure_ai": ProviderCredentialConfig(
        provider="azure_ai",
        label="Azure AI",
        fields=[
            ProviderCredentialField(
                key="AZURE_API_BASE",
                label="API Base URL",
                type="text",
                description="Your Azure AI endpoint (e.g., https://<resource>.services.ai.azure.com/anthropic).",
            ),
            ProviderCredentialField(
                key="AZURE_API_KEY",
                label="API Key",
                type="password",
                description="Your Azure AI API key.",
            ),
            ProviderCredentialField(
                key="AZURE_AI_MODEL_NAME",
                label="Default model override",
                type="text",
                description="Optional fallback model. Leave empty to use the selected built-in model.",
                required=False,
            ),
        ],
    ),
}
