"""LLM route alias helpers for Claude Code through the managed gateway."""

from __future__ import annotations

_LITELLM_ROUTE_PREFIXES: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "vertex_ai": "vertex_ai",
    "bedrock": "bedrock",
    "azure_openai": "azure",
    "azure_ai": "azure_ai",
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

    if any(
        model_name.startswith(f"{prefix}/")
        for prefix in set(_LITELLM_ROUTE_PREFIXES.values())
    ):
        return model_name

    if prefix := _LITELLM_ROUTE_PREFIXES.get(model_provider):
        return f"{prefix}/{model_name}"

    return model_name
