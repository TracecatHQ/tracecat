"""Azure Database for PostgreSQL login with Entra ID tokens.

Tokens come from azure-identity's ``DefaultAzureCredential`` (Workload
Identity, managed identity), which caches a token and fetches a new one close
to expiry. Postgres checks the token only at login, so an open connection
outlives it and nothing has to re-authenticate.

Imported only when ``TRACECAT__DB_AUTH_MODE=azure_entra``.
"""

from collections.abc import Callable
from typing import Final

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential

from tracecat.db.exceptions import DatabaseCredentialUnavailableError
from tracecat.db.iam.types import DbAzureEntraAuth

AZURE_POSTGRES_SCOPE: Final = "https://ossrdbms-aad.database.windows.net/.default"
"""Entra token scope (audience) for Azure Database for PostgreSQL."""


class AzureEntraTokenProvider:
    """Entra ID tokens for new Postgres connections.

    The credential is created on first use, in the event loop that opens the
    connections, and is bound to it: give each engine its own provider.
    """

    def __init__(
        self,
        auth: DbAzureEntraAuth,
        *,
        credential_factory: Callable[[], AsyncTokenCredential] | None = None,
    ) -> None:
        self._client_id = auth.client_id
        self._credential_factory = credential_factory or self._default_credential
        self._credential: AsyncTokenCredential | None = None

    def _default_credential(self) -> AsyncTokenCredential:
        if self._client_id is None:
            return DefaultAzureCredential()
        return DefaultAzureCredential(
            managed_identity_client_id=self._client_id,
            workload_identity_client_id=self._client_id,
        )

    async def __call__(self) -> str:
        if (credential := self._credential) is None:
            credential = self._credential = self._credential_factory()
        try:
            token = await credential.get_token(AZURE_POSTGRES_SCOPE)
        except Exception as exc:
            raise DatabaseCredentialUnavailableError(
                "Could not get an Entra ID token for Postgres"
            ) from exc
        return token.token

    async def aclose(self) -> None:
        if self._credential is not None:
            credential, self._credential = self._credential, None
            await credential.close()


def mint_entra_token(auth: DbAzureEntraAuth) -> str:
    """Fetch one Entra ID token synchronously (alembic's one-off login).

    Raises:
        DatabaseCredentialUnavailableError: The token could not be fetched.
    """
    if auth.client_id is None:
        credential = SyncDefaultAzureCredential()
    else:
        credential = SyncDefaultAzureCredential(
            managed_identity_client_id=auth.client_id,
            workload_identity_client_id=auth.client_id,
        )
    try:
        return credential.get_token(AZURE_POSTGRES_SCOPE).token
    except Exception as exc:
        raise DatabaseCredentialUnavailableError(
            "Could not get an Entra ID token for Postgres"
        ) from exc
    finally:
        credential.close()
