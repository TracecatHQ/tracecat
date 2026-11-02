"""Agent-owned diagnostic context, separate from runtime error classification."""

from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict

MAX_LLM_ERROR_BODY_BYTES = 64 * 1024

type ProviderConfiguration = Literal["builtin", "custom"]
"""How the provider behind a route is integrated, not who the provider is."""

_CUSTOM_MODEL_PROVIDER = "custom-model-provider"


def provider_configuration_for(model_provider: str) -> ProviderConfiguration:
    """Classify how a model provider is integrated.

    Args:
        model_provider: Provider slug behind the model selection.

    Returns:
        ``custom`` for the custom model provider, ``builtin`` otherwise.
    """
    return "custom" if model_provider == _CUSTOM_MODEL_PROVIDER else "builtin"


def parse_bounded_error_body(body: bytes) -> object | None:
    """Parse a provider-controlled error body without unbounded work.

    Args:
        body: Raw upstream error body.

    Returns:
        Parsed JSON, or None when the body is empty, oversized, or not JSON.
    """
    if not body or len(body) > MAX_LLM_ERROR_BODY_BYTES:
        return None
    try:
        return orjson.loads(body)
    except orjson.JSONDecodeError:
        return None


class LLMErrorDiagnostics(BaseModel):
    """Safe request context for reporting an LLM failure.

    Route describes the request path, not credential ownership. Provider
    configuration describes the integration, not the provider's identity.
    Neither dimension determines error ownership or retry behavior.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["direct", "managed"]
    provider_configuration: ProviderConfiguration | None = None
