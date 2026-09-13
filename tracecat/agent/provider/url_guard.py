"""SSRF guard for caller-supplied LLM provider base URLs.

Custom providers and the built-in gateway providers (Ollama, vLLM, LiteLLM,
OpenRouter) let org members store a ``base_url`` that Tracecat later requests
server-side, both for ``GET {base_url}/models`` discovery and for inference.
Every such URL passes through :func:`validate_llm_provider_url` before it is
persisted or fetched so it cannot target loopback, private, link-local, or
cloud-metadata addresses. Single-tenant deployments that run a model server on
the same network opt out with ``TRACECAT__AGENT_ALLOW_PRIVATE_LLM_HOSTS``.
"""

from __future__ import annotations

from tracecat import config
from tracecat.network import DisallowedUrlError, validate_url_resolves_public_async


class LLMProviderUrlNotAllowedError(ValueError):
    """Raised when an LLM provider base URL resolves to a disallowed address.

    Subclasses :class:`ValueError` so existing ``ValueError`` handlers in the
    provider routers surface it as a 400 without echoing the resolved address.
    """

    def __init__(self) -> None:
        super().__init__(
            "Provider base URL is not allowed: it must resolve to a public "
            "address. Set TRACECAT__AGENT_ALLOW_PRIVATE_LLM_HOSTS=true to "
            "permit private or internal hosts on this deployment."
        )


async def validate_llm_provider_url(url: str) -> None:
    """Reject ``url`` unless its host resolves to publicly routable addresses.

    Args:
        url: Provider base URL supplied by an org member.

    Raises:
        LLMProviderUrlNotAllowedError: If the host is missing, cannot be
            resolved, or resolves to a non-public address while
            ``TRACECAT__AGENT_ALLOW_PRIVATE_LLM_HOSTS`` is off.
    """
    if config.TRACECAT__AGENT_ALLOW_PRIVATE_LLM_HOSTS:
        return
    try:
        await validate_url_resolves_public_async(url)
    except DisallowedUrlError as exc:
        raise LLMProviderUrlNotAllowedError() from exc
