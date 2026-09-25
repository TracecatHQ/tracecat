"""DB-backed tests for organization secret stores and AWS-backed references."""

import uuid
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.secrets.providers.aws_secrets_manager import generate_store_external_id
from tracecat_ee.secrets.references.service import SecretReferencesService
from tracecat_ee.secrets.stores.backends import parse_store_config
from tracecat_ee.secrets.stores.service import SecretStoresService

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import (
    Organization,
    OrganizationSecretStore,
    Secret,
    Workspace,
    WorkspaceSecretStoreAuthorization,
)
from tracecat.db.rls import set_rls_context
from tracecat.db.tenant_rls import enable_org_table_rls, enable_workspace_table_rls
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import PageParams
from tracecat.secrets.enums import AwsSecretMappingMode, SecretSource
from tracecat.secrets.schemas import (
    AwsSecretJsonField,
    AwsSecretKeyMapping,
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    AwsSecretsManagerStoreCreate,
    AwsSecretsManagerStoreUpdate,
    SecretCreate,
    SecretKeyValue,
    SecretStoreCreate,
    SecretStoreUpdate,
    SecretUpdate,
)
from tracecat.secrets.service import build_external_secret_reference

pytestmark = pytest.mark.usefixtures("db")

ROLE_ARN = "arn:aws:iam::123456789012:role/tracecat-secrets-reader"
REGION = "us-east-1"
SECRET_ARN = f"arn:aws:secretsmanager:{REGION}:123456789012:secret:app/api-AbCdEf"
OTHER_REGION_ARN = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:app/api-AbCdEf"


@pytest.fixture
async def stores(session: AsyncSession, svc_admin_role: Role) -> SecretStoresService:
    return SecretStoresService(session=session, role=svc_admin_role)


@pytest.fixture
async def secrets(
    session: AsyncSession, svc_admin_role: Role
) -> SecretReferencesService:
    return SecretReferencesService(session=session, role=svc_admin_role)


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
async def test_store_collections_paginate_without_skips(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", "test-pagination-secret")
    created = []
    for index in range(3):
        store = await stores.create_store(
            SecretStoreCreate(
                name=f"page-{index}",
                config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
            )
        )
        # Exercise the ID tie-breaker with identical timestamps.
        if created:
            store.created_at = created[0].created_at
        created.append(store)
        await stores.authorize_workspace(store, svc_workspace.id)

    for list_page in (stores.list_stores, secrets.list_authorized_stores):
        first = await list_page(PageParams(limit=1))
        assert len(first.items) == 1
        assert first.next_cursor is not None
        second = await list_page(PageParams(limit=1, cursor=first.next_cursor))
        assert second.prev_cursor is not None
        previous = await list_page(PageParams(limit=1, cursor=second.prev_cursor))
        assert [s.id for s in previous.items] == [s.id for s in first.items]
        assert second.next_cursor is not None
        last = await list_page(PageParams(limit=1, cursor=second.next_cursor))
        assert last.next_cursor is None
        assert {s.id for s in first.items + second.items + last.items} == {
            s.id for s in created
        }
        with pytest.raises(TracecatValidationError):
            await list_page(PageParams(limit=1, cursor="invalid"))


@pytest.mark.anyio
async def test_create_store_persists_external_id_across_updates(
    stores: SecretStoresService,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    external_id = parse_store_config(store).external_id
    assert external_id.startswith("tracecat-")

    await stores.update_store(
        store,
        SecretStoreUpdate(
            name="renamed",
            config=AwsSecretsManagerStoreUpdate(
                role_arn="arn:aws:iam::123456789012:role/other"
            ),
            enabled=False,
        ),
    )
    refreshed = await stores.get_store(store.id)
    assert refreshed.name == "renamed"
    assert refreshed.enabled is False
    refreshed_config = parse_store_config(refreshed)
    assert refreshed_config.external_id == external_id
    assert refreshed_config.role_arn == "arn:aws:iam::123456789012:role/other"


@pytest.mark.anyio
async def test_store_lookup_is_scoped_to_organization(
    stores: SecretStoresService,
) -> None:
    with pytest.raises(TracecatNotFoundError):
        await stores.get_store(uuid.uuid4())


@pytest.mark.anyio
async def test_reference_requires_workspace_authorization(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    assert (await secrets.list_authorized_stores(PageParams())).items == []

    with pytest.raises(TracecatAuthorizationError):
        await secrets.create_aws_secret_reference(reference_params(store.id))

    await stores.authorize_workspace(store, svc_workspace.id)
    authorized = await secrets.list_authorized_stores(PageParams())
    assert [s.id for s in authorized.items] == [store.id]

    created = await secrets.create_aws_secret_reference(reference_params(store.id))
    assert created.source == SecretSource.AWS_SECRETS_MANAGER
    assert created.store_id == store.id
    assert created.remote_reference == SECRET_ARN
    # Metadata path resolves key names without any remote access.
    assert secrets.secret_key_names(created) == ["API_TOKEN"]
    # No values are stored locally.
    assert secrets.decrypt_keys(created.encrypted_keys) == []


@pytest.mark.anyio
async def test_authorize_existing_workspace_preserves_authorization(
    stores: SecretStoresService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="idempotent-store",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    first = await stores.authorize_workspace(store, svc_workspace.id)
    repeated = await stores.authorize_workspace(store, svc_workspace.id)
    assert repeated.id == first.id
    assert repeated.created_at == first.created_at
    count = await stores.session.scalar(
        select(func.count())
        .select_from(WorkspaceSecretStoreAuthorization)
        .where(
            WorkspaceSecretStoreAuthorization.store_id == store.id,
            WorkspaceSecretStoreAuthorization.workspace_id == svc_workspace.id,
        )
    )
    assert count == 1


@pytest.mark.anyio
async def test_authorize_rejects_workspace_outside_organization(
    stores: SecretStoresService,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    with pytest.raises(TracecatNotFoundError):
        await stores.authorize_workspace(store, uuid.uuid4())


@pytest.mark.anyio
async def test_reference_region_must_match_store(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    params = reference_params(store.id)
    params.remote_reference = OTHER_REGION_ARN
    with pytest.raises(ValueError, match="region"):
        await secrets.create_aws_secret_reference(params)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("remote_reference", "region_change_allowed"),
    [(SECRET_ARN, False), ("app/api", True)],
)
async def test_region_change_rejected_while_arn_references_exist(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
    remote_reference: str,
    region_change_allowed: bool,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    params = reference_params(store.id)
    params.remote_reference = remote_reference
    await secrets.create_aws_secret_reference(params)

    # Role-only changes never strand references.
    new_role = "arn:aws:iam::123456789012:role/other-reader"
    await stores.update_store(
        store,
        SecretStoreUpdate(config=AwsSecretsManagerStoreUpdate(role_arn=new_role)),
    )
    region_update = SecretStoreUpdate(
        config=AwsSecretsManagerStoreUpdate(region="eu-west-1")
    )
    if region_change_allowed:
        await stores.update_store(store, region_update)
        assert parse_store_config(store).region == "eu-west-1"
    else:
        with pytest.raises(TracecatConflictError, match="by ARN"):
            await stores.update_store(store, region_update)
        assert parse_store_config(store).region == REGION


@pytest.mark.anyio
async def test_name_uniqueness_spans_local_and_aws_rows(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
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
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    created = await secrets.create_aws_secret_reference(reference_params(store.id))

    with pytest.raises(ValueError):
        await secrets.update_secret(
            created, SecretUpdate(keys=[SecretKeyValue(key="K", value=SecretStr("v"))])
        )
    with pytest.raises(ValueError):
        await secrets.update_secret(created, SecretUpdate(name="api-key"))

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
    reference = build_external_secret_reference(refreshed)
    assert reference.store_config.external_id == parse_store_config(store).external_id
    assert reference.store_config.role_arn == ROLE_ARN
    assert reference.fetch_key == (store.id, SECRET_ARN)


@pytest.mark.anyio
async def test_store_and_authorization_lifecycle_guards(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
    session: AsyncSession,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
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
    assert (await secrets.list_authorized_stores(PageParams())).items == []
    await stores.delete_store(store)
    assert (await stores.list_stores(PageParams())).items == []


@pytest.mark.anyio
@pytest.mark.parametrize("loaded", [False, True])
async def test_delete_preserves_references_missed_by_preflight(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    loaded: bool,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="delete-conflict",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    reference = await secrets.create_aws_secret_reference(reference_params(store.id))
    store_id, secret_id = store.id, reference.id
    if loaded:
        await session.refresh(store, attribute_names=["secrets"])
    # Model a reference committed after the count, before the ORM delete.
    monkeypatch.setattr(stores, "count_references", AsyncMock(return_value={}))
    with pytest.raises(TracecatConflictError):
        await stores.delete_store(store)
    assert (await secrets.get_secret(secret_id)).store_id == store_id
    assert (await stores.get_store(store_id)).id == store_id


@pytest.mark.anyio
async def test_reference_guards_with_enforced_rls_and_org_only_context(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
    session: AsyncSession,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="rls-store",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
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
        config={
            "provider": "aws_secrets_manager",
            "role_arn": ROLE_ARN,
            "region": REGION,
            "external_id": "foreign-external-id",
        },
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
    # PostgreSQL requires UPDATE privilege for the FOR SHARE scope lock.
    await session.execute(
        text(f'GRANT UPDATE ON organization_secret_store TO "{reader}"')
    )
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


@pytest.mark.anyio
async def test_store_config_round_trips_and_rejects_unknown_provider(
    stores: SecretStoresService,
) -> None:
    with pytest.raises(TracecatValidationError):
        await stores.create_store(
            SecretStoreCreate.model_construct(
                name="unknown",
                description=None,
                provider="hashicorp_vault",
                config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
                enabled=True,
            )
        )

    store = await stores.create_store(
        SecretStoreCreate(
            name="round-trip",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    config = parse_store_config(store)
    assert config.role_arn == ROLE_ARN
    assert config.region == REGION
    assert config.external_id.startswith("tracecat-")


@pytest.mark.anyio
async def test_org_wide_access_includes_future_workspaces_and_preserves_bindings(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    svc_workspace: Workspace,
    session: AsyncSession,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="org-wide",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
            all_workspaces=True,
        )
    )
    assert [
        s.id for s in (await secrets.list_authorized_stores(PageParams())).items
    ] == [store.id]
    # New workspaces inherit access without a grant backfill or creation hook.
    future_workspace = Workspace(
        name="future-workspace", organization_id=svc_workspace.organization_id
    )
    session.add(future_workspace)
    await session.commit()
    # Exercise lazy authorization inserts under the production tenant policies.
    reader = f"org_store_user_{uuid.uuid4().hex}"
    await session.execute(text(f'CREATE ROLE "{reader}"'))
    await session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{reader}"'))
    await session.execute(
        text(
            f'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO "{reader}"'
        )
    )
    await session.execute(
        text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "{reader}"')
    )
    for table in ("organization_secret_store", "workspace_secret_store_authorization"):
        for statement in enable_org_table_rls(table).split(";"):
            if statement.strip():
                await session.execute(text(statement))
    for statement in enable_workspace_table_rls("secret").split(";"):
        if statement.strip():
            await session.execute(text(statement))
    await session.execute(text(f'SET LOCAL ROLE "{reader}"'))
    await set_rls_context(
        session, org_id=stores.organization_id, workspace_id=svc_workspace.id
    )
    future_secrets = SecretReferencesService(
        session,
        role=secrets.role.model_copy(update={"workspace_id": future_workspace.id}),
    )
    assert [
        s.id for s in (await future_secrets.list_authorized_stores(PageParams())).items
    ] == [store.id]
    for name in ("first_ref", "second_ref"):
        await secrets.create_aws_secret_reference(reference_params(store.id, name))
    grants = (
        await session.scalars(
            select(WorkspaceSecretStoreAuthorization).where(
                WorkspaceSecretStoreAuthorization.store_id == store.id
            )
        )
    ).all()
    assert [grant.workspace_id for grant in grants] == [svc_workspace.id]
    with pytest.raises(TracecatConflictError, match="Turn off all-workspace"):
        await stores.revoke_workspace(store, svc_workspace.id)

    # Disabling the store remains independent of workspace access.
    await stores.update_store(store, SecretStoreUpdate(enabled=False))
    assert store.all_workspaces is True
    reference = await secrets.get_secret_by_name("first_ref")
    assert reference is not None
    await session.refresh(reference, attribute_names=["store"])
    assert build_external_secret_reference(reference).store_enabled is False

    # Returning to selected access keeps existing references, but not unused access.
    await stores.update_store(store, SecretStoreUpdate(all_workspaces=False))
    assert [
        s.id for s in (await secrets.list_authorized_stores(PageParams())).items
    ] == [store.id]
    assert (await future_secrets.list_authorized_stores(PageParams())).items == []
    with pytest.raises(TracecatAuthorizationError):
        await future_secrets.create_aws_secret_reference(reference_params(store.id))
    with pytest.raises(TracecatConflictError, match="still has 2 secret"):
        await stores.revoke_workspace(store, svc_workspace.id)


@pytest.mark.anyio
async def test_org_wide_access_never_crosses_organization_boundaries(
    stores: SecretStoresService,
    secrets: SecretReferencesService,
    session: AsyncSession,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(
            name="org-wide",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
            all_workspaces=True,
        )
    )
    other_org = Organization(name="other-org", slug=f"other-{uuid.uuid4().hex}")
    session.add(other_org)
    await session.flush()
    other_workspace = Workspace(name="other-workspace", organization_id=other_org.id)
    session.add(other_workspace)
    await session.commit()
    for organization_id in (other_org.id, stores.organization_id):
        # Also reject a mismatched org/workspace pair without relying on RLS.
        foreign_secrets = SecretReferencesService(
            session,
            role=secrets.role.model_copy(
                update={
                    "organization_id": organization_id,
                    "workspace_id": other_workspace.id,
                }
            ),
        )
        assert (await foreign_secrets.list_authorized_stores(PageParams())).items == []
        with pytest.raises(TracecatAuthorizationError):
            await foreign_secrets.create_aws_secret_reference(
                reference_params(store.id)
            )


@pytest.mark.anyio
@pytest.mark.parametrize("reference_first", [True, False])
async def test_org_wide_scope_change_serializes_with_reference_creation(
    svc_admin_role: Role,
    reference_first: bool,
) -> None:
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.database import TEST_DB_CONFIG

    # Independent committed transactions exercise the production READ COMMITTED
    # race, rather than the service fixture's enclosing SERIALIZABLE transaction.
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as first:
            org = Organization(name="race-org", slug=f"race-{uuid.uuid4().hex}")
            first.add(org)
            await first.flush()
            workspace = Workspace(name="race-workspace", organization_id=org.id)
            first.add(workspace)
            await first.commit()
            role = svc_admin_role.model_copy(
                update={"organization_id": org.id, "workspace_id": workspace.id}
            )
            stores = SecretStoresService(first, role=role)
            store = await stores.create_store(
                SecretStoreCreate(
                    name="race-store",
                    config=AwsSecretsManagerStoreCreate(
                        role_arn=ROLE_ARN, region=REGION
                    ),
                    all_workspaces=True,
                )
            )
            references = SecretReferencesService(first, role=role)
            async with AsyncSession(engine, expire_on_commit=False) as second:
                other_stores = SecretStoresService(second, role=role)
                other_references = SecretReferencesService(second, role=role)
                other_store = await other_stores.get_store(store.id)
                if reference_first:
                    await references._get_authorized_store(store.id)
                else:
                    store.all_workspaces = False
                    await first.flush()
                await second.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    if reference_first:
                        await other_stores.update_store(
                            other_store, SecretStoreUpdate(all_workspaces=False)
                        )
                    else:
                        await other_references.create_aws_secret_reference(
                            reference_params(store.id)
                        )
                await second.rollback()
                if reference_first:
                    await references.create_aws_secret_reference(
                        reference_params(store.id)
                    )
                    other_store = await other_stores.get_store(store.id)
                    await other_stores.update_store(
                        other_store, SecretStoreUpdate(all_workspaces=False)
                    )
                    assert [
                        s.id
                        for s in (
                            await other_references.list_authorized_stores(PageParams())
                        ).items
                    ] == [store.id]
                else:
                    await first.commit()
                    with pytest.raises(TracecatAuthorizationError):
                        await other_references.create_aws_secret_reference(
                            reference_params(store.id)
                        )
    finally:
        await engine.dispose()
