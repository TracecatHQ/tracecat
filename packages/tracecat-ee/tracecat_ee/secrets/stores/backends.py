"""Provider-pluggable interface for external secret stores.

Only provider modules know provider-specific configuration fields. Core
(:mod:`tracecat_ee.secrets.resolver`) owns deduplication, key mapping,
sanitized errors and clearing fetched values; a provider only describes its
settings, validates references, and fetches a batch of remote values.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from tracecat.db.models import OrganizationSecretStore
from tracecat.exceptions import TracecatValidationError
from tracecat.secrets.enums import SecretStoreProvider
from tracecat.secrets.schemas import SecretStoreConfig, parse_secret_store_config
from tracecat.secrets.types import FetchResult, StoreContext
from tracecat_ee.secrets.providers.aws_secrets_manager import AwsSecretsManagerBackend
from tracecat_ee.secrets.providers.bitwarden_secrets_manager import (
    BitwardenSecretsManagerBackend,
)

if TYPE_CHECKING:
    from tracecat.secrets.enums import SecretResolutionErrorCode
    from tracecat_ee.secrets.resolver import SecretResolutionError


class SecretStoreBackend[ConfigT, CreateT, UpdateT](Protocol):
    """One external secret store provider.

    Generic over the provider's persisted config and its create/update
    parameter models, so each provider is typed against its own models.
    """

    provider: SecretStoreProvider
    display_name: str
    """Short vendor name used in sanitized error messages, e.g. ``"AWS"``."""
    requires_access_token: bool
    """Whether the store needs an access token before it can be enabled."""

    def new_config(self, params: CreateT) -> ConfigT:
        """Build a persisted config, generating server-owned fields."""
        ...

    def update_config(
        self,
        config: ConfigT,
        params: UpdateT,
        *,
        references: Collection[str],
    ) -> ConfigT:
        """Apply client-supplied updates, preserving server-owned fields.

        ``references`` are the distinct remote references saved against the
        store. The provider decides whether the change is allowed while they
        exist and raises :class:`~tracecat.exceptions.TracecatConflictError`
        when it is not.
        """
        ...

    def resolution_error(
        self,
        code: SecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        vendor_code: str | None,
    ) -> SecretResolutionError:
        """Build the sanitized error a run raises when one reference fails.

        The error's type and detail reach the run's error record, so a
        provider keeps its own (AWS keeps ``AwsSecretResolutionError``).
        """
        ...

    def is_ready(self, config: ConfigT) -> bool:
        """Whether the config is complete enough for the store to be enabled."""
        ...

    async def check_config(self, config: ConfigT) -> None:
        """Raise ``ValueError`` when a config is unsafe to save (network checks)."""
        ...

    def validate_reference(self, config: ConfigT, key: str) -> None:
        """Raise ``ValueError`` when a remote reference is malformed for this store.

        Runs when a reference is saved. Checks format only; no remote call.
        """
        ...

    async def fetch(
        self, store: StoreContext, references: Sequence[str]
    ) -> FetchResult:
        """Fetch every distinct reference of one store in one call.

        Returns one outcome per reference and never raises for a remote
        failure: failures are :class:`~tracecat.secrets.types.Failure` values.
        """
        ...


type AnyBackend = SecretStoreBackend[Any, Any, Any]

BACKENDS: dict[SecretStoreProvider, AnyBackend] = {
    SecretStoreProvider.AWS_SECRETS_MANAGER: AwsSecretsManagerBackend(),
    SecretStoreProvider.BITWARDEN_SECRETS_MANAGER: BitwardenSecretsManagerBackend(),
}


def get_backend(provider: SecretStoreProvider | str) -> AnyBackend:
    """Look up a backend, rejecting unknown providers with a typed error."""
    try:
        return BACKENDS[SecretStoreProvider(provider)]
    except (KeyError, ValueError) as e:
        raise TracecatValidationError(
            f"Unsupported secret store provider {provider!r}"
        ) from e


def parse_store_config(store: OrganizationSecretStore) -> SecretStoreConfig:
    """Validate a stored config blob into its provider's config model."""
    return parse_secret_store_config(store.config)
