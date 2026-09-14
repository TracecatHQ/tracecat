"""DB-backed tests for organization secret stores and AWS-backed references."""

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Workspace
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.secrets.enums import AwsSecretMappingMode, SecretSource
from tracecat.secrets.schemas import (
    AwsSecretJsonField,
    AwsSecretKeyMapping,
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    SecretCreate,
    SecretKeyValue,
    SecretStoreCreate,
    SecretStoreUpdate,
    SecretUpdate,
)
from tracecat.secrets.service import SecretsService, build_aws_secret_reference
from tracecat.secrets.store_service import (
    SecretStoresService,
    generate_store_external_id,
)

pytestmark = pytest.mark.usefixtures("db")

ROLE_ARN = "arn:aws:iam::123456789012:role/tracecat-secrets-reader"
REGION = "us-east-1"
SECRET_ARN = f"arn:aws:secretsmanager:{REGION}:123456789012:secret:app/api-AbCdEf"
OTHER_REGION_ARN = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:app/api-AbCdEf"


@pytest.fixture
async def stores(session: AsyncSession, svc_admin_role: Role) -> SecretStoresService:
    return SecretStoresService(session=session, role=svc_admin_role)


@pytest.fixture
async def secrets(session: AsyncSession, svc_admin_role: Role) -> SecretsService:
    return SecretsService(session=session, role=svc_admin_role)


def whole_string_mapping(key: str = "API_TOKEN") -> AwsSecretKeyMapping:
    return AwsSecretKeyMapping(mode=AwsSecretMappingMode.WHOLE_STRING, keys=[key])


def reference_params(
    store_id: uuid.UUID, name: str = "aws_ref"
) -> AwsSecretReferenceCreate:
    return AwsSecretReferenceCreate(
        name=name,
        store_id=store_id,
        remote_reference=SECRET_ARN,
        key_mapping=whole_string_mapping(),
    )


def test_external_id_is_opaque_and_unique() -> None:
    first = generate_store_external_id()
    second = generate_store_external_id()
    assert first.startswith("tracecat-")
    assert first != second


@pytest.mark.anyio
async def test_create_store_persists_external_id_across_updates(
    stores: SecretStoresService,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    external_id = store.external_id
    assert external_id.startswith("tracecat-")

    await stores.update_store(
        store,
        SecretStoreUpdate(
            name="renamed",
            role_arn="arn:aws:iam::123456789012:role/other",
            enabled=False,
        ),
    )
    refreshed = await stores.get_store(store.id)
    assert refreshed.name == "renamed"
    assert refreshed.enabled is False
    assert refreshed.external_id == external_id


@pytest.mark.anyio
async def test_store_lookup_is_scoped_to_organization(
    stores: SecretStoresService,
) -> None:
    with pytest.raises(TracecatNotFoundError):
        await stores.get_store(uuid.uuid4())


@pytest.mark.anyio
async def test_reference_requires_workspace_authorization(
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    assert await secrets.list_authorized_stores() == []

    with pytest.raises(TracecatAuthorizationError):
        await secrets.create_aws_secret_reference(reference_params(store.id))

    await stores.authorize_workspace(store, svc_workspace.id)
    authorized = await secrets.list_authorized_stores()
    assert [s.id for s in authorized] == [store.id]

    created = await secrets.create_aws_secret_reference(reference_params(store.id))
    assert created.source == SecretSource.AWS_SECRETS_MANAGER
    assert created.store_id == store.id
    assert created.remote_reference == SECRET_ARN
    # Metadata path resolves key names without any remote access.
    assert secrets.secret_key_names(created) == ["API_TOKEN"]
    # No values are stored locally.
    assert secrets.decrypt_keys(created.encrypted_keys) == []


@pytest.mark.anyio
async def test_authorize_rejects_workspace_outside_organization(
    stores: SecretStoresService,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    with pytest.raises(TracecatNotFoundError):
        await stores.authorize_workspace(store, uuid.uuid4())


@pytest.mark.anyio
async def test_reference_region_must_match_store(
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    params = reference_params(store.id)
    params.remote_reference = OTHER_REGION_ARN
    with pytest.raises(ValueError, match="region"):
        await secrets.create_aws_secret_reference(params)


@pytest.mark.anyio
async def test_name_uniqueness_spans_local_and_aws_rows(
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    await secrets.create_secret(
        SecretCreate(
            name="shared", keys=[SecretKeyValue(key="K", value=SecretStr("v"))]
        )
    )
    with pytest.raises(IntegrityError):
        await secrets.create_aws_secret_reference(
            reference_params(store.id, name="shared")
        )
    await secrets.session.rollback()


@pytest.mark.anyio
async def test_aws_reference_rejects_local_value_updates(
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    created = await secrets.create_aws_secret_reference(reference_params(store.id))

    with pytest.raises(ValueError):
        await secrets.update_secret(
            created, SecretUpdate(keys=[SecretKeyValue(key="K", value=SecretStr("v"))])
        )

    await secrets.update_aws_secret_reference(
        created,
        AwsSecretReferenceUpdate(
            key_mapping=AwsSecretKeyMapping(
                mode=AwsSecretMappingMode.JSON,
                fields=[
                    AwsSecretJsonField(key="USER", field="username"),
                    AwsSecretJsonField(key="PASS", field="password"),
                ],
            )
        ),
    )
    refreshed = await secrets.get_secret(created.id)
    assert secrets.secret_key_names(refreshed) == ["USER", "PASS"]
    reference = build_aws_secret_reference(refreshed)
    assert reference.external_id == store.external_id
    assert reference.role_arn == ROLE_ARN
    assert reference.fetch_key == (ROLE_ARN, SECRET_ARN)


@pytest.mark.anyio
async def test_store_and_authorization_lifecycle_guards(
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    created = await secrets.create_aws_secret_reference(reference_params(store.id))

    with pytest.raises(TracecatConflictError):
        await stores.revoke_workspace(store, svc_workspace.id)
    with pytest.raises(TracecatConflictError):
        await stores.delete_store(store)

    # Deleting the alias only removes Tracecat metadata.
    await secrets.delete_secret(created)
    await stores.revoke_workspace(store, svc_workspace.id)
    assert await secrets.list_authorized_stores() == []
    await stores.delete_store(store)
    assert await stores.list_stores() == []
