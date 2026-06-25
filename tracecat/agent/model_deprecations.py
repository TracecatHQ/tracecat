"""Shared model deprecation policy for agent model selection.

Deprecated models remain executable for existing saved configs, but hidden models
should not be offered for new selections in model/catalog pickers.
"""

from __future__ import annotations

from collections.abc import Iterable

DeprecatedModelKey = tuple[str, str]

# Keep this scoped to the platform OpenAI model. Custom/OpenAI-compatible
# sources may expose a model with the same name and should not be hidden unless
# they opt into their own metadata-level status later.
DEPRECATED_MODEL_KEYS: frozenset[DeprecatedModelKey] = frozenset(
    {("openai", "gpt-4o-mini")}
)
HIDDEN_MODEL_KEYS: frozenset[DeprecatedModelKey] = DEPRECATED_MODEL_KEYS

DEPRECATED_MODEL_MESSAGE = (
    "gpt-4o-mini is deprecated and hidden for new selections; existing agents "
    "and actions that already use it continue to run."
)


def model_key(model_provider: str, model_name: str) -> DeprecatedModelKey:
    """Return the normalized deprecation key for a provider/model pair."""
    return (model_provider.strip().lower(), model_name.strip())


def is_deprecated_model(model_provider: str, model_name: str) -> bool:
    """Return whether a provider/model pair is deprecated."""
    return model_key(model_provider, model_name) in DEPRECATED_MODEL_KEYS


def is_hidden_model(model_provider: str, model_name: str) -> bool:
    """Return whether a provider/model pair should be hidden from pickers."""
    return model_key(model_provider, model_name) in HIDDEN_MODEL_KEYS


def iter_hidden_model_keys() -> Iterable[DeprecatedModelKey]:
    """Yield hidden model keys for SQL filter construction."""
    return HIDDEN_MODEL_KEYS


def deprecation_message(model_provider: str, model_name: str) -> str | None:
    """Return the deprecation message for a provider/model pair, if any."""
    if is_deprecated_model(model_provider, model_name):
        return DEPRECATED_MODEL_MESSAGE
    return None
