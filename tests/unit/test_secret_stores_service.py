"""DB-backed tests for organization secret stores and AWS-backed references."""

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    Organization,
    OrganizationSecretStore,
    Secret,
    Workspace,
    WorkspaceSecretStoreAuthorization,
)
from tracecat.db.rls import set_rls_context
from tracecat.db.tenant_rls import enable_workspace_table_rls
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
    assert reference.fetch_key == (store.id, SECRET_ARN)


@pytest.mark.anyio
async def test_store_and_authorization_lifecycle_guards(
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
    session: AsyncSession,
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

    # The DB refuses to drop an authorization a reference still depends on,
    # so a revoke racing a concurrent create cannot leave the reference usable.
    with pytest.raises(IntegrityError, match="fk_secret_store_authorization"):
        async with session.begin_nested():
            await session.execute(
                delete(WorkspaceSecretStoreAuthorization).where(
                    WorkspaceSecretStoreAuthorization.store_id == store.id
                )
            )

    # Deleting the alias only removes Tracecat metadata.
    await secrets.delete_secret(created)
    await stores.revoke_workspace(store, svc_workspace.id)
    assert await secrets.list_authorized_stores() == []
    await stores.delete_store(store)
    assert await stores.list_stores() == []


@pytest.mark.anyio
async def test_reference_guards_with_enforced_rls_and_org_only_context(
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
    session: AsyncSession,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="rls-store", role_arn=ROLE_ARN, region=REGION)
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    await secrets.create_aws_secret_reference(reference_params(store.id))
    other_workspace = Workspace(
        name="other-workspace", organization_id=svc_workspace.organization_id
    )
    other_org = Organization(name="other-org", slug=f"other-{uuid.uuid4().hex}")
    session.add_all([other_workspace, other_org])
    await session.flush()
    foreign_workspace = Workspace(
        name="foreign-workspace", organization_id=other_org.id
    )
    foreign_store = OrganizationSecretStore(
        organization_id=other_org.id,
        name="foreign-store",
        role_arn=ROLE_ARN,
        region=REGION,
        external_id="foreign-external-id",
    )
    session.add_all([foreign_workspace, foreign_store])
    await session.flush()
    for workspace, target_store in (
        (other_workspace, store),
        (foreign_workspace, foreign_store),
    ):
        session.add(
            WorkspaceSecretStoreAuthorization(
                organization_id=workspace.organization_id,
                workspace_id=workspace.id,
                store_id=target_store.id,
            )
        )
        await session.flush()
        session.add(
            Secret(
                workspace_id=workspace.id,
                name="other-ref",
                source=SecretSource.AWS_SECRETS_MANAGER,
                store_id=target_store.id,
                encrypted_keys=secrets.encrypt_keys([]),
                remote_reference=SECRET_ARN,
                remote_key_mapping=whole_string_mapping().model_dump(mode="json"),
            )
        )
    await session.flush()

    # A non-owner role ensures PostgreSQL actually applies the workspace policy.
    reader = f"secret_store_reader_{uuid.uuid4().hex}"
    await session.execute(text(f'CREATE ROLE "{reader}"'))
    await session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{reader}"'))
    await session.execute(
        text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{reader}"')
    )
    for statement in enable_workspace_table_rls("secret").split(";"):
        if statement.strip():
            await session.execute(text(statement))
    await session.execute(text(f'SET LOCAL ROLE "{reader}"'))
    org_role = stores.role.model_copy(update={"workspace_id": None})
    org_stores = SecretStoresService(session, role=org_role)
    try:
        await set_rls_context(session, org_id=stores.organization_id, workspace_id=None)
        assert (await session.execute(select(func.count(Secret.id)))).scalar_one() == 0
        assert await org_stores.count_references([store.id, foreign_store.id]) == {
            store.id: 2
        }
        with pytest.raises(TracecatConflictError) as revoked:
            await org_stores.revoke_workspace(store, svc_workspace.id)
        assert revoked.value.detail == {"reference_count": 1}
        with pytest.raises(TracecatConflictError) as deleted:
            await org_stores.delete_store(store)
        assert deleted.value.detail == {"reference_count": 2}
        # The privileged read must not change the request session's RLS context.
        assert (await session.execute(select(func.count(Secret.id)))).scalar_one() == 0
    finally:
        await session.execute(text("RESET ROLE"))
