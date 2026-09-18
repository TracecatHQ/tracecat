"""Provider-pluggable interface for external secret stores.

Only provider modules know provider-specific configuration fields.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from tracecat.db.models import OrganizationSecretStore
from tracecat.exceptions import TracecatValidationError
from tracecat.secrets.aws_secrets_manager import AwsSecretsManagerBackend
from tracecat.secrets.enums import SecretStoreProvider
from tracecat.secrets.schemas import (
    SecretStoreConfig,
    SecretStoreCreateConfig,
    SecretStoreUpdateConfig,
)
from tracecat.secrets.types import (
    CheckResult,
    ExternalSecretReference,
)


class SecretStoreBackend(Protocol):
    """One external secret store provider."""

    provider: SecretStoreProvider

    def new_config(self, params: SecretStoreCreateConfig) -> SecretStoreConfig:
        """Build a persisted config, generating server-owned fields."""
        ...

    def update_config(
        self, config: SecretStoreConfig, params: SecretStoreUpdateConfig
    ) -> SecretStoreConfig:
        """Apply client-supplied updates, preserving server-owned fields."""
        ...

    def validate_reference(self, config: SecretStoreConfig, key: str) -> None:
        """Raise ``ValueError`` when a remote key is invalid for this store."""
        ...

    async def resolve(
        self, references: Sequence[ExternalSecretReference]
    ) -> dict[str, dict[str, str]]:
        """Resolve references to ``{alias: {key: value}}``."""
        ...

    async def check(self, reference: ExternalSecretReference) -> CheckResult:
        """Verify one reference resolves without returning the value."""
        ...


BACKENDS: dict[SecretStoreProvider, SecretStoreBackend] = {
    SecretStoreProvider.AWS_SECRETS_MANAGER: AwsSecretsManagerBackend(),
}


def get_backend(provider: SecretStoreProvider | str) -> SecretStoreBackend:
    """Look up a backend, rejecting unknown providers with a typed error."""
    try:
        return BACKENDS[SecretStoreProvider(provider)]
    except (KeyError, ValueError) as e:
        raise TracecatValidationError(
            f"Unsupported secret store provider {provider!r}"
        ) from e


def parse_store_config(store: OrganizationSecretStore) -> SecretStoreConfig:
    """Validate a stored config blob through its provider's config model."""
    get_backend(store.provider)
    return SecretStoreConfig.model_validate(store.config)
