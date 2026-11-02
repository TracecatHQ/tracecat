"""Bitbucket Data Center token service for workspace sync."""

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
from tracecat.vcs.bitbucket_data_center.schemas import (
    BitbucketDataCenterTokenCredentials,
)
from tracecat.vcs.exceptions import VcsProviderError
from tracecat.vcs.schemas import BitbucketDataCenterTokenCredentialsStatus

BITBUCKET_DATA_CENTER_TOKEN_SECRET_NAME = "bitbucket_data_center-token-credentials"


class BitbucketDataCenterError(VcsProviderError):
    """Bitbucket Data Center operation error."""


class BitbucketDataCenterTokenSecretState(StrEnum):
    """State of the stored Bitbucket Data Center token org secret."""

    MISSING = "missing"
    VALID = "valid"
    CORRUPTED = "corrupted"


class BitbucketDataCenterTokenService(BaseOrgService):
    """Organization-level Bitbucket Data Center token credentials."""

    service_name = "bitbucket_data_center_token"

    def _build_secret_keys(
        self,
        *,
        base_url: str,
        token: SecretStr,
    ) -> list[SecretKeyValue]:
        credentials = BitbucketDataCenterTokenCredentials(
            base_url=base_url, token=token
        )
        return [
            SecretKeyValue(key="base_url", value=SecretStr(credentials.base_url)),
            SecretKeyValue(key="token", value=credentials.token),
        ]

    async def _get_bitbucket_data_center_token_secret_state(
        self,
    ) -> tuple[
        BitbucketDataCenterTokenSecretState,
        OrganizationSecret | None,
        BitbucketDataCenterTokenCredentials | None,
    ]:
        """Classify the Bitbucket Data Center token secret without failing on corruption."""
        secrets_service = SecretsService(session=self.session, role=self.role)
        try:
            secret = await secrets_service._get_org_secret_by_name(
                BITBUCKET_DATA_CENTER_TOKEN_SECRET_NAME
            )
        except TracecatNotFoundError:
            return BitbucketDataCenterTokenSecretState.MISSING, None, None

        try:
            decrypted_keys = secrets_service.decrypt_keys(secret.encrypted_keys)
            key_dict = {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}
            credentials = BitbucketDataCenterTokenCredentials.model_validate(key_dict)
        except (InvalidToken, PydanticValidationError, ValueError) as e:
            self.logger.warning(
                "Stored Bitbucket Data Center token credentials are corrupted; allowing reconfiguration",
                secret_id=str(secret.id),
                error_type=type(e).__name__,
            )
            return BitbucketDataCenterTokenSecretState.CORRUPTED, secret, None

        return BitbucketDataCenterTokenSecretState.VALID, secret, credentials

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:update")
    async def save_bitbucket_data_center_token_credentials(
        self,
        *,
        base_url: str,
        token: SecretStr,
    ) -> tuple[BitbucketDataCenterTokenCredentials, bool]:
        """Save Bitbucket Data Center token credentials, replacing corrupt values when needed."""
        credentials = BitbucketDataCenterTokenCredentials(
            base_url=base_url, token=token
        )
        (
            secret_state,
            secret,
            _existing,
        ) = await self._get_bitbucket_data_center_token_secret_state()
        secret_keys = self._build_secret_keys(
            base_url=credentials.base_url,
            token=credentials.token,
        )
        secrets_service = SecretsService(session=self.session, role=self.role)

        match secret_state:
            case BitbucketDataCenterTokenSecretState.MISSING:
                await secrets_service._create_org_secret(
                    SecretCreate(
                        name=BITBUCKET_DATA_CENTER_TOKEN_SECRET_NAME,
                        type=SecretType.CUSTOM,
                        description="Bitbucket Data Center token credentials for workspace synchronization",
                        keys=secret_keys,
                        tags={
                            "purpose": "bitbucket_data_center-token",
                            "provider": "bitbucket_data_center",
                        },
                    )
                )
                self.logger.info("Created Bitbucket Data Center token credentials")
                return credentials, True
            case (
                BitbucketDataCenterTokenSecretState.VALID
                | BitbucketDataCenterTokenSecretState.CORRUPTED
            ):
                if secret is None:
                    raise BitbucketDataCenterError(
                        "Stored Bitbucket Data Center token credentials could not be recovered."
                    )
                await secrets_service._update_org_secret(
                    secret,
                    SecretUpdate(keys=secret_keys),
                )
                self.logger.info(
                    "Updated Bitbucket Data Center token credentials",
                    recovered_corrupted=secret_state
                    is BitbucketDataCenterTokenSecretState.CORRUPTED,
                )
                return credentials, False

        raise BitbucketDataCenterError(
            "Failed to save Bitbucket Data Center token credentials"
        )

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:delete")
    async def delete_bitbucket_data_center_token_credentials(self) -> None:
        """Delete Bitbucket Data Center token credentials for the organization."""
        try:
            secrets_service = SecretsService(session=self.session, role=self.role)
            secret = await secrets_service._get_org_secret_by_name(
                BITBUCKET_DATA_CENTER_TOKEN_SECRET_NAME
            )
            await secrets_service._delete_org_secret(secret)
            self.logger.info("Deleted Bitbucket Data Center token credentials")
        except TracecatNotFoundError as e:
            raise BitbucketDataCenterError(
                "Failed to delete Bitbucket Data Center token credentials: credentials not found"
            ) from e
        except TracecatException as e:
            raise BitbucketDataCenterError(
                "Failed to delete Bitbucket Data Center token credentials"
            ) from e

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:read")
    async def get_bitbucket_data_center_token_credentials_status(
        self,
    ) -> BitbucketDataCenterTokenCredentialsStatus:
        """Return Bitbucket Data Center credential status without exposing the token."""
        (
            secret_state,
            secret,
            credentials,
        ) = await self._get_bitbucket_data_center_token_secret_state()
        match secret_state:
            case BitbucketDataCenterTokenSecretState.VALID if secret and credentials:
                return BitbucketDataCenterTokenCredentialsStatus(
                    exists=True,
                    base_url=credentials.base_url,
                    created_at=secret.created_at.isoformat()
                    if secret.created_at
                    else None,
                )
            case BitbucketDataCenterTokenSecretState.CORRUPTED if secret:
                return BitbucketDataCenterTokenCredentialsStatus(
                    exists=True, is_corrupted=True
                )
            case _:
                return BitbucketDataCenterTokenCredentialsStatus(exists=False)

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("workflow:sync", "workspace_sync:sync", require_all=False)
    async def get_bitbucket_data_center_token_credentials(
        self,
    ) -> BitbucketDataCenterTokenCredentials:
        """Retrieve Bitbucket Data Center credentials for workspace sync API calls."""
        (
            secret_state,
            _secret,
            credentials,
        ) = await self._get_bitbucket_data_center_token_secret_state()
        match secret_state:
            case BitbucketDataCenterTokenSecretState.MISSING:
                raise BitbucketDataCenterError(
                    "Failed to retrieve Bitbucket Data Center token credentials: credentials not found"
                )
            case BitbucketDataCenterTokenSecretState.CORRUPTED:
                raise BitbucketDataCenterError(
                    "Failed to retrieve Bitbucket Data Center token credentials: invalid credential data"
                )
            case BitbucketDataCenterTokenSecretState.VALID if credentials:
                return credentials

        raise BitbucketDataCenterError(
            "Failed to retrieve Bitbucket Data Center token credentials"
        )
