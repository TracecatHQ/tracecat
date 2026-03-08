from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import uuid4

import orjson
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.credential_sync import service as credential_sync_service
from tracecat.credential_sync.schemas import (
    AwsCredentialSyncConfigUpdate,
    SyncedSecretKeyValue,
    SyncedSecretPayload,
)
from tracecat.credential_sync.service import CredentialSyncService
from tracecat.credential_sync.types import RemoteSecretRecord
from tracecat.exceptions import TracecatNotFoundError
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.settings.constants import AWS_CREDENTIAL_SYNC_SETTING_KEY


class DummySession:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1


@dataclass
class DummySetting:
    key: str
    value: dict[str, object]


@dataclass
class DummySecret:
    workspace_id: object
    name: str
    type: SecretType
    environment: str
    description: str | None = None
    tags: dict[str, str] | None = None
    encrypted_keys: list[SecretKeyValue] | None = None


class StubCredentialSyncBackend:
    def __init__(self) -> None:
        self.upserted: list[tuple[str, str]] = []
        self.to_create = True
        self.to_raise_for: set[str] = set()
        self.remote_records: list[RemoteSecretRecord] = []

    async def upsert_secret(self, *, secret_name: str, secret_string: str) -> bool:
        self.upserted.append((secret_name, secret_string))
        if secret_name in self.to_raise_for:
            raise ValueError("boom")
        created = self.to_create
        self.to_create = False
        return created

    async def list_secrets(self, *, prefix: str) -> list[RemoteSecretRecord]:
        return [
            record for record in self.remote_records if record.name.startswith(prefix)
        ]


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        user_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"org:credential-sync:manage"}),
    )


@pytest.fixture
def stubbed_services(monkeypatch: pytest.MonkeyPatch):
    settings_store: dict[str, DummySetting] = {}
    secret_store: dict[tuple[str, str], DummySecret] = {}

    class StubSettingsService:
        def __init__(self, session: DummySession, role: Role | None = None) -> None:
            self.session = session
            self.role = role

        async def get_org_setting(self, key: str) -> DummySetting | None:
            return settings_store.get(key)

        def get_value(self, setting: DummySetting) -> dict[str, object]:
            return setting.value

        async def _create_org_setting(self, params) -> DummySetting:
            setting = DummySetting(key=params.key, value=dict(params.value))
            settings_store[params.key] = setting
            return setting

        async def _update_setting(self, setting: DummySetting, params) -> DummySetting:
            setting.value = dict(params.value)
            return setting

    class StubSecretsService:
        def __init__(self, session: DummySession, role: Role | None = None) -> None:
            self.session = session
            if role is None:
                raise ValueError("Role is required for StubSecretsService")
            self.role = role

        def decrypt_keys(
            self, encrypted_keys: list[SecretKeyValue]
        ) -> list[SecretKeyValue]:
            return encrypted_keys

        async def list_secrets(
            self, *, types: set[SecretType] | None = None
        ) -> list[DummySecret]:
            secrets = list(secret_store.values())
            if types is None:
                return secrets
            return [secret for secret in secrets if secret.type in types]

        async def get_secret_by_name(
            self, secret_name: str, environment: str | None = None
        ) -> DummySecret:
            env = environment or "default"
            secret = secret_store.get((secret_name, env))
            if secret is None:
                raise TracecatNotFoundError("missing")
            return secret

        async def create_secret(self, params: SecretCreate) -> DummySecret:
            secret = DummySecret(
                workspace_id=self.role.workspace_id,
                name=params.name,
                type=params.type,
                environment=params.environment,
                description=params.description,
                tags=params.tags,
                encrypted_keys=params.keys,
            )
            secret_store[(secret.name, secret.environment)] = secret
            return secret

        async def update_secret(
            self, secret: DummySecret, params: SecretUpdate
        ) -> DummySecret:
            if params.type is not None:
                secret.type = params.type
            if params.description is not None:
                secret.description = params.description
            if params.tags is not None:
                secret.tags = params.tags
            if params.keys is not None:
                secret.encrypted_keys = params.keys
            return secret

    monkeypatch.setattr(credential_sync_service, "SettingsService", StubSettingsService)
    monkeypatch.setattr(credential_sync_service, "SecretsService", StubSecretsService)
    return settings_store, secret_store


@pytest.fixture
def backend_service(
    role: Role,
    stubbed_services,
) -> tuple[CredentialSyncService, StubCredentialSyncBackend, DummySession, dict, dict]:
    session = DummySession()
    backend = StubCredentialSyncBackend()
    service = CredentialSyncService(
        cast(AsyncSession, session),
        role=role,
        backend_factory=lambda _config: backend,
    )
    settings_store, secret_store = stubbed_services
    return service, backend, session, settings_store, secret_store


async def _configure_service(service: CredentialSyncService) -> None:
    await service.update_aws_config(
        AwsCredentialSyncConfigUpdate(
            region="us-east-1",
            secret_prefix="tracecat/test-sync",
            access_key_id=SecretStr("AKIA_TEST"),
            secret_access_key=SecretStr("secret-test-key"),
            session_token=SecretStr("session-token"),
        )
    )


@pytest.mark.anyio
async def test_update_and_read_aws_sync_config_preserves_secret_values(
    backend_service: tuple[
        CredentialSyncService, StubCredentialSyncBackend, DummySession, dict, dict
    ],
) -> None:
    service, _backend, session, settings_store, _secret_store = backend_service
    await _configure_service(service)

    await service.update_aws_config(
        AwsCredentialSyncConfigUpdate(
            region="us-west-2",
            secret_prefix="tracecat/updated-prefix",
        )
    )

    read = await service.get_aws_config()
    assert read.region == "us-west-2"
    assert read.secret_prefix == "tracecat/updated-prefix"
    assert read.has_access_key_id is True
    assert read.has_secret_access_key is True
    assert read.has_session_token is True
    assert read.is_configured is True
    assert session.commit_count == 2

    stored = settings_store[AWS_CREDENTIAL_SYNC_SETTING_KEY].value
    assert stored["access_key_id"] == "AKIA_TEST"
    assert stored["secret_access_key"] == "secret-test-key"
    assert stored["session_token"] == "session-token"


@pytest.mark.anyio
def test_build_secret_name_is_workspace_scoped(
    backend_service: tuple[
        CredentialSyncService, StubCredentialSyncBackend, DummySession, dict, dict
    ],
) -> None:
    service, _backend, _session, _settings_store, _secret_store = backend_service
    config = credential_sync_service.AwsCredentialSyncConfig(
        region="us-east-1",
        secret_prefix=" tracecat/test-sync/ ",
        access_key_id="AKIA_TEST",
        secret_access_key="secret-test-key",
    )

    secret_name = service._build_secret_name(
        config=config,
        workspace_id="workspace-123",
        environment="prod/us-east-1",
        secret_name="db/password",
    )

    assert secret_name == (
        f"tracecat/test-sync/organizations/{service.organization_id}/workspaces/"
        "workspace-123/environments/prod%2Fus-east-1/credentials/db%2Fpassword"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("secret_type", "keys"),
    [
        (SecretType.CUSTOM, [SecretKeyValue(key="TOKEN", value=SecretStr("alpha"))]),
        (
            SecretType.SSH_KEY,
            [
                SecretKeyValue(
                    key="PRIVATE_KEY",
                    value=SecretStr(
                        "-----BEGIN PRIVATE KEY-----\nssh\n-----END PRIVATE KEY-----"
                    ),
                )
            ],
        ),
        (
            SecretType.MTLS,
            [
                SecretKeyValue(
                    key="TLS_CERTIFICATE",
                    value=SecretStr(
                        "-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----"
                    ),
                ),
                SecretKeyValue(
                    key="TLS_PRIVATE_KEY",
                    value=SecretStr(
                        "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----"
                    ),
                ),
            ],
        ),
        (
            SecretType.CA_CERT,
            [
                SecretKeyValue(
                    key="CA_CERTIFICATE",
                    value=SecretStr(
                        "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"
                    ),
                )
            ],
        ),
    ],
)
async def test_push_serializes_all_supported_secret_types(
    backend_service: tuple[
        CredentialSyncService, StubCredentialSyncBackend, DummySession, dict, dict
    ],
    secret_type: SecretType,
    keys: list[SecretKeyValue],
) -> None:
    service, backend, _session, _settings_store, secret_store = backend_service
    await _configure_service(service)
    secret_store[(f"{secret_type.value}-secret", "default")] = DummySecret(
        workspace_id=service.role.workspace_id,
        name=f"{secret_type.value}-secret",
        type=secret_type,
        environment="default",
        description="sync me",
        encrypted_keys=keys,
    )

    result = await service.push_aws_credentials()

    assert result.failed == 0
    assert result.processed == 1
    assert len(backend.upserted) == 1
    pushed_name, pushed_payload = backend.upserted[0]
    assert str(service.organization_id) in pushed_name
    assert str(service.role.workspace_id) in pushed_name
    payload = SyncedSecretPayload.model_validate(orjson.loads(pushed_payload))
    assert payload.name == f"{secret_type.value}-secret"
    assert payload.type == secret_type


@pytest.mark.anyio
async def test_push_reports_remote_failures(
    backend_service: tuple[
        CredentialSyncService, StubCredentialSyncBackend, DummySession, dict, dict
    ],
) -> None:
    service, backend, _session, _settings_store, secret_store = backend_service
    await _configure_service(service)
    secret_store[("success_secret", "default")] = DummySecret(
        workspace_id=service.role.workspace_id,
        name="success_secret",
        type=SecretType.CUSTOM,
        environment="default",
        encrypted_keys=[SecretKeyValue(key="TOKEN", value=SecretStr("alpha"))],
    )
    secret_store[("error_secret", "default")] = DummySecret(
        workspace_id=service.role.workspace_id,
        name="error_secret",
        type=SecretType.CUSTOM,
        environment="default",
        encrypted_keys=[SecretKeyValue(key="TOKEN", value=SecretStr("beta"))],
    )

    error_remote_name = (
        f"tracecat/test-sync/organizations/{service.organization_id}/workspaces/"
        f"{service.role.workspace_id}/environments/default/credentials/error_secret"
    )
    backend.to_raise_for.add(error_remote_name)

    result = await service.push_aws_credentials()

    assert result.processed == 2
    assert result.failed == 1
    assert result.created == 1
    assert result.success is False
    assert result.errors[0].secret_name == "error_secret"


@pytest.mark.anyio
async def test_pull_upserts_and_does_not_delete_missing_locals(
    backend_service: tuple[
        CredentialSyncService, StubCredentialSyncBackend, DummySession, dict, dict
    ],
) -> None:
    service, backend, _session, _settings_store, secret_store = backend_service
    await _configure_service(service)
    secret_store[("existing_secret", "default")] = DummySecret(
        workspace_id=service.role.workspace_id,
        name="existing_secret",
        type=SecretType.CUSTOM,
        description="old",
        environment="default",
        encrypted_keys=[SecretKeyValue(key="TOKEN", value=SecretStr("old"))],
    )
    secret_store[("untouched_secret", "default")] = DummySecret(
        workspace_id=service.role.workspace_id,
        name="untouched_secret",
        type=SecretType.CUSTOM,
        description="stay",
        environment="default",
        encrypted_keys=[SecretKeyValue(key="TOKEN", value=SecretStr("stay"))],
    )

    prefix = (
        f"tracecat/test-sync/organizations/{service.organization_id}/workspaces/"
        f"{service.role.workspace_id}"
    )
    backend.remote_records = [
        RemoteSecretRecord(
            name=f"{prefix}/environments/default/credentials/existing_secret",
            secret_string=orjson.dumps(
                SyncedSecretPayload(
                    name="existing_secret",
                    environment="default",
                    type=SecretType.CUSTOM,
                    description="new",
                    keys=[SyncedSecretKeyValue(key="TOKEN", value="new")],
                ).model_dump()
            ).decode("utf-8"),
        ),
        RemoteSecretRecord(
            name=f"{prefix}/environments/default/credentials/new_secret",
            secret_string=orjson.dumps(
                SyncedSecretPayload(
                    name="new_secret",
                    environment="default",
                    type=SecretType.CUSTOM,
                    description="created",
                    keys=[SyncedSecretKeyValue(key="TOKEN", value="created")],
                ).model_dump()
            ).decode("utf-8"),
        ),
        RemoteSecretRecord(
            name=f"{prefix}/environments/default/credentials/bad_secret",
            secret_string='{"invalid": true}',
        ),
    ]

    result = await service.pull_aws_credentials()

    assert result.processed == 3
    assert result.updated == 1
    assert result.created == 1
    assert result.failed == 1

    existing = secret_store[("existing_secret", "default")]
    assert existing.description == "new"
    assert existing.encrypted_keys is not None
    assert existing.encrypted_keys[0].value.get_secret_value() == "new"

    created = secret_store[("new_secret", "default")]
    assert created.description == "created"
    assert created.encrypted_keys is not None
    assert created.encrypted_keys[0].value.get_secret_value() == "created"

    untouched = secret_store[("untouched_secret", "default")]
    assert untouched.description == "stay"
    assert untouched.encrypted_keys is not None
    assert untouched.encrypted_keys[0].value.get_secret_value() == "stay"
