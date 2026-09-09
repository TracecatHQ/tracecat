"""Agent-owned diagnostic context, separate from runtime error classification."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class LLMErrorDiagnostics(BaseModel):
    """Safe request context for reporting an LLM failure.

    Route describes the request path, not credential ownership. Provider
    configuration describes the integration, not the provider's identity.
    Neither dimension determines error ownership or retry behavior.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["direct", "managed"]
    provider_configuration: Literal["builtin", "custom"] | None = None
