"""OpenAI-compatible gateway providers that can bypass the managed LiteLLM proxy.

Ollama, vLLM, LiteLLM, and OpenRouter all expose an OpenAI-compatible HTTP
surface. Tracecat treats them as first-class providers backed by an
``agent-{slug}-credentials`` org secret, discovers their models from
``{base_url}/models``, and lets each one either route through the managed
LiteLLM gateway or forward requests directly upstream (passthrough).

The ``custom-model-provider`` slug shares the same passthrough semantics but
keeps its own credential keys and model-name override, so it is registered
here alongside the built-in gateway providers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

CUSTOM_MODEL_PROVIDER_SLUG = "custom-model-provider"

type GatewayProviderSlug = Literal[
    "ollama",
    "vllm",
    "litellm",
    "openrouter",
    "custom-model-provider",
]

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_TRUTHY_FLAGS = frozenset({"1", "true", "yes", "on"})
_OPENAI_VERSION_SUFFIX_RE = re.compile(r"/v\d+/?$")


@dataclass(frozen=True, slots=True)
class GatewayProviderSpec:
    """Static description of an OpenAI-compatible provider.

    Attributes:
        slug: Provider slug used in catalog rows and credential secrets.
        api_key_key: Credential key holding the upstream API key.
        base_url_key: Credential key holding the OpenAI-compatible base URL.
        passthrough_key: Credential key holding the passthrough flag.
        model_name_key: Optional credential key overriding the model name.
        litellm_prefix: LiteLLM provider route prefix for managed requests.
        default_base_url: Base URL used when the credential is unset.
        default_passthrough: Passthrough behaviour when the flag is unset.
        litellm_api_base_strips_version: Whether the LiteLLM adapter expects
            the host root rather than the ``/v1`` OpenAI-compatible base.
        requires_api_key: Whether managed LiteLLM calls need a real API key.
    """

    slug: GatewayProviderSlug
    api_key_key: str
    base_url_key: str
    passthrough_key: str
    model_name_key: str | None
    litellm_prefix: str | None
    default_base_url: str | None
    default_passthrough: bool
    litellm_api_base_strips_version: bool = False
    requires_api_key: bool = False


@dataclass(frozen=True, slots=True)
class GatewayProviderRuntimeConfig:
    """Resolved runtime settings for one gateway provider credential set."""

    base_url: str | None
    passthrough: bool
    api_key: str | None
    model_name: str | None


GATEWAY_PROVIDER_SPECS: dict[str, GatewayProviderSpec] = {
    "ollama": GatewayProviderSpec(
        slug="ollama",
        api_key_key="OLLAMA_API_KEY",
        base_url_key="OLLAMA_BASE_URL",
        passthrough_key="OLLAMA_PASSTHROUGH",
        model_name_key=None,
        litellm_prefix="ollama_chat",
        default_base_url=OLLAMA_DEFAULT_BASE_URL,
        default_passthrough=False,
        litellm_api_base_strips_version=True,
    ),
    "vllm": GatewayProviderSpec(
        slug="vllm",
        api_key_key="VLLM_API_KEY",
        base_url_key="VLLM_BASE_URL",
        passthrough_key="VLLM_PASSTHROUGH",
        model_name_key=None,
        litellm_prefix="hosted_vllm",
        default_base_url=None,
        default_passthrough=False,
    ),
    "litellm": GatewayProviderSpec(
        slug="litellm",
        api_key_key="LITELLM_API_KEY",
        base_url_key="LITELLM_BASE_URL",
        passthrough_key="LITELLM_PASSTHROUGH",
        model_name_key=None,
        litellm_prefix="litellm_proxy",
        default_base_url=None,
        # A LiteLLM proxy already speaks the Anthropic Messages API, so
        # chaining it behind Tracecat's own LiteLLM gateway is redundant.
        default_passthrough=True,
    ),
    "openrouter": GatewayProviderSpec(
        slug="openrouter",
        api_key_key="OPENROUTER_API_KEY",
        base_url_key="OPENROUTER_BASE_URL",
        passthrough_key="OPENROUTER_PASSTHROUGH",
        model_name_key=None,
        litellm_prefix="openrouter",
        default_base_url=OPENROUTER_DEFAULT_BASE_URL,
        default_passthrough=False,
        requires_api_key=True,
    ),
    CUSTOM_MODEL_PROVIDER_SLUG: GatewayProviderSpec(
        slug=CUSTOM_MODEL_PROVIDER_SLUG,
        api_key_key="CUSTOM_MODEL_PROVIDER_API_KEY",
        base_url_key="CUSTOM_MODEL_PROVIDER_BASE_URL",
        passthrough_key="CUSTOM_MODEL_PROVIDER_PASSTHROUGH",
        model_name_key="CUSTOM_MODEL_PROVIDER_MODEL_NAME",
        litellm_prefix=None,
        default_base_url=None,
        default_passthrough=False,
    ),
}

BUILTIN_GATEWAY_PROVIDER_SLUGS: frozenset[str] = frozenset(
    slug for slug in GATEWAY_PROVIDER_SPECS if slug != CUSTOM_MODEL_PROVIDER_SLUG
)


def is_gateway_provider(provider: str) -> bool:
    """Return whether ``provider`` supports passthrough routing."""
    return provider in GATEWAY_PROVIDER_SPECS


def is_builtin_gateway_provider(provider: str) -> bool:
    """Return whether ``provider`` is a built-in OpenAI-compatible provider."""
    return provider in BUILTIN_GATEWAY_PROVIDER_SLUGS


def parse_passthrough_flag(value: str | None, *, default: bool = False) -> bool:
    """Parse a stored boolean-ish credential flag."""
    if value is None or not value.strip():
        return default
    return value.strip().lower() in _TRUTHY_FLAGS


def strip_openai_version_suffix(base_url: str) -> str:
    """Strip a trailing ``/vN`` segment so a URL points at the host root."""
    return _OPENAI_VERSION_SUFFIX_RE.sub("", base_url.rstrip("/"))


def resolve_gateway_provider_config(
    provider: str,
    credentials: dict[str, str],
) -> GatewayProviderRuntimeConfig | None:
    """Resolve base URL, passthrough, API key, and model override for a provider.

    Returns ``None`` when ``provider`` is not a gateway provider.
    """
    spec = GATEWAY_PROVIDER_SPECS.get(provider)
    if spec is None:
        return None
    base_url = credentials.get(spec.base_url_key) or spec.default_base_url
    model_name = credentials.get(spec.model_name_key) if spec.model_name_key else None
    return GatewayProviderRuntimeConfig(
        base_url=base_url.rstrip("/") if base_url else None,
        passthrough=parse_passthrough_flag(
            credentials.get(spec.passthrough_key),
            default=spec.default_passthrough,
        ),
        api_key=credentials.get(spec.api_key_key) or None,
        model_name=model_name or None,
    )
