from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from urllib.parse import quote

import boto3
import orjson
from botocore.exceptions import ClientError
from cryptography.fernet import InvalidToken
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.credential_sync.schemas import (
    SYNCABLE_SECRET_TYPES,
    AwsCredentialSyncConfig,
    AwsCredentialSyncConfigRead,
    AwsCredentialSyncConfigUpdate,
    CredentialSyncErrorItem,
    CredentialSyncOperation,
    CredentialSyncProvider,
    CredentialSyncResult,
    SyncedSecretKeyValue,
    SyncedSecretPayload,
)
from tracecat.credential_sync.types import CredentialSyncBackend, RemoteSecretRecord
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseOrgService
from tracecat.settings.constants import AWS_CREDENTIAL_SYNC_SETTING_KEY
from tracecat.settings.schemas import SettingCreate, SettingUpdate
from tracecat.settings.service import SettingsService

type BackendFactory = Callable[[AwsCredentialSyncConfig], CredentialSyncBackend]

_SYNC_SECRET_SCOPES = frozenset({"secret:read", "secret:create", "secret:update"})


class AwsSecretsManagerSyncBackend:
    """AWS Secrets Manager implementation for credential sync."""

    def __init__(self, config: AwsCredentialSyncConfig) -> None:
        self._config = config

    def _create_client(self):
        session = boto3.session.Session(
            aws_access_key_id=self._config.access_key_id,
            aws_secret_access_key=self._config.secret_access_key,
            aws_session_token=self._config.session_token,
            region_name=self._config.region,
        )
        return session.client("secretsmanager")

    async def upsert_secret(self, *, secret_name: str, secret_string: str) -> bool:
        return await asyncio.to_thread(
            self._upsert_secret_sync,
            secret_name,
            secret_string,
        )

    def _upsert_secret_sync(self, secret_name: str, secret_string: str) -> bool:
        client = self._create_client()
        try:
            try:
                client.describe_secret(SecretId=secret_name)
                client.put_secret_value(
                    SecretId=secret_name,
                    SecretString=secret_string,
                )
                return False
            except ClientError as exc:
                error = (
                    exc.response.get("Error", {})
                    if isinstance(exc.response, dict)
                    else {}
                )
                error_code = error.get("Code")
                if error_code != "ResourceNotFoundException":
                    raise
                client.create_secret(
                    Name=secret_name,
                    SecretString=secret_string,
                )
                return True
        finally:
            with suppress(Exception):
                client.close()

    async def list_secrets(self, *, prefix: str) -> Sequence[RemoteSecretRecord]:
        return await asyncio.to_thread(self._list_secrets_sync, prefix)

    def _list_secrets_sync(self, prefix: str) -> list[RemoteSecretRecord]:
        client = self._create_client()
        try:
            paginator = client.get_paginator("list_secrets")
            records: list[RemoteSecretRecord] = []
            for page in paginator.paginate(
                Filters=[{"Key": "name", "Values": [prefix]}]
            ):
                for secret in page.get("SecretList", []):
                    name = secret.get("Name")
                    if not isinstance(name, str) or not name.startswith(prefix):
                        continue
                    response = client.get_secret_value(SecretId=name)
                    if not isinstance(response.get("SecretString"), str):
                        raise ValueError(
                            f"Remote secret {name!r} is missing SecretString payload"
                        )
                    records.append(
                        RemoteSecretRecord(
                            name=name,
                            secret_string=response["SecretString"],
                        )
                    )
            return records
        finally:
            with suppress(Exception):
                client.close()


class CredentialSyncService(BaseOrgService):
    """Organization-scoped on-demand credential synchronization."""

    service_name = "credential_sync"

    def __init__(
        self,
        session: AsyncSession,
        role: Role | None = None,
        *,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        super().__init__(session, role=role)
        self._backend_factory = backend_factory or AwsSecretsManagerSyncBackend

    def _require_workspace_context(self) -> Role:
        if self.role.workspace_id is None:
            raise TracecatAuthorizationError(
                "Workspace context required for credential sync"
            )
        return self.role

    def _get_sync_secrets_service(self) -> SecretsService:
        role = self._require_workspace_context()
        current_scopes = role.scopes or frozenset()
        sync_role = role.model_copy(
            update={"scopes": current_scopes | _SYNC_SECRET_SCOPES}
        )
        return SecretsService(self.session, role=sync_role)

    async def get_aws_config(self) -> AwsCredentialSyncConfigRead:
        settings_service = SettingsService(self.session, role=self.role)
        setting = await settings_service.get_org_setting(
            AWS_CREDENTIAL_SYNC_SETTING_KEY
        )
        if setting is None:
            return AwsCredentialSyncConfigRead()
        try:
            config = AwsCredentialSyncConfig.model_validate(
                settings_service.get_value(setting)
            )
        except (InvalidToken, ValidationError, ValueError) as exc:
            self.logger.warning(
                "Failed to decrypt or validate stored AWS credential sync config",
                error=str(exc),
            )
            return AwsCredentialSyncConfigRead(is_corrupted=True)
        return self._serialize_config(config)

    async def update_aws_config(self, params: AwsCredentialSyncConfigUpdate) -> None:
        settings_service = SettingsService(self.session, role=self.role)
        setting = await settings_service.get_org_setting(
            AWS_CREDENTIAL_SYNC_SETTING_KEY
        )
        existing: AwsCredentialSyncConfig | None = None
        if setting is not None:
            try:
                existing = AwsCredentialSyncConfig.model_validate(
                    settings_service.get_value(setting)
                )
            except (InvalidToken, ValidationError, ValueError) as exc:
                self.logger.warning(
                    "Stored AWS credential sync config is unreadable; overwriting with provided values",
                    error=str(exc),
                )

        merged = existing.model_dump() if existing is not None else {}
        for key, value in params.model_dump(exclude_unset=True).items():
            match key:
                case "access_key_id" | "secret_access_key" | "session_token":
                    if isinstance(value, SecretStr):
                        merged[key] = value.get_secret_value()
                case _:
                    if value is not None:
                        merged[key] = value

        config = AwsCredentialSyncConfig.model_validate(merged)
        if setting is None:
            await settings_service._create_org_setting(
                SettingCreate(
                    key=AWS_CREDENTIAL_SYNC_SETTING_KEY,
                    value=config.model_dump(),
                    is_sensitive=True,
                )
            )
            await self.session.commit()
            return

        await settings_service._update_setting(
            setting,
            SettingUpdate(value=config.model_dump()),
        )
        await self.session.commit()

    async def push_aws_credentials(self) -> CredentialSyncResult:
        secrets_service = self._get_sync_secrets_service()
        config = await self._require_aws_config()
        backend = self._backend_factory(config)
        secrets = await secrets_service.list_secrets(types=set(SYNCABLE_SECRET_TYPES))
        result = CredentialSyncResult(
            provider=CredentialSyncProvider.AWS,
            operation=CredentialSyncOperation.PUSH,
            success=True,
        )
        for secret in secrets:
            result.processed += 1
            remote_name = self._build_secret_name(
                config=config,
                workspace_id=str(secret.workspace_id),
                environment=secret.environment,
                secret_name=secret.name,
            )
            try:
                payload = SyncedSecretPayload(
                    name=secret.name,
                    environment=secret.environment,
                    type=(
                        type_
                        if isinstance((type_ := secret.type), SecretType)
                        else SecretType(type_)
                    ),
                    description=secret.description,
                    tags=secret.tags,
                    keys=[
                        SyncedSecretKeyValue(
                            key=kv.key,
                            value=kv.value.get_secret_value(),
                        )
                        for kv in secrets_service.decrypt_keys(secret.encrypted_keys)
                    ],
                )
                created = await backend.upsert_secret(
                    secret_name=remote_name,
                    secret_string=orjson.dumps(payload.model_dump()).decode("utf-8"),
                )
                if created:
                    result.created += 1
                else:
                    result.updated += 1
            except (ClientError, ValueError, ValidationError) as exc:
                result.failed += 1
                result.errors.append(
                    CredentialSyncErrorItem(
                        secret_name=secret.name,
                        environment=secret.environment,
                        remote_name=remote_name,
                        message=str(exc),
                    )
                )
        result.success = result.failed == 0
        return result

    async def pull_aws_credentials(self) -> CredentialSyncResult:
        role = self._require_workspace_context()
        secrets_service = self._get_sync_secrets_service()
        config = await self._require_aws_config()
        backend = self._backend_factory(config)
        workspace_prefix = self._build_workspace_prefix(
            config=config,
            workspace_id=str(role.workspace_id),
        )
        result = CredentialSyncResult(
            provider=CredentialSyncProvider.AWS,
            operation=CredentialSyncOperation.PULL,
            success=True,
        )
        for remote_secret in await backend.list_secrets(prefix=workspace_prefix):
            result.processed += 1
            payload: SyncedSecretPayload | None = None
            try:
                payload = SyncedSecretPayload.model_validate(
                    orjson.loads(remote_secret.secret_string)
                )
                params = SecretCreate(
                    name=payload.name,
                    type=payload.type,
                    description=payload.description,
                    tags=payload.tags,
                    environment=payload.environment,
                    keys=[
                        SecretKeyValue(
                            key=item.key,
                            value=SecretStr(item.value),
                        )
                        for item in payload.keys
                    ],
                )
                try:
                    existing = await secrets_service.get_secret_by_name(
                        payload.name,
                        payload.environment,
                    )
                except TracecatNotFoundError:
                    await secrets_service.create_secret(params)
                    result.created += 1
                    continue

                await secrets_service.update_secret(
                    existing,
                    SecretUpdate(
                        type=params.type,
                        description=params.description,
                        tags=params.tags,
                        keys=params.keys,
                    ),
                )
                result.updated += 1
            except (ClientError, InvalidToken, ValidationError, ValueError) as exc:
                result.failed += 1
                result.errors.append(
                    CredentialSyncErrorItem(
                        secret_name=payload.name
                        if payload is not None
                        else remote_secret.name,
                        environment=payload.environment
                        if payload is not None
                        else None,
                        remote_name=remote_secret.name,
                        message=str(exc),
                    )
                )
        result.success = result.failed == 0
        return result

    async def _require_aws_config(self) -> AwsCredentialSyncConfig:
        settings_service = SettingsService(self.session, role=self.role)
        setting = await settings_service.get_org_setting(
            AWS_CREDENTIAL_SYNC_SETTING_KEY
        )
        if setting is None:
            raise ValueError("AWS credential sync is not configured")
        try:
            return AwsCredentialSyncConfig.model_validate(
                settings_service.get_value(setting)
            )
        except (InvalidToken, ValidationError, ValueError) as exc:
            raise ValueError(
                "Stored AWS credential sync configuration is invalid. Re-enter the AWS settings and try again."
            ) from exc

    def _serialize_config(
        self, config: AwsCredentialSyncConfig
    ) -> AwsCredentialSyncConfigRead:
        return AwsCredentialSyncConfigRead(
            region=config.region,
            secret_prefix=config.secret_prefix,
            has_access_key_id=bool(config.access_key_id),
            has_secret_access_key=bool(config.secret_access_key),
            has_session_token=bool(config.session_token),
            is_configured=bool(
                config.region
                and config.secret_prefix
                and config.access_key_id
                and config.secret_access_key
            ),
        )

    def _build_secret_name(
        self,
        *,
        config: AwsCredentialSyncConfig,
        workspace_id: str,
        environment: str,
        secret_name: str,
    ) -> str:
        workspace_prefix = self._build_workspace_prefix(
            config=config,
            workspace_id=workspace_id,
        )
        environment_segment = quote(environment or DEFAULT_SECRETS_ENVIRONMENT, safe="")
        secret_segment = quote(secret_name, safe="")
        return (
            f"{workspace_prefix}/environments/{environment_segment}/credentials/"
            f"{secret_segment}"
        )

    def _build_workspace_prefix(
        self, *, config: AwsCredentialSyncConfig, workspace_id: str
    ) -> str:
        normalized_prefix = config.secret_prefix.strip().rstrip("/")
        return (
            f"{normalized_prefix}/organizations/{self.organization_id}/workspaces/"
            f"{workspace_id}"
        )
