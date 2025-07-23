from typing import Final

from tracecat.integrations.models import ProviderKey
from tracecat.integrations.providers.base import BaseOAuthProvider
from tracecat.integrations.providers.microsoft.graph import (
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
)
from tracecat.integrations.providers.microsoft.teams import (
    MicrosoftTeamsACProvider,
    MicrosoftTeamsCCProvider,
)

_PROVIDER_CLASSES: list[type[BaseOAuthProvider]] = [
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
    MicrosoftTeamsACProvider,
    MicrosoftTeamsCCProvider,
]


def _build_provider_registry() -> dict[ProviderKey, type[BaseOAuthProvider]]:
    """Build provider registry with duplicate detection."""
    registry: dict[ProviderKey, type[BaseOAuthProvider]] = {}
    for cls in _PROVIDER_CLASSES:
        if not getattr(cls, "_include_in_registry", True):
            continue
        key = ProviderKey(id=cls.id, grant_type=cls.grant_type)
        if key in registry:
            raise ValueError(
                f"Duplicate provider key {key} for {cls.__name__} "
                f"(already registered by {registry[key].__name__})"
            )
        registry[key] = cls
    return registry


PROVIDER_REGISTRY: Final[dict[ProviderKey, type[BaseOAuthProvider]]] = (
    _build_provider_registry()
)


def get_provider_class(key: ProviderKey) -> type[BaseOAuthProvider] | None:
    """Return the provider class matching *key*, or ``None``."""
    return PROVIDER_REGISTRY.get(key)


def all_providers() -> list[type[BaseOAuthProvider]]:
    """Return all registered provider classes."""
    return list(PROVIDER_REGISTRY.values())
