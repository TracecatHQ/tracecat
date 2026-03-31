"""Provider registry for the Tracecat-owned LLM proxy.

This module intentionally stays small.
Each provider family owns its own shaping logic in a dedicated module.
The registry only wires those adapters together.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tracecat.agent.llm_proxy.provider_anthropic import AnthropicAdapter
from tracecat.agent.llm_proxy.provider_azure_ai import AzureAIAdapter
from tracecat.agent.llm_proxy.provider_bedrock import BedrockAdapter
from tracecat.agent.llm_proxy.provider_common import (
    AnthropicStreamingAdapter,
    PassthroughStreamAdapter,
    ProviderAdapter,
    ProviderRetryAdapter,
)
from tracecat.agent.llm_proxy.provider_google import GeminiAdapter, VertexAIAdapter
from tracecat.agent.llm_proxy.provider_openai import (
    OpenAICompatibleAdapter,
    OpenAIFamilyAdapter,
)


@dataclass(slots=True)
class ProviderRegistry:
    """Lookup table for provider adapters."""

    adapters: dict[str, ProviderAdapter] = field(default_factory=dict)

    def get(self, provider: str) -> ProviderAdapter:
        if adapter := self.adapters.get(provider):
            return adapter
        raise ValueError(f"Unsupported provider '{provider}'")

    @classmethod
    def default(cls) -> ProviderRegistry:
        return cls(
            adapters={
                "openai": OpenAIFamilyAdapter("openai"),
                "custom-model-provider": OpenAIFamilyAdapter("custom-model-provider"),
                "azure_openai": OpenAIFamilyAdapter("azure_openai"),
                "anthropic": AnthropicAdapter(),
                "gemini": GeminiAdapter(),
                "vertex_ai": VertexAIAdapter(),
                "bedrock": BedrockAdapter(),
                "azure_ai": AzureAIAdapter(),
            }
        )


__all__ = [
    "AnthropicAdapter",
    "AnthropicStreamingAdapter",
    "PassthroughStreamAdapter",
    "AzureAIAdapter",
    "BedrockAdapter",
    "GeminiAdapter",
    "OpenAICompatibleAdapter",
    "OpenAIFamilyAdapter",
    "ProviderAdapter",
    "ProviderRegistry",
    "ProviderRetryAdapter",
    "VertexAIAdapter",
]
