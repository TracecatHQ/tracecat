"""Isolated regressions for external secret store API contracts."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.secrets.providers.aws_secrets_manager import AwsSecretsManagerBackend
from tracecat_ee.secrets.references import router, workflows
from tracecat_ee.secrets.references.service import SecretReferencesService
from tracecat_ee.secrets.stores.service import SecretStoresService

from tracecat import config
from tracecat.agent import service as agent_service
from tracecat.api.app import create_app
from tracecat.auth import sandbox as auth_sandbox
from tracecat.auth.types import Role
from tracecat.db.models import OrganizationSecretStore, Secret
from tracecat.exceptions import EntitlementRequired, TracecatConflictError
from tracecat.expressions.parser.core import parser
from tracecat.identifiers import SecretUUID
from tracecat.pagination import PageParams, PaginationError
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.enums import SecretSource
from tracecat.secrets.schemas import (
    AwsSecretKeyMapping,
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    AwsSecretsManagerStoreCreate,
    AwsSecretsManagerStoreUpdate,
    SecretReferenceCheckRequest,
    SecretReferenceCheckResult,
    SecretStoreUpdate,
    SecretUpdate,
)
from tracecat.secrets.service import SecretsService
from tracecat.tiers.enums import Entitlement


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"secret:read", "agent:read", "org:secret:delete"}),
    )


@pytest.mark.parametrize("mode", ["whole_string", "json"])
@pytest.mark.parametrize("key", ["API-KEY", "9TOKEN", "💣KEY", "TOKEN\n", "A.B", ""])
def test_mapping_rejects_unreferenceable_keys(mode: str, key: str) -> None:
    mapping = (
        {"mode": mode, "keys": [key]}
        if mode == "whole_string"
        else {"mode": mode, "fields": [{"key": key, "field": "remote-field"}]}
    )
    with pytest.raises(ValidationError):
        AwsSecretKeyMapping.model_validate(mapping)


@pytest.mark.parametrize("key", ["TOKEN", "_TOKEN", "a9", "_"])
def test_valid_mapping_keys_parse_in_expressions(key: str) -> None:
    for mapping in (
        {"mode": "whole_string", "keys": [key]},
        {"mode": "json", "fields": [{"key": key, "field": "9-remote-field"}]},
    ):
        assert AwsSecretKeyMapping.model_validate(mapping).output_keys() == [key]
        parser.parse(f"SECRETS.example.{key}")


@pytest.mark.anyio
async def test_check_dispatches_to_executor_without_resolving_in_api(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_KEY", "synthetic-test-key")
    secret = Secret(
        id=SecretUUID.new(uuid.uuid4()), source=SecretSource.AWS_SECRETS_MANAGER
    )
    monkeypatch.setattr(
        SecretReferencesService, "get_secret", AsyncMock(return_value=secret)
    )
    local_check = AsyncMock(side_effect=AssertionError("AWS must run on executor"))
    monkeypatch.setattr(
        SecretReferencesService, "check_aws_secret_reference", local_check
    )
    expected = SecretReferenceCheckResult(ok=True, resolved_keys=["TOKEN"])
    client = MagicMock(execute_workflow=AsyncMock(return_value=expected))
    monkeypatch.setattr(router, "get_temporal_client", AsyncMock(return_value=client))
    session = AsyncMock(spec=AsyncSession)

    result = await router.check_aws_secret_reference(
        role=role, session=session, secret_id=SecretUUID.new(secret.id)
    )

    assert result == expected
    local_check.assert_not_awaited()
    session.commit.assert_awaited_once()
    call = client.execute_workflow.call_args
    assert call.kwargs["task_queue"] == config.TRACECAT__EXECUTOR_QUEUE
    request = call.args[1]
    assert request.secret_id == secret.id
    assert request.role == role
    assert set(request.model_dump()) == {"role", "secret_id"}


@pytest.mark.anyio
@pytest.mark.parametrize("fails", [False, True])
async def test_executor_reloads_reference_and_sanitizes_failures(
    monkeypatch: pytest.MonkeyPatch, role: Role, fails: bool
) -> None:
    secret = Secret(id=uuid.uuid4())
    expected = SecretReferenceCheckResult(ok=True, resolved_keys=["TOKEN"])
    service = MagicMock(
        get_secret=AsyncMock(return_value=secret),
        check_aws_secret_reference=AsyncMock(
            side_effect=RuntimeError("synthetic-secret-value") if fails else None,
            return_value=expected,
        ),
    )

    @asynccontextmanager
    async def with_session(*, role: Role):
        assert role.workspace_id is not None
        yield service

    monkeypatch.setattr(SecretReferencesService, "with_session", with_session)
    entitlement = AsyncMock()
    monkeypatch.setattr(workflows, "check_entitlement", entitlement)
    result = await workflows.check_secret_reference_activity(
        SecretReferenceCheckRequest(role=role, secret_id=secret.id)
    )
    entitlement.assert_awaited_once()
    service.get_secret.assert_awaited_once_with(secret.id)
    service.check_aws_secret_reference.assert_awaited_once_with(secret)
    assert result.ok is not fails
    assert "synthetic-secret-value" not in result.model_dump_json()
    assert result.resolved_keys == ([] if fails else ["TOKEN"])


@pytest.mark.anyio
@pytest.mark.parametrize("workspace", [False, True])
async def test_collection_queries_are_bounded_and_cursors_are_scoped(
    monkeypatch: pytest.MonkeyPatch, role: Role, workspace: bool
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", "synthetic-signing-key")
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_KEY", "synthetic-test-key")
    timestamp = datetime.now(UTC)
    stores = [
        OrganizationSecretStore(id=uuid.UUID(int=index), created_at=timestamp)
        for index in (1, 2)
    ]
    session = AsyncMock(spec=AsyncSession)
    session.get_bind.return_value = MagicMock(dialect=postgresql.dialect())
    session.execute.return_value = MagicMock(
        all=lambda: [(store, timestamp, store.id) for store in stores]
    )
    list_page = (
        SecretReferencesService(session, role).list_authorized_stores
        if workspace
        else SecretStoresService(session, role).list_stores
    )
    first = await list_page(PageParams(limit=1))
    assert first.items == stores[:1]
    assert first.next_cursor is not None
    query = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "LIMIT" in str(query)
    assert list(query.params.values())[-1] == 2  # One row plus lookahead.
    assert role.organization_id in query.params.values()
    if workspace:
        assert role.workspace_id in query.params.values()

    session.execute.return_value = MagicMock(
        all=lambda: [(stores[1], timestamp, stores[1].id)]
    )
    last = await list_page(PageParams(limit=1, cursor=first.next_cursor))
    assert last.items == stores[1:]
    assert last.next_cursor is None
    assert last.prev_cursor is not None

    other_role = role.model_copy(update={"organization_id": uuid.uuid4()})
    other_list = (
        SecretReferencesService(session, other_role).list_authorized_stores
        if workspace
        else SecretStoresService(session, other_role).list_stores
    )
    with pytest.raises(PaginationError):
        await other_list(PageParams(limit=1, cursor=first.next_cursor))


@pytest.mark.parametrize("name", ["api-key", "9token", "App", "a.b", ""])
def test_reference_name_rejects_unreferenceable_aliases(name: str) -> None:
    mapping = {"mode": "whole_string", "keys": ["TOKEN"]}
    with pytest.raises(ValidationError):
        AwsSecretReferenceCreate.model_validate(
            {
                "name": name,
                "store_id": uuid.uuid4(),
                "remote_reference": "synthetic/secret",
                "key_mapping": mapping,
            }
        )
    with pytest.raises(ValidationError):
        AwsSecretReferenceUpdate.model_validate({"name": name})


def test_reference_description_fits_column() -> None:
    mapping = {"mode": "whole_string", "keys": ["TOKEN"]}
    with pytest.raises(ValidationError):
        AwsSecretReferenceCreate.model_validate(
            {
                "name": "app_db",
                "description": "x" * 256,
                "store_id": uuid.uuid4(),
                "remote_reference": "synthetic/secret",
                "key_mapping": mapping,
            }
        )
    with pytest.raises(ValidationError):
        AwsSecretReferenceUpdate.model_validate({"description": "x" * 256})
    with pytest.raises(ValidationError):
        SecretUpdate.model_validate({"description": "x" * 256})


@pytest.mark.parametrize("environment", ["", "x" * 101])
def test_reference_environment_fits_column(environment: str) -> None:
    mapping = {"mode": "whole_string", "keys": ["TOKEN"]}
    with pytest.raises(ValidationError):
        AwsSecretReferenceCreate.model_validate(
            {
                "name": "app_db",
                "environment": environment,
                "store_id": uuid.uuid4(),
                "remote_reference": "synthetic/secret",
                "key_mapping": mapping,
            }
        )


@pytest.mark.parametrize("field", ["name", "enabled"])
def test_store_update_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError):
        SecretStoreUpdate.model_validate({field: None})
    assert field not in SecretStoreUpdate.model_validate({}).model_dump(
        exclude_unset=True
    )


def test_store_listing_does_not_shadow_secret_names() -> None:
    paths = create_app().openapi()["paths"]
    assert "/workspaces/{workspace_id}/secret-stores" in paths
    assert "/workspaces/{workspace_id}/secrets/stores" not in paths


@pytest.mark.parametrize("model", [AwsSecretReferenceUpdate, SecretUpdate])
@pytest.mark.parametrize("field", ["name", "environment"])
def test_secret_update_rejects_explicit_null(
    model: type[AwsSecretReferenceUpdate] | type[SecretUpdate], field: str
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({field: None})
    assert field not in model.model_validate({}).model_dump(exclude_unset=True)


@pytest.mark.parametrize(
    ("role_arn", "region"),
    [
        ("arn:aws:iam::123456789012:role/reader", "cn-north-1"),
        ("arn:aws-us-gov:iam::123456789012:role/reader", "us-east-1"),
    ],
)
def test_store_config_rejects_mixed_partitions(role_arn: str, region: str) -> None:
    with pytest.raises(ValidationError):
        AwsSecretsManagerStoreCreate(role_arn=role_arn, region=region)
    config = AwsSecretsManagerBackend().new_config(
        AwsSecretsManagerStoreCreate(
            role_arn="arn:aws:iam::123456789012:role/reader", region="us-east-1"
        )
    )
    with pytest.raises(ValueError):
        AwsSecretsManagerBackend().update_config(
            config, AwsSecretsManagerStoreUpdate(role_arn=role_arn, region=region)
        )


def test_store_config_allows_regions_unknown_to_botocore() -> None:
    config = AwsSecretsManagerStoreCreate(
        role_arn="arn:aws:iam::123456789012:role/reader", region="xx-future-1"
    )
    assert config.region == "xx-future-1"


@pytest.mark.parametrize("name", ["app_db", "_token", "a9"])
def test_valid_reference_names_parse_in_expressions(name: str) -> None:
    assert AwsSecretReferenceUpdate.model_validate({"name": name}).name == name
    parser.parse(f"SECRETS.{name}.TOKEN")


def _external_secret(name: str) -> Secret:
    return Secret(
        id=uuid.uuid4(),
        name=name,
        environment="default",
        source=SecretSource.AWS_SECRETS_MANAGER,
        encrypted_keys=b"",
        remote_reference="synthetic-key",
        remote_key_mapping={"mode": "whole_string", "keys": ["TOKEN"]},
        store=OrganizationSecretStore(
            id=uuid.uuid4(),
            enabled=True,
            provider="aws_secrets_manager",
            config={
                "provider": "aws_secrets_manager",
                "region": "us-east-1",
                "role_arn": "arn:aws:iam::123456789012:role/test-reader",
                "external_id": "synthetic-external-id",
            },
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["async", "sync"])
async def test_failed_sandbox_entry_clears_fetched_values(
    monkeypatch: pytest.MonkeyPatch, role: Role, entry: str
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    undecryptable = Secret(
        id=uuid.uuid4(),
        name="local",
        environment="default",
        source=SecretSource.LOCAL,
        encrypted_keys=b"not-a-fernet-token",
    )
    monkeypatch.setattr(
        auth_sandbox.AuthSandbox,
        "_get_secrets",
        AsyncMock(return_value=[_external_secret("remote"), undecryptable]),
    )
    backend = MagicMock(
        resolve=AsyncMock(return_value={"remote": {"TOKEN": "synthetic-value"}})
    )
    monkeypatch.setattr(auth_sandbox, "get_backend", lambda _: backend)

    sandbox = auth_sandbox.AuthSandbox(role=role, secrets=["remote", "local"])
    with pytest.raises(InvalidToken):
        if entry == "async":
            await sandbox.__aenter__()
        else:
            await asyncio.to_thread(sandbox.__enter__)
    backend.resolve.assert_awaited_once()
    assert sandbox._external_values == {}
    assert sandbox.secrets == {}


@pytest.mark.anyio
@pytest.mark.parametrize("caller", ["sandbox", "agent"])
@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("entitled", [False, True])
async def test_runtime_entitlement_precedes_external_fetch(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    caller: str,
    external: bool,
    entitled: bool,
) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_KEY", key)
    secret = Secret(
        id=uuid.uuid4(),
        name="openai",
        environment="default",
        source=SecretSource.AWS_SECRETS_MANAGER if external else SecretSource.LOCAL,
        encrypted_keys=encrypt_keyvalues([], key=key),
        remote_reference="synthetic-key",
        remote_key_mapping={"mode": "whole_string", "keys": ["TOKEN"]},
        store=OrganizationSecretStore(
            id=uuid.uuid4(),
            enabled=True,
            provider="aws_secrets_manager",
            config={
                "provider": "aws_secrets_manager",
                "region": "us-east-1",
                "role_arn": "arn:aws:iam::123456789012:role/test-reader",
                "external_id": "synthetic-external-id",
            },
        ),
    )
    session = AsyncMock(spec=AsyncSession)
    search = AsyncMock(return_value=[secret])
    monkeypatch.setattr(SecretsService, "search_secrets", search)
    entitlement = AsyncMock(
        side_effect=None if entitled else EntitlementRequired("external_secret_stores")
    )
    backend = MagicMock(
        resolve=AsyncMock(return_value={"openai": {"TOKEN": "synthetic-value"}})
    )
    module = auth_sandbox if caller == "sandbox" else agent_service
    monkeypatch.setattr(module, "check_entitlement", entitlement)
    monkeypatch.setattr(module, "get_backend", lambda _: backend)

    @asynccontextmanager
    async def with_session(*, role: Role):
        yield SecretsService(session, role=role)

    monkeypatch.setattr(SecretsService, "with_session", with_session)
    operation = (
        auth_sandbox.AuthSandbox(
            role=role, secrets=["openai"], optional_secrets=["openai"]
        )._load_secrets()
        if caller == "sandbox"
        else agent_service.AgentManagementService(
            session, role
        ).get_workspace_provider_credentials("openai")
    )
    if external and not entitled:
        with pytest.raises(EntitlementRequired):
            await operation
        backend.resolve.assert_not_awaited()
    else:
        await operation
        assert backend.resolve.await_count == int(external)
    if external:
        entitlement.assert_awaited_once_with(
            session, role, Entitlement.EXTERNAL_SECRET_STORES
        )
    else:
        entitlement.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_conflict_rolls_back_without_detaching_references(
    role: Role,
) -> None:
    assert OrganizationSecretStore.secrets.property.passive_deletes == "all"
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = IntegrityError("DELETE", {}, Exception("foreign key"))
    service = SecretStoresService(session, role)
    service.count_references = AsyncMock(return_value={})
    store = OrganizationSecretStore(id=uuid.uuid4())
    with pytest.raises(TracecatConflictError):
        await service.delete_store(store)
    session.rollback.assert_awaited_once()
