"""LLM route alias helpers for Claude Code through the managed gateway."""

from __future__ import annotations

from tracecat.agent.gateway_providers import GATEWAY_PROVIDER_SPECS

_LITELLM_ROUTE_PREFIXES: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "mistral": "mistral",
    "vertex_ai": "vertex_ai",
    "bedrock": "bedrock",
    "azure_openai": "azure",
    "azure_ai": "azure_ai",
} | {
    spec.slug: spec.litellm_prefix
    for spec in GATEWAY_PROVIDER_SPECS.values()
    if spec.litellm_prefix is not None
}


def get_litellm_route_model(
    *,
    model_provider: str,
    model_name: str,
    passthrough: bool = False,
) -> str:
    """Prefix model names so LiteLLM enters the intended provider route.

    Claude Code speaks to LiteLLM through the Anthropic-compatible
    ``/v1/messages`` surface. LiteLLM chooses the provider route from the
    incoming ``model`` string before Tracecat's credential hook rewrites the
    final provider-specific model ID, so unqualified model names can fall
    through to the OpenAI catch-all route.
    """
    if passthrough:
        # Direct upstream passthrough should preserve the configured model ID.
        return model_name

    prefix = _LITELLM_ROUTE_PREFIXES.get(model_provider)
    if prefix is None:
        return model_name

    # Only the provider's own prefix counts as "already routed". Aggregators
    # such as OpenRouter use vendor-qualified IDs (``anthropic/claude-...``)
    # that must still be wrapped so LiteLLM does not route to the vendor.
    if model_name.startswith(f"{prefix}/"):
        return model_name

    return f"{prefix}/{model_name}"
