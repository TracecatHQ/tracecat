"""Shared provider constants, credential checks, and source config helpers.

Used by catalog, credentials, selections, sources, and runtime services
to avoid duplicating provider-specific logic.
"""

from __future__ import annotations

import orjson
from cryptography.fernet import InvalidToken

from tracecat import config
from tracecat.agent.config import PROVIDER_CREDENTIAL_CONFIGS
from tracecat.agent.types import CustomModelSourceType, ModelSourceType
from tracecat.db.models import AgentSource
from tracecat.exceptions import TracecatNotFoundError
from tracecat.secrets.encryption import (
    decrypt_keyvalues,
    decrypt_value,
    encrypt_value,
)

BUILT_IN_PROVIDER_SOURCE_TYPES = {
    ModelSourceType.OPENAI,
    ModelSourceType.ANTHROPIC,
    ModelSourceType.GEMINI,
    ModelSourceType.BEDROCK,
    ModelSourceType.VERTEX_AI,
    ModelSourceType.AZURE_OPENAI,
    ModelSourceType.AZURE_AI,
}

BUILT_IN_PROVIDER_ORDER = (
    ModelSourceType.OPENAI,
    ModelSourceType.ANTHROPIC,
    ModelSourceType.GEMINI,
    ModelSourceType.VERTEX_AI,
    ModelSourceType.BEDROCK,
    ModelSourceType.AZURE_OPENAI,
    ModelSourceType.AZURE_AI,
)

CREDENTIAL_SECRET_NAME_TEMPLATE = "agent-{}-credentials"


def provider_label(provider: str) -> str:
    if provider in PROVIDER_CREDENTIAL_CONFIGS:
        return PROVIDER_CREDENTIAL_CONFIGS[provider].label
    return provider.replace("_", " ").title()


def ensure_builtin_provider(provider: str) -> str:
    if provider not in {source_type.value for source_type in BUILT_IN_PROVIDER_ORDER}:
        raise TracecatNotFoundError(f"Provider {provider} not found")
    return provider


def provider_base_url_key(provider: str) -> str | None:
    match provider:
        case "openai":
            return "OPENAI_BASE_URL"
        case "anthropic":
            return "ANTHROPIC_BASE_URL"
        case "azure_openai" | "azure_ai":
            return "AZURE_API_BASE"
        case _:
            return None


def provider_runtime_target(
    *, provider: str, credentials: dict[str, str] | None
) -> str | None:
    if not credentials:
        return None
    match provider:
        case "bedrock":
            return credentials.get("AWS_INFERENCE_PROFILE_ID") or credentials.get(
                "AWS_MODEL_ID"
            )
        case "vertex_ai":
            return credentials.get("VERTEX_AI_MODEL")
        case "azure_openai":
            return credentials.get("AZURE_DEPLOYMENT_NAME")
        case "azure_ai":
            return credentials.get("AZURE_AI_MODEL_NAME")
        case _:
            return None


def provider_credentials_complete(
    *, provider: str, credentials: dict[str, str] | None
) -> bool:
    if not credentials:
        return False
    match provider:
        case "openai":
            return bool(credentials.get("OPENAI_API_KEY"))
        case "anthropic":
            return bool(credentials.get("ANTHROPIC_API_KEY"))
        case "gemini":
            return bool(credentials.get("GEMINI_API_KEY"))
        case "bedrock":
            return bool(credentials.get("AWS_REGION"))
        case "vertex_ai":
            return bool(
                credentials.get("GOOGLE_API_CREDENTIALS")
                and credentials.get("GOOGLE_CLOUD_PROJECT")
            )
        case "azure_openai":
            return bool(
                credentials.get("AZURE_API_BASE")
                and credentials.get("AZURE_API_VERSION")
                and (
                    credentials.get("AZURE_API_KEY")
                    or credentials.get("AZURE_AD_TOKEN")
                )
            )
        case "azure_ai":
            return bool(
                credentials.get("AZURE_API_BASE") and credentials.get("AZURE_API_KEY")
            )
        case _:
            return True


def credential_secret_name(provider: str) -> str:
    return CREDENTIAL_SECRET_NAME_TEMPLATE.format(provider)


def deserialize_secret_keyvalues(payload: bytes | None) -> dict[str, str]:
    """Decrypt and deserialize an organization secret payload."""
    if not payload:
        return {}
    keyvalues = decrypt_keyvalues(
        payload,
        key=config.TRACECAT__DB_ENCRYPTION_KEY or "",
    )
    return {kv.key: kv.value.get_secret_value() for kv in keyvalues}


def serialize_source_config(payload: dict[str, str] | None) -> bytes | None:
    """Encrypt a source config dict for storage."""
    if not payload:
        return None
    return encrypt_value(
        orjson.dumps(payload),
        key=config.TRACECAT__DB_ENCRYPTION_KEY or "",
    )


def deserialize_source_config(payload: bytes | None) -> dict[str, str]:
    """Decrypt and deserialize a source config blob, returning {} on failure."""
    if not payload:
        return {}
    try:
        decrypted = decrypt_value(
            payload,
            key=config.TRACECAT__DB_ENCRYPTION_KEY or "",
        )
    except (InvalidToken, ValueError):
        try:
            return orjson.loads(payload)
        except orjson.JSONDecodeError:
            return {}
    return orjson.loads(decrypted)


def source_type_from_row(row: AgentSource) -> CustomModelSourceType:
    """Derive the custom source type from the stored row fields."""
    if row.declared_models:
        return CustomModelSourceType.MANUAL_CUSTOM
    if row.model_provider == CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY.value:
        return CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY
    return CustomModelSourceType.MANUAL_CUSTOM


def openai_compatible_runtime_base_url(url: str) -> str:
    """Normalize an OpenAI-compatible discovery URL to the /v1 base for runtime use."""
    base = url.strip().rstrip("/")
    if base.endswith("/v1/models"):
        return f"{base.removesuffix('/v1/models')}/v1"
    if base.endswith("/models"):
        return base.removesuffix("/models").rstrip("/")
    return base


def source_runtime_base_url(
    source: AgentSource,
    *,
    source_config: dict[str, str] | None = None,
) -> str | None:
    """Resolve the runtime base URL for a custom source row."""
    if not source.base_url:
        return None
    if source_type_from_row(source) != CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY:
        return source.base_url
    values = source_config or deserialize_source_config(source.encrypted_config)
    if runtime_base_url := values.get("runtime_base_url"):
        return runtime_base_url
    return openai_compatible_runtime_base_url(source.base_url)
