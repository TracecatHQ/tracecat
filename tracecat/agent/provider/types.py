"""Domain types for LLM provider management."""

from dataclasses import dataclass
from enum import StrEnum


class CustomProviderType(StrEnum):
    """Explicit provider type driving discovery and validation."""

    GENERIC_OPENAI_COMPATIBLE = "generic_openai_compatible"
    LITELLM = "litellm"
    OLLAMA = "ollama"


@dataclass(kw_only=True, slots=True)
class ResolvedCustomProviderCredentials:
    """Decrypted credentials for a custom LLM provider."""

    api_key: str | None = None
    custom_headers: dict[str, str] | None = None


def ollama_gateway_root(base_url: str) -> str:
    """Strip a single trailing ``/v1`` suffix (Ollama native root). Idempotent."""
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return trimmed[: -len("/v1")]
    return trimmed


def ensure_ollama_v1(base_url: str) -> str:
    """Ensure a single trailing ``/v1`` (Ollama OpenAI-compat surface). Idempotent."""
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return trimmed
    return f"{trimmed}/v1"
