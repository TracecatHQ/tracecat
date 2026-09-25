"""Bitbucket token service for workspace sync."""

from __future__ import annotations

from enum import StrEnum

from cryptography.fernet import InvalidToken
from pydantic import SecretStr
from pydantic import ValidationError as PydanticValidationError

from tracecat.authz.controls import require_scope
from tracecat.db.models import OrganizationSecret
from tracecat.exceptions import TracecatException, TracecatNotFoundError
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseOrgService, requires_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat.vcs.bitbucket.schemas import BitbucketTokenCredentials
from tracecat.vcs.exceptions import VcsProviderError
from tracecat.vcs.schemas import BitbucketTokenCredentialsStatus

BITBUCKET_TOKEN_SECRET_NAME = "bitbucket-token-credentials"


class BitbucketError(VcsProviderError):
    """Bitbucket operation error."""


class BitbucketTokenSecretState(StrEnum):
    """State of the stored Bitbucket token org secret."""

    MISSING = "missing"
    VALID = "valid"
    CORRUPTED = "corrupted"


class BitbucketTokenService(BaseOrgService):
    """Organization-level Bitbucket token credentials."""

    service_name = "bitbucket_token"

    def _build_secret_keys(
        self,
        *,
        email: str,
        token: SecretStr,
    ) -> list[SecretKeyValue]:
        credentials = BitbucketTokenCredentials(email=email, token=token)
        return [
            SecretKeyValue(key="email", value=SecretStr(credentials.email)),
            SecretKeyValue(key="token", value=credentials.token),
        ]

    async def _get_bitbucket_token_secret_state(
        self,
    ) -> tuple[
        BitbucketTokenSecretState,
        OrganizationSecret | None,
        BitbucketTokenCredentials | None,
    ]:
        """Classify the Bitbucket token secret without failing on corruption."""
        secrets_service = SecretsService(session=self.session, role=self.role)
        try:
            secret = await secrets_service._get_org_secret_by_name(
                BITBUCKET_TOKEN_SECRET_NAME
            )
        except TracecatNotFoundError:
            return BitbucketTokenSecretState.MISSING, None, None

        try:
            decrypted_keys = secrets_service.decrypt_keys(secret.encrypted_keys)
            key_dict = {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}
            credentials = BitbucketTokenCredentials.model_validate(key_dict)
        except (InvalidToken, PydanticValidationError, ValueError) as e:
            self.logger.warning(
                "Stored Bitbucket token credentials are corrupted; allowing reconfiguration",
                secret_id=str(secret.id),
                error_type=type(e).__name__,
            )
            return BitbucketTokenSecretState.CORRUPTED, secret, None

        return BitbucketTokenSecretState.VALID, secret, credentials

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:update")
    async def save_bitbucket_token_credentials(
        self,
        *,
        email: str,
        token: SecretStr,
    ) -> tuple[BitbucketTokenCredentials, bool]:
        """Save Bitbucket token credentials, replacing corrupt values when needed."""
        credentials = BitbucketTokenCredentials(email=email, token=token)
        secret_state, secret, _existing = await self._get_bitbucket_token_secret_state()
        secret_keys = self._build_secret_keys(
            email=credentials.email,
            token=credentials.token,
        )
        secrets_service = SecretsService(session=self.session, role=self.role)

        match secret_state:
            case BitbucketTokenSecretState.MISSING:
                await secrets_service._create_org_secret(
                    SecretCreate(
                        name=BITBUCKET_TOKEN_SECRET_NAME,
                        type=SecretType.CUSTOM,
                        description="Bitbucket token credentials for workspace synchronization",
                        keys=secret_keys,
                        tags={"purpose": "bitbucket-token", "provider": "bitbucket"},
                    )
                )
                self.logger.info("Created Bitbucket token credentials")
                return credentials, True
            case BitbucketTokenSecretState.VALID | BitbucketTokenSecretState.CORRUPTED:
                if secret is None:
                    raise BitbucketError(
                        "Stored Bitbucket token credentials could not be recovered."
                    )
                await secrets_service._update_org_secret(
                    secret,
                    SecretUpdate(keys=secret_keys),
                )
                self.logger.info(
                    "Updated Bitbucket token credentials",
                    recovered_corrupted=secret_state
                    is BitbucketTokenSecretState.CORRUPTED,
                )
                return credentials, False

        raise BitbucketError("Failed to save Bitbucket token credentials")

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:delete")
    async def delete_bitbucket_token_credentials(self) -> None:
        """Delete Bitbucket token credentials for the organization."""
        try:
            secrets_service = SecretsService(session=self.session, role=self.role)
            secret = await secrets_service._get_org_secret_by_name(
                BITBUCKET_TOKEN_SECRET_NAME
            )
            await secrets_service._delete_org_secret(secret)
            self.logger.info("Deleted Bitbucket token credentials")
        except TracecatNotFoundError as e:
            raise BitbucketError(
                "Failed to delete Bitbucket token credentials: credentials not found"
            ) from e
        except TracecatException as e:
            raise BitbucketError("Failed to delete Bitbucket token credentials") from e

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:read")
    async def get_bitbucket_token_credentials_status(
        self,
    ) -> BitbucketTokenCredentialsStatus:
        """Return Bitbucket credential status without exposing the token."""
        (
            secret_state,
            secret,
            credentials,
        ) = await self._get_bitbucket_token_secret_state()
        match secret_state:
            case BitbucketTokenSecretState.VALID if secret and credentials:
                return BitbucketTokenCredentialsStatus(
                    exists=True,
                    email=credentials.email,
                    created_at=secret.created_at.isoformat()
                    if secret.created_at
                    else None,
                )
            case BitbucketTokenSecretState.CORRUPTED if secret:
                return BitbucketTokenCredentialsStatus(exists=True, is_corrupted=True)
            case _:
                return BitbucketTokenCredentialsStatus(exists=False)

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("workflow:sync", "workspace_sync:sync", require_all=False)
    async def get_bitbucket_token_credentials(self) -> BitbucketTokenCredentials:
        """Retrieve Bitbucket credentials for workspace sync API calls."""
        (
            secret_state,
            _secret,
            credentials,
        ) = await self._get_bitbucket_token_secret_state()
        match secret_state:
            case BitbucketTokenSecretState.MISSING:
                raise BitbucketError(
                    "Failed to retrieve Bitbucket token credentials: credentials not found"
                )
            case BitbucketTokenSecretState.CORRUPTED:
                raise BitbucketError(
                    "Failed to retrieve Bitbucket token credentials: invalid credential data"
                )
            case BitbucketTokenSecretState.VALID if credentials:
                return credentials

        raise BitbucketError("Failed to retrieve Bitbucket token credentials")
