"""GitLab token service for workspace sync."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

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
from tracecat.vcs.exceptions import VcsProviderError
from tracecat.vcs.gitlab.schemas import GitLabTokenCredentials

GITLAB_TOKEN_SECRET_NAME = "gitlab-token-credentials"


class GitLabError(VcsProviderError):
    """GitLab operation error."""


class GitLabApiError(GitLabError):
    """GitLab REST API operation error with structured status code."""

    def __init__(self, message: str, *, status_code: int, detail: Any | None = None):
        super().__init__(message, detail=detail)
        self.status_code = status_code


class GitLabTokenSecretState(StrEnum):
    """State of the stored GitLab token org secret."""

    MISSING = "missing"
    VALID = "valid"
    CORRUPTED = "corrupted"


class GitLabTokenService(BaseOrgService):
    """Organization-level GitLab token credentials."""

    service_name = "gitlab_token"

    def _build_secret_keys(
        self,
        *,
        base_url: str,
        token: SecretStr,
    ) -> list[SecretKeyValue]:
        credentials = GitLabTokenCredentials(base_url=base_url, token=token)
        return [
            SecretKeyValue(key="base_url", value=SecretStr(credentials.base_url)),
            SecretKeyValue(key="token", value=credentials.token),
        ]

    async def _get_gitlab_token_secret_state(
        self,
    ) -> tuple[
        GitLabTokenSecretState,
        OrganizationSecret | None,
        GitLabTokenCredentials | None,
    ]:
        """Classify the GitLab token secret without failing on corruption."""
        secrets_service = SecretsService(session=self.session, role=self.role)
        try:
            secret = await secrets_service._get_org_secret_by_name(
                GITLAB_TOKEN_SECRET_NAME
            )
        except TracecatNotFoundError:
            return GitLabTokenSecretState.MISSING, None, None

        try:
            decrypted_keys = secrets_service.decrypt_keys(secret.encrypted_keys)
            key_dict = {kv.key: kv.value.get_secret_value() for kv in decrypted_keys}
            credentials = GitLabTokenCredentials.model_validate(key_dict)
        except (InvalidToken, PydanticValidationError, ValueError) as e:
            self.logger.warning(
                "Stored GitLab token credentials are corrupted; allowing reconfiguration",
                secret_id=str(secret.id),
                error_type=type(e).__name__,
                error=str(e),
            )
            return GitLabTokenSecretState.CORRUPTED, secret, None

        return GitLabTokenSecretState.VALID, secret, credentials

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:update")
    async def save_gitlab_token_credentials(
        self,
        *,
        base_url: str,
        token: SecretStr,
    ) -> tuple[GitLabTokenCredentials, bool]:
        """Save GitLab token credentials, replacing corrupt values when needed."""
        credentials = GitLabTokenCredentials(base_url=base_url, token=token)
        secret_state, secret, _existing = await self._get_gitlab_token_secret_state()
        secret_keys = self._build_secret_keys(
            base_url=credentials.base_url,
            token=credentials.token,
        )
        secrets_service = SecretsService(session=self.session, role=self.role)

        match secret_state:
            case GitLabTokenSecretState.MISSING:
                await secrets_service._create_org_secret(
                    SecretCreate(
                        name=GITLAB_TOKEN_SECRET_NAME,
                        type=SecretType.CUSTOM,
                        description="GitLab token credentials for workspace synchronization",
                        keys=secret_keys,
                        tags={"purpose": "gitlab-token", "provider": "gitlab"},
                    )
                )
                self.logger.info("Created GitLab token credentials")
                return credentials, True
            case GitLabTokenSecretState.VALID | GitLabTokenSecretState.CORRUPTED:
                if secret is None:
                    raise GitLabError(
                        "Stored GitLab token credentials could not be recovered."
                    )
                await secrets_service._update_org_secret(
                    secret,
                    SecretUpdate(keys=secret_keys),
                )
                self.logger.info(
                    "Updated GitLab token credentials",
                    recovered_corrupted=secret_state
                    is GitLabTokenSecretState.CORRUPTED,
                )
                return credentials, False

        raise GitLabError("Failed to save GitLab token credentials")

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:delete")
    async def delete_gitlab_token_credentials(self) -> None:
        """Delete GitLab token credentials for the organization."""
        try:
            secrets_service = SecretsService(session=self.session, role=self.role)
            secret = await secrets_service._get_org_secret_by_name(
                GITLAB_TOKEN_SECRET_NAME
            )
            await secrets_service._delete_org_secret(secret)
            self.logger.info("Deleted GitLab token credentials")
        except TracecatNotFoundError as e:
            raise GitLabError(
                "Failed to delete GitLab token credentials: credentials not found"
            ) from e
        except TracecatException as e:
            raise GitLabError("Failed to delete GitLab token credentials") from e

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("org:settings:read")
    async def get_gitlab_token_credentials_status(self) -> dict[str, Any]:
        """Return GitLab credential status without exposing the token."""
        secret_state, secret, credentials = await self._get_gitlab_token_secret_state()
        match secret_state:
            case GitLabTokenSecretState.VALID if secret and credentials:
                return {
                    "exists": True,
                    "is_corrupted": False,
                    "base_url": credentials.base_url,
                    "created_at": secret.created_at.isoformat()
                    if secret.created_at
                    else None,
                }
            case GitLabTokenSecretState.CORRUPTED if secret:
                return {
                    "exists": True,
                    "is_corrupted": True,
                    "base_url": None,
                    "created_at": secret.created_at.isoformat()
                    if secret.created_at
                    else None,
                }
            case _:
                return {
                    "exists": False,
                    "is_corrupted": False,
                    "base_url": None,
                    "created_at": None,
                }

    @requires_entitlement(Entitlement.GIT_SYNC)
    @require_scope("workflow:sync", "workspace_sync:sync", require_all=False)
    async def get_gitlab_token_credentials(self) -> GitLabTokenCredentials:
        """Retrieve GitLab credentials for workspace sync API calls."""
        secret_state, _secret, credentials = await self._get_gitlab_token_secret_state()
        match secret_state:
            case GitLabTokenSecretState.MISSING:
                raise GitLabError(
                    "Failed to retrieve GitLab token credentials: credentials not found"
                )
            case GitLabTokenSecretState.CORRUPTED:
                raise GitLabError(
                    "Failed to retrieve GitLab token credentials: invalid credential data"
                )
            case GitLabTokenSecretState.VALID if credentials:
                return credentials

        raise GitLabError("Failed to retrieve GitLab token credentials")
