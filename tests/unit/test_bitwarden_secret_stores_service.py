"""DB-backed tests for Bitwarden Secrets Manager stores and their access token."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.secrets.references import router as references_router
from tracecat_ee.secrets.references.service import (
    NOT_AWS_MESSAGE,
    SecretReferencesService,
)
from tracecat_ee.secrets.stores import router as stores_router
from tracecat_ee.secrets.stores.backends import get_backend, parse_store_config
from tracecat_ee.secrets.stores.service import SecretStoresService

from tracecat.api.common import serialize_validation_errors
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.db.models import Organization, OrganizationSecretStore, Secret, Workspace
from tracecat.exceptions import TracecatConflictError
from tracecat.identifiers import SecretUUID
from tracecat.organization.management import delete_organization_with_cleanup
from tracecat.secrets.encryption import decrypt_value
from tracecat.secrets.enums import SecretMappingMode, SecretSource, SecretStoreProvider
from tracecat.secrets.schemas import (
    AwsSecretReferenceUpdate,
    AwsSecretsManagerStoreCreate,
    BitwardenSecretsManagerStoreConfig,
    BitwardenSecretsManagerStoreCreate,
    BitwardenSecretsManagerStoreUpdate,
    SecretKeyMapping,
    SecretReferenceCreate,
    SecretStoreCreate,
    SecretStoreUpdate,
)

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

BITWARDEN = SecretStoreProvider.BITWARDEN_SECRETS_MANAGER
TOKEN = "0.planted-client-id.planted-secret:cGxhbnRlZC1rZXktMTIzNA=="
ROTATED = "0.rotated-client-id.rotated-secret:cm90YXRlZC1rZXktMTIzNA=="


@pytest.fixture
async def stores(session: AsyncSession, svc_admin_role: Role) -> SecretStoresService:
    return SecretStoresService(session=session, role=svc_admin_role)


def _create(
    name: str, token: str | None = TOKEN, *, enabled: bool = True
) -> SecretStoreCreate:
    return SecretStoreCreate(
        name=name,
        provider=BITWARDEN,
        config=BitwardenSecretsManagerStoreCreate(
            provider=BITWARDEN, server="eu", organization_id=uuid.uuid4()
        ),
        access_token=SecretStr(token) if token is not None else None,
        enabled=enabled,
    )


def _stored_token(store: OrganizationSecretStore) -> str:
    assert store.encrypted_access_token is not None
    return decrypt_value(
        store.encrypted_access_token, key=get_db_encryption_key()
    ).decode()


async def _reference(
    session: AsyncSession,
    role: Role,
    workspace: Workspace,
    store: OrganizationSecretStore,
) -> Secret:
    await SecretStoresService(session=session, role=role).authorize_workspace(
        store, workspace.id
    )
    return await SecretReferencesService(
        session=session, role=role
    ).create_secret_reference(
        SecretReferenceCreate(
            name="bw_ref",
            store_id=store.id,
            remote_reference=str(uuid.uuid4()),
            key_mapping=SecretKeyMapping(
                mode=SecretMappingMode.WHOLE_STRING, keys=["TOKEN"]
            ),
        )
    )


async def test_token_is_stored_encrypted_and_enable_needs_one(
    stores: SecretStoresService,
) -> None:
    # Given a Bitwarden store created with an access token
    store = await stores.create_store(_create("bitwarden"))
    # Then the column holds ciphertext of that token, not the token
    assert store.encrypted_access_token is not None
    assert TOKEN.encode() not in store.encrypted_access_token
    assert _stored_token(store) == TOKEN

    # Given a store without a token, Then it cannot be created enabled
    with pytest.raises(ValueError, match="Set an access token"):
        await stores.create_store(_create("bitwarden-none", None))
    disabled = await stores.create_store(_create("bitwarden-none", None, enabled=False))
    # and cannot be enabled until a token is set
    with pytest.raises(ValueError, match="Set an access token"):
        await stores.update_store(disabled, SecretStoreUpdate(enabled=True))
    await stores.update_store(
        disabled, SecretStoreUpdate(enabled=True, access_token=SecretStr(TOKEN))
    )
    assert disabled.enabled is True
    assert _stored_token(disabled) == TOKEN


async def test_update_omitted_keeps_value_replaces_and_null_is_refused(
    stores: SecretStoresService,
) -> None:
    store = await stores.create_store(_create("bitwarden"))

    # Omitted: a rename leaves the token unchanged
    await stores.update_store(store, SecretStoreUpdate(name="bitwarden-renamed"))
    assert _stored_token(store) == TOKEN

    # A value replaces it
    await stores.update_store(store, SecretStoreUpdate(access_token=SecretStr(ROTATED)))
    assert _stored_token(store) == ROTATED

    # Explicit null is refused, so a token cannot be cleared by accident
    with pytest.raises(ValidationError):
        SecretStoreUpdate.model_validate({"access_token": None})


async def test_rejected_input_never_echoes_the_token(
    stores: SecretStoresService,
) -> None:
    # A 422 returns each validation error's input. A store whose top-level
    # provider is omitted (it defaults to AWS) fails on its config, and that
    # error must not carry the rest of the body.
    with pytest.raises(ValidationError) as mismatch:
        SecretStoreCreate.model_validate(
            {
                "name": "bitwarden",
                "config": {
                    "provider": BITWARDEN,
                    "server": "eu",
                    "organization_id": str(uuid.uuid4()),
                },
                "access_token": TOKEN,
            }
        )
    errors = serialize_validation_errors(mismatch.value.errors())
    assert [error["msg"] for error in errors] == [
        "Value error, config.provider must match provider"
    ]
    assert TOKEN not in str(errors)

    # The token's length is checked by the service, whose message has no input
    too_long = SecretStr("x" * 4097)
    with pytest.raises(ValueError, match="1 to 4096 characters") as refused:
        await stores.create_store(_create("bitwarden", too_long.get_secret_value()))
    assert "xxxx" not in str(refused.value)
    store = await stores.create_store(_create("bitwarden"))
    for bad in (too_long, SecretStr("")):
        with pytest.raises(ValueError, match="1 to 4096 characters"):
            await stores.update_store(store, SecretStoreUpdate(access_token=bad))
    assert _stored_token(store) == TOKEN


async def test_api_only_says_whether_a_token_is_set(
    session: AsyncSession,
    stores: SecretStoresService,
    svc_admin_role: Role,
    svc_workspace: Workspace,
) -> None:
    created = await stores_router.create_secret_store(
        role=svc_admin_role, session=session, params=_create("bitwarden")
    )
    without = await stores_router.create_secret_store(
        role=svc_admin_role,
        session=session,
        params=_create("bitwarden-none", None, enabled=False),
    )
    assert created.access_token_set is True
    assert without.access_token_set is False

    # Every read of the store carries the flag and never the token
    fetched = await stores_router.get_secret_store(
        role=svc_admin_role, session=session, store_id=created.id
    )
    listed = await stores_router.list_secret_stores(
        role=svc_admin_role, session=session, limit=50, cursor=None
    )
    store = await stores.get_store(created.id)
    await stores.authorize_workspace(store, svc_workspace.id)
    workspace_view = await references_router.list_authorized_secret_stores(
        role=svc_admin_role, session=session, limit=50, cursor=None
    )
    for response in (created, fetched, listed, workspace_view):
        dumped = response.model_dump_json()
        assert TOKEN not in dumped
        assert "planted" not in dumped
    assert fetched.access_token_set is True
    # and SecretStr keeps it out of the request model's repr
    assert TOKEN not in repr(_create("bitwarden-repr"))


async def test_rotation_is_allowed_while_server_and_organization_are_fixed(
    session: AsyncSession,
    stores: SecretStoresService,
    svc_admin_role: Role,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(_create("bitwarden"))
    created = await _reference(session, svc_admin_role, svc_workspace, store)
    assert created.source == SecretSource.BITWARDEN_SECRETS_MANAGER
    created_id = created.id
    original = parse_store_config(store)
    assert isinstance(original, BitwardenSecretsManagerStoreConfig)

    # Given a Bitwarden store with one reference, When server or org changes
    for change in (
        BitwardenSecretsManagerStoreUpdate(provider=BITWARDEN, server="us"),
        BitwardenSecretsManagerStoreUpdate(
            provider=BITWARDEN, organization_id=uuid.uuid4()
        ),
    ):
        with pytest.raises(TracecatConflictError):
            await stores.update_store(store, SecretStoreUpdate(config=change))
        await session.rollback()
        await session.refresh(store)
    # Then nothing changed
    await session.refresh(store)
    assert parse_store_config(store) == original

    # When they replace the access token, Then it is accepted
    await stores.update_store(store, SecretStoreUpdate(access_token=SecretStr(ROTATED)))
    assert _stored_token(store) == ROTATED

    # Given the same store with no references, When they change server
    await session.execute(delete(Secret).where(Secret.id == created_id))
    await session.commit()
    await stores.update_store(
        store,
        SecretStoreUpdate(
            config=BitwardenSecretsManagerStoreUpdate(provider=BITWARDEN, server="us")
        ),
    )
    # Then it is accepted
    updated = parse_store_config(store)
    assert isinstance(updated, BitwardenSecretsManagerStoreConfig)
    assert updated.server == "us"
    assert updated.organization_id == original.organization_id


async def test_organization_deletion_removes_the_store(session: AsyncSession) -> None:
    organization = Organization(
        id=uuid.uuid4(), name="bw-delete", slug=f"bw-delete-{uuid.uuid4().hex[:8]}"
    )
    session.add(organization)
    await session.flush()
    session.add(
        OrganizationSecretStore(
            organization_id=organization.id,
            name="bitwarden",
            provider=BITWARDEN,
            config={
                "provider": BITWARDEN,
                "server": "eu",
                "organization_id": str(uuid.uuid4()),
            },
            encrypted_access_token=b"sealed",
        )
    )
    await session.commit()

    await delete_organization_with_cleanup(
        session, organization=organization, operator_user_id=None
    )
    await session.commit()

    assert (
        await session.scalar(
            select(OrganizationSecretStore).where(
                OrganizationSecretStore.organization_id == organization.id
            )
        )
    ) is None


async def test_aws_refuses_a_token_and_self_hosted_servers_must_be_public(
    session: AsyncSession, stores: SecretStoresService, svc_admin_role: Role
) -> None:
    # A provider that needs no token refuses one, as a 422 through the API
    aws_with_token = SecretStoreCreate(
        name="aws-with-token",
        config=AwsSecretsManagerStoreCreate(
            role_arn="arn:aws:iam::123456789012:role/reader", region="us-east-1"
        ),
        access_token=SecretStr(TOKEN),
    )
    with pytest.raises(ValueError, match="does not use an access token"):
        await stores.create_store(aws_with_token)
    with pytest.raises(HTTPException) as refused:
        await stores_router.create_secret_store(
            role=svc_admin_role, session=session, params=aws_with_token
        )
    assert refused.value.status_code == 422
    assert TOKEN not in str(refused.value.detail)

    # A self-hosted server must resolve to a public address, on create and update
    private = SecretStoreCreate(
        name="private-bitwarden",
        provider=BITWARDEN,
        config=BitwardenSecretsManagerStoreCreate(
            provider=BITWARDEN,
            server="https://127.0.0.1:8443",
            organization_id=uuid.uuid4(),
        ),
        access_token=SecretStr(TOKEN),
    )
    with pytest.raises(ValueError, match="public address"):
        await stores.create_store(private)
    store = await stores.create_store(_create("bitwarden"))
    with pytest.raises(ValueError, match="public address"):
        await stores.update_store(
            store,
            SecretStoreUpdate(
                config=BitwardenSecretsManagerStoreUpdate(
                    provider=BITWARDEN, server="https://169.254.169.254"
                )
            ),
        )


async def test_unchanged_config_is_not_rechecked(
    monkeypatch: pytest.MonkeyPatch, stores: SecretStoresService
) -> None:
    # The server's DNS is down: a rename that resends the same config still
    # works; a server change is still refused.
    backend = get_backend(BITWARDEN)
    monkeypatch.setattr(backend, "check_config", AsyncMock())
    store = await stores.create_store(
        SecretStoreCreate(
            name="self-hosted",
            provider=BITWARDEN,
            config=BitwardenSecretsManagerStoreCreate(
                provider=BITWARDEN,
                server="https://vault.example.com",
                organization_id=uuid.uuid4(),
            ),
            access_token=SecretStr(TOKEN),
        )
    )
    monkeypatch.setattr(
        backend, "check_config", AsyncMock(side_effect=ValueError("public address"))
    )
    await stores.update_store(
        store,
        SecretStoreUpdate(
            name="self-hosted-renamed",
            config=BitwardenSecretsManagerStoreUpdate(
                provider=BITWARDEN, server="https://vault.example.com"
            ),
        ),
    )
    assert store.name == "self-hosted-renamed"
    with pytest.raises(ValueError, match="public address"):
        await stores.update_store(
            store,
            SecretStoreUpdate(
                config=BitwardenSecretsManagerStoreUpdate(
                    provider=BITWARDEN, server="https://vault2.example.com"
                )
            ),
        )


async def test_config_check_runs_before_the_store_row_is_locked(
    monkeypatch: pytest.MonkeyPatch, stores: SecretStoresService
) -> None:
    # The check may wait on DNS; the lock blocks reference writes to the store.
    backend = get_backend(BITWARDEN)
    monkeypatch.setattr(backend, "check_config", AsyncMock())
    store = await stores.create_store(_create("bitwarden"))
    order: list[str] = []
    monkeypatch.setattr(
        backend,
        "check_config",
        AsyncMock(side_effect=lambda config: order.append("check")),
    )
    refresh = stores.session.refresh

    async def recording_refresh(instance: object, *args: object, **kwargs: object):
        if kwargs.get("with_for_update"):
            order.append("lock")
        return await refresh(instance, *args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(stores.session, "refresh", recording_refresh)

    await stores.update_store(
        store,
        SecretStoreUpdate(
            config=BitwardenSecretsManagerStoreUpdate(
                provider=BITWARDEN, server="https://vault.example.com"
            )
        ),
    )

    assert order == ["check", "lock"]
    assert store.config["server"] == "https://vault.example.com"


async def test_a_store_changed_before_the_lock_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch, stores: SecretStoresService
) -> None:
    # Another save lands between the config check and the lock, so the
    # config about to be committed was checked against a stale store.
    backend = get_backend(BITWARDEN)
    monkeypatch.setattr(backend, "check_config", AsyncMock())
    store = await stores.create_store(_create("bitwarden"))
    saved_elsewhere = {**store.config, "server": "https://other.example.com"}
    refresh = stores.session.refresh

    async def concurrent_save(instance: object, *args: object, **kwargs: object):
        if kwargs.get("with_for_update"):
            # The row changes in the database; the locking refresh reloads it.
            await stores.session.execute(
                update(OrganizationSecretStore)
                .where(OrganizationSecretStore.id == store.id)
                .values(config=saved_elsewhere)
            )
        return await refresh(instance, *args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(stores.session, "refresh", concurrent_save)

    with pytest.raises(TracecatConflictError, match="changed"):
        await stores.update_store(
            store,
            SecretStoreUpdate(
                config=BitwardenSecretsManagerStoreUpdate(
                    provider=BITWARDEN, server="https://vault.example.com"
                )
            ),
        )
    assert store.config == saved_elsewhere


async def test_deprecated_aws_check_route_refuses_a_bitwarden_reference(
    session: AsyncSession,
    stores: SecretStoresService,
    svc_admin_role: Role,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(_create("bitwarden"))
    reference = await _reference(session, svc_admin_role, svc_workspace, store)

    # The AWS alias refuses it before any check runs, as it refuses local secrets
    with pytest.raises(HTTPException) as refused:
        await references_router.check_aws_secret_reference(
            role=svc_admin_role,
            session=session,
            secret_id=SecretUUID.new(reference.id),
        )
    assert refused.value.status_code == 422
    assert refused.value.detail == NOT_AWS_MESSAGE


async def test_deprecated_aws_update_route_cannot_move_a_bitwarden_secret_to_aws(
    session: AsyncSession,
    stores: SecretStoresService,
    svc_admin_role: Role,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(_create("bitwarden"))
    reference = await _reference(session, svc_admin_role, svc_workspace, store)
    aws_store = await stores.create_store(
        SecretStoreCreate(
            name="aws",
            config=AwsSecretsManagerStoreCreate(
                role_arn="arn:aws:iam::123456789012:role/reader", region="us-east-1"
            ),
        )
    )
    await stores.authorize_workspace(aws_store, svc_workspace.id)

    # Pointing the Bitwarden secret at an AWS store through the AWS alias is
    # refused as the check alias refuses it, and the secret is unchanged
    with pytest.raises(HTTPException) as refused:
        await references_router.update_aws_secret_reference(
            role=svc_admin_role,
            session=session,
            secret_id=SecretUUID.new(reference.id),
            params=AwsSecretReferenceUpdate(
                store_id=aws_store.id, remote_reference="app/api"
            ),
        )
    assert refused.value.status_code == 422
    assert refused.value.detail == NOT_AWS_MESSAGE
    await session.refresh(reference)
    assert reference.source == SecretSource.BITWARDEN_SECRETS_MANAGER
    assert reference.store_id == store.id


def test_a_config_for_another_provider_is_refused() -> None:
    # A row whose provider and config disagree never reaches a backend
    row = OrganizationSecretStore(
        provider=SecretStoreProvider.AWS_SECRETS_MANAGER,
        config={
            "provider": BITWARDEN,
            "server": "eu",
            "organization_id": str(uuid.uuid4()),
        },
    )
    with pytest.raises(ValueError, match="not aws_secrets_manager"):
        parse_store_config(row)
