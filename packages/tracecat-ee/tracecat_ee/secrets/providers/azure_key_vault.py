"""Azure Key Vault provider for external secret stores.

A store points at one vault and reads as one Entra ID service principal
(tenant ID, client ID and a client secret stored encrypted on the store). A
reference is a secret name; every fetch reads its latest version.

Each fetch builds its own credential and client and closes both, so nothing
(token or value) is kept between fetches.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Collection, Sequence
from typing import Final

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.identity.aio import ClientSecretCredential
from azure.keyvault.secrets.aio import SecretClient

from tracecat.exceptions import TracecatConflictError
from tracecat.logger import logger
from tracecat.secrets.enums import SecretResolutionErrorCode, SecretStoreProvider
from tracecat.secrets.schemas import (
    AzureKeyVaultStoreConfig,
    AzureKeyVaultStoreCreate,
    AzureKeyVaultStoreUpdate,
)
from tracecat.secrets.types import Failure, FetchOutcome, StoreContext
from tracecat_ee.secrets.errors import SecretResolutionError
from tracecat_ee.secrets.providers.backend_helpers import (
    VENDOR_CODE,
    apply_config_update,
    fail_all,
)

FETCH_TIMEOUT_SECONDS: Final = 20.0
"""One deadline for signing in and reading every reference of a store."""

MAX_CONCURRENT_READS: Final = 8
"""Secrets one fetch reads at once."""

# Key Vault secret names: 1-127 alphanumerics and hyphens.
_SECRET_NAME = re.compile(r"[0-9A-Za-z-]{1,127}")
_STORE_WIDE_CODES: Final = frozenset(
    {
        SecretResolutionErrorCode.AUTH_FAILED,
        SecretResolutionErrorCode.UNAVAILABLE,
        SecretResolutionErrorCode.THROTTLED,
    }
)


def _vendor_code(error: HttpResponseError) -> str | None:
    """Key Vault's error code (``SecretNotFound``) or the HTTP status, never text."""
    code = error.error.code if error.error else None
    if isinstance(code, str) and VENDOR_CODE.fullmatch(code):
        return code
    return f"http_{error.status_code}" if error.status_code else None


def classify_error(error: Exception) -> Failure:
    """Map an Azure SDK exception to a sanitized failure by type and status."""
    if isinstance(error, ServiceRequestError | ServiceResponseError):
        return Failure(SecretResolutionErrorCode.UNAVAILABLE, "connection")
    if not isinstance(error, HttpResponseError):
        return Failure(SecretResolutionErrorCode.UNKNOWN, None)
    match error:
        # Subclasses of HttpResponseError, so they come first.
        case ClientAuthenticationError():
            code = SecretResolutionErrorCode.AUTH_FAILED
        case ResourceNotFoundError():
            code = SecretResolutionErrorCode.NOT_FOUND
        case HttpResponseError(status_code=403):
            code = SecretResolutionErrorCode.ACCESS_DENIED
        case HttpResponseError(status_code=429):
            code = SecretResolutionErrorCode.THROTTLED
        case HttpResponseError(status_code=int(status)) if status >= 500:
            code = SecretResolutionErrorCode.UNAVAILABLE
        case _:
            code = SecretResolutionErrorCode.UNKNOWN
    return Failure(code, _vendor_code(error))


async def _read_one(
    client: SecretClient, name: str, slots: asyncio.Semaphore
) -> FetchOutcome:
    """Read the latest version of one secret. Never raises for a remote failure."""
    async with slots:
        try:
            secret = await client.get_secret(name)
        except Exception as e:
            failure = classify_error(e)
            # Type and code only; SDK messages can quote request details.
            logger.warning(
                "Azure Key Vault read failed",
                error_type=type(e).__name__,
                error_code=failure.code.value,
                vendor_code=failure.vendor_code,
            )
            return failure
    if secret.value is None:
        return Failure(SecretResolutionErrorCode.UNKNOWN, "no_value")
    return secret.value


async def fetch_from_vault(
    config: AzureKeyVaultStoreConfig,
    client_secret: str,
    references: Sequence[str],
) -> dict[str, FetchOutcome]:
    """Read every reference with one credential within one deadline."""
    if not references:
        return {}
    slots = asyncio.Semaphore(MAX_CONCURRENT_READS)
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            async with (
                ClientSecretCredential(
                    tenant_id=str(config.tenant_id),
                    client_id=str(config.client_id),
                    client_secret=client_secret,
                    authority=config.authority,
                ) as credential,
                SecretClient(
                    vault_url=config.vault_url,
                    credential=credential,
                    verify_challenge_resource=True,
                    retry_total=2,
                    connection_timeout=5,
                    read_timeout=10,
                ) as client,
            ):
                # One read first, so the rest reuse its token; a store-wide
                # failure would repeat for every name.
                first, *rest = references
                head = await _read_one(client, first, slots)
                if isinstance(head, Failure) and head.code in _STORE_WIDE_CODES:
                    return fail_all(references, head)
                values = [
                    head,
                    *await asyncio.gather(
                        *(_read_one(client, name, slots) for name in rest)
                    ),
                ]
    except TimeoutError:
        logger.warning(
            "Azure Key Vault fetch timed out",
            references=len(references),
            timeout_seconds=FETCH_TIMEOUT_SECONDS,
        )
        return fail_all(
            references, Failure(SecretResolutionErrorCode.TIMEOUT, "deadline")
        )
    return dict(zip(references, values, strict=True))


def _azure_config(config: object) -> AzureKeyVaultStoreConfig:
    if not isinstance(config, AzureKeyVaultStoreConfig):
        raise TypeError("Azure Key Vault store requires an Azure Key Vault config")
    return config


class AzureKeyVaultBackend:
    """Azure Key Vault implementation of ``SecretStoreBackend``."""

    provider = SecretStoreProvider.AZURE_KEY_VAULT
    display_name = "Azure Key Vault"
    uses_access_token = True
    """The access token is the service principal's client secret."""

    def new_config(self, params: AzureKeyVaultStoreCreate) -> AzureKeyVaultStoreConfig:
        """Build a store config. Azure Key Vault has no server-owned fields."""
        return AzureKeyVaultStoreConfig(
            vault_url=params.vault_url,
            tenant_id=params.tenant_id,
            client_id=params.client_id,
        )

    def update_config(
        self, config: AzureKeyVaultStoreConfig, params: AzureKeyVaultStoreUpdate
    ) -> AzureKeyVaultStoreConfig:
        """Apply vault, tenant and client updates."""
        return apply_config_update(config, params)

    def check_change(
        self,
        old: AzureKeyVaultStoreConfig,
        new: AzureKeyVaultStoreConfig,
        *,
        references: Collection[str],
    ) -> None:
        """Saved references are secret names in one vault, so the vault cannot
        change while references exist. The tenant, client and secret can.
        """
        if new.vault_url != old.vault_url and references:
            raise TracecatConflictError(
                "Update or remove secrets that reference this store before"
                " changing its vault."
            )

    def resolution_error(
        self,
        code: SecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        vendor_code: str | None,
    ) -> SecretResolutionError:
        """The generic error, with ``provider_error_code`` in its detail."""
        return SecretResolutionError(
            code,
            alias=alias,
            environment=environment,
            provider_label=self.display_name,
            vendor_code=vendor_code,
        )

    def is_ready(self, config: AzureKeyVaultStoreConfig) -> bool:
        """The config is complete once created."""
        return True

    async def check_config(self, config: AzureKeyVaultStoreConfig) -> None:
        """No network check: the config only accepts Key Vault hosts."""

    def validate_reference(self, config: AzureKeyVaultStoreConfig, key: str) -> None:
        """A reference is a Key Vault secret name."""
        if not _SECRET_NAME.fullmatch(key):
            raise ValueError(
                "An Azure Key Vault secret reference is the secret's name:"
                " 1 to 127 letters, digits and hyphens."
            )

    async def fetch(
        self, store: StoreContext, references: Sequence[str]
    ) -> dict[str, FetchOutcome]:
        """Read the latest version of every reference of the store."""
        if store.access_token is None:
            # Enabled with a secret that may no longer decrypt.
            return fail_all(
                references, Failure(SecretResolutionErrorCode.CREDENTIAL_MISSING)
            )
        return await fetch_from_vault(
            _azure_config(store.config), store.access_token, references
        )
