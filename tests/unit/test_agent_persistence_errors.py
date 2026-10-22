"""Agent persistence boundaries classify only known database constraints."""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import cast, get_args
from unittest.mock import AsyncMock

import pytest
from asyncpg import (
    ForeignKeyViolationError,
    NotNullViolationError,
    UniqueViolationError,
)
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.agent import router as agent_router
from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.channels.schemas import (
    AgentChannelTokenCreate,
    AgentChannelTokenUpdate,
    ChannelType,
    SlackChannelTokenConfig,
)
from tracecat.agent.channels.service import AgentChannelService
from tracecat.agent.folders.service import AgentFolderService
from tracecat.agent.preset import internal_router as internal_preset_router
from tracecat.agent.preset import router as preset_router
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.session import router as session_router
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.skill.folders.service import SkillFolderService
from tracecat.agent.skill.tags.service import SkillTagsService
from tracecat.agent.tags.service import AgentTagsService
from tracecat.api.common import generic_exception_handler
from tracecat.auth.dependencies import (
    ExecutorWorkspaceRole,
    OrgUserRole,
    WorkspaceActorRouteRole,
)
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
from tracecat.db.models import (
    AgentCatalog,
    AgentChannelToken,
    AgentFolder,
    AgentModelAccess,
    AgentPreset,
    AgentTag,
    Skill,
    SkillFolder,
    SkillTag,
    Workspace,
)
from tracecat.db.types import PostgresConstraintError
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)


def unique_error(constraint: str) -> IntegrityError:
    error = UniqueViolationError("synthetic-diagnostic")
    cast(PostgresConstraintError, error).constraint_name = constraint
    return IntegrityError("synthetic statement", {}, error)


@pytest.fixture
def api() -> Iterator[TestClient]:
    role = Role(
        type="user",
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )
    app = FastAPI()
    for router in (
        agent_router.router,
        preset_router.router,
        internal_preset_router.router,
        session_router.router,
    ):
        app.include_router(router)
    app.add_exception_handler(Exception, generic_exception_handler)
    for annotation in (OrgUserRole, WorkspaceActorRouteRole, ExecutorWorkspaceRole):
        app.dependency_overrides[get_args(annotation)[1].dependency] = lambda: role
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize(
    ("error", "status"),
    [
        (unique_error("synthetic_unrelated_constraint"), 500),
        (TracecatValidationError("Invalid credentials"), 400),
        (TracecatConflictError("Credentials already exist"), 409),
    ],
)
def test_credentials_map_only_domain_errors(
    api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    error: Exception,
    status: int,
) -> None:
    operation = "create" if method == "POST" else "update"
    monkeypatch.setattr(
        AgentManagementService,
        f"{operation}_provider_credentials",
        AsyncMock(side_effect=error),
    )
    path = "/agent/credentials" if method == "POST" else "/agent/credentials/synthetic"
    body = (
        {"provider": "synthetic", "credentials": {}}
        if method == "POST"
        else {"credentials": {}}
    )
    response = api.request(method, path, json=body)
    assert response.status_code == status
    assert "synthetic-diagnostic" not in response.text


def test_credentials_update_preserves_not_found(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        AgentManagementService,
        "update_provider_credentials",
        AsyncMock(side_effect=TracecatNotFoundError("Credentials not found")),
    )
    response = api.put("/agent/credentials/synthetic", json={"credentials": {}})
    assert response.status_code == 404


@pytest.mark.parametrize("internal", [False, True])
@pytest.mark.parametrize("update", [False, True])
@pytest.mark.parametrize("known", [False, True])
def test_preset_routes_preserve_slug_conflict_contract(
    api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    internal: bool,
    update: bool,
    known: bool,
) -> None:
    error = (
        TracecatValidationError(
            "Agent preset slug is already in use for this workspace"
        )
        if known
        else unique_error("synthetic_unrelated_constraint")
    )
    monkeypatch.setattr(
        AgentPresetService,
        "update_preset" if update else "create_preset",
        AsyncMock(side_effect=error),
    )
    preset_id = uuid.uuid4()
    monkeypatch.setattr(
        AgentPresetService,
        "get_preset",
        AsyncMock(return_value=AgentPreset(id=preset_id)),
    )
    if internal:
        monkeypatch.setattr(
            internal_preset_router,
            "_create_payload_with_default_model",
            AsyncMock(
                return_value={
                    "name": "Synthetic",
                    "slug": "synthetic",
                    "model_name": "synthetic-model",
                    "model_provider": "openai",
                }
            ),
        )
        monkeypatch.setattr(
            AgentPresetService,
            "get_preset_by_slug",
            AsyncMock(return_value=AsyncMock(id=preset_id)),
        )
        path = (
            "/internal/agent/presets/by-slug/synthetic"
            if update
            else "/internal/agent/presets"
        )
    else:
        path = f"/agent/presets/{preset_id}" if update else "/agent/presets"
    response = api.request(
        "PATCH" if update else "POST",
        path,
        json={
            "name": "Synthetic",
            "slug": "synthetic",
            "model_name": "synthetic-model",
            "model_provider": "openai",
        },
    )
    assert response.status_code == (400 if known else 500)
    assert "synthetic-diagnostic" not in response.text


def test_internal_preset_update_preserves_concurrent_not_found(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        AgentPresetService,
        "get_preset_by_slug",
        AsyncMock(return_value=AsyncMock(id=uuid.uuid4())),
    )
    monkeypatch.setattr(
        AgentPresetService,
        "update_preset",
        AsyncMock(side_effect=TracecatNotFoundError("Agent preset not found")),
    )
    response = api.patch(
        "/internal/agent/presets/by-slug/synthetic", json={"name": "Synthetic"}
    )
    assert response.status_code == 404


@pytest.mark.parametrize("known", [False, True])
def test_session_route_conflict_and_unexpected_error(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, known: bool
) -> None:
    monkeypatch.setattr(
        session_router, "require_workspace_chat_entitlement_for_entity", AsyncMock()
    )
    monkeypatch.setattr(
        AgentSessionService,
        "create_session",
        AsyncMock(
            side_effect=TracecatConflictError("Agent session already exists")
            if known
            else unique_error("synthetic_unrelated_constraint")
        ),
    )
    response = api.post(
        "/agent/sessions",
        json={
            "id": str(uuid.uuid4()),
            "entity_type": "workflow",
            "entity_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == (409 if known else 500)
    assert "synthetic-diagnostic" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("update", [False, True])
@pytest.mark.parametrize("known", [False, True])
async def test_preset_service_rolls_back_only_named_slug_conflict(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch, update: bool, known: bool
) -> None:
    session = AsyncMock()
    service = AgentPresetService(session, role=svc_role)
    error = unique_error(
        "uq_agent_preset_workspace_slug_active"
        if known
        else "synthetic_unrelated_constraint"
    )
    monkeypatch.setattr(
        service,
        "_update_preset" if update else "_create_preset",
        AsyncMock(side_effect=error),
    )
    with pytest.raises(TracecatValidationError if known else IntegrityError):
        if update:
            await service.update_preset(
                AgentPreset(id=uuid.uuid4()), AgentPresetUpdate(slug="synthetic")
            )
        else:
            await service.create_preset(
                AgentPresetCreate(
                    name="Synthetic", model_name="synthetic", model_provider="openai"
                )
            )
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["agent", "skill"])
@pytest.mark.parametrize("known", [False, True])
async def test_tag_commit_classifies_only_named_conflicts(
    svc_role: Role, kind: str, known: bool
) -> None:
    session = AsyncMock()
    session.commit.side_effect = unique_error(
        f"uq_{kind}_tag_name_workspace" if known else "synthetic_unrelated_constraint"
    )
    service = (
        AgentTagsService(session, role=svc_role)
        if kind == "agent"
        else SkillTagsService(session, role=svc_role)
    )
    with pytest.raises(TracecatConflictError if known else IntegrityError):
        await service._commit_tag_definition_change("Tag already exists")
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["agent", "skill"])
@pytest.mark.parametrize("phase", ["commit", "descendants"])
@pytest.mark.parametrize("known", [False, True])
async def test_folder_write_handles_autoflush_and_commit_conflicts(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch, kind: str, phase: str, known: bool
) -> None:
    session = AsyncMock()
    error = unique_error(
        f"uq_{kind}_folder_path_workspace"
        if known
        else "synthetic_unrelated_constraint"
    )
    service = (
        AgentFolderService(session, role=svc_role)
        if kind == "agent"
        else SkillFolderService(session, role=svc_role)
    )
    descendants = AsyncMock(side_effect=error if phase == "descendants" else None)
    monkeypatch.setattr(service, "_update_descendant_paths", descendants)
    if phase == "commit":
        session.commit.side_effect = error
    with pytest.raises(TracecatValidationError if known else IntegrityError):
        await service._write_folder_change(
            conflict_path="/synthetic/", commit=True, old_path="/old/"
        )
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("known", [False, True])
async def test_catalog_commit_classifies_only_named_conflict(
    svc_role: Role, known: bool
) -> None:
    session = AsyncMock()
    session.commit.side_effect = unique_error(
        "uq_agent_catalog_custom_provider_model_provider_model_name"
        if known
        else "synthetic_unrelated_constraint"
    )
    with pytest.raises(TracecatValidationError if known else IntegrityError):
        token = ctx_role.set(svc_role)
        try:
            await AgentCatalogService(session).create_catalog_entry(
                org_id=svc_role.organization_id,
                model_provider="synthetic-provider",
                model_name="synthetic-model",
            )
        finally:
            ctx_role.reset(token)
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("update", [False, True])
@pytest.mark.parametrize("known", [False, True])
async def test_channel_commit_classifies_only_active_token_conflict(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch, update: bool, known: bool
) -> None:
    session = AsyncMock()
    session.commit.side_effect = unique_error(
        "ix_agent_channel_token_agent_preset_id_channel_type_active"
        if known
        else "synthetic_unrelated_constraint"
    )
    service = AgentChannelService(session, role=svc_role)
    monkeypatch.setattr(service, "_require_workspace_preset", AsyncMock())
    token_config = SlackChannelTokenConfig(
        slack_bot_token="synthetic-token",
        slack_client_id="synthetic-client",
        slack_client_secret="synthetic-secret",
        slack_signing_secret="synthetic-signing",
    )
    monkeypatch.setattr(
        service, "serialize_channel_config_for_storage", lambda **kwargs: {}
    )
    monkeypatch.setattr(
        service, "parse_stored_channel_config", lambda **kwargs: token_config
    )
    with pytest.raises(TracecatValidationError if known else IntegrityError):
        if update:
            token = AgentChannelToken(
                workspace_id=svc_role.workspace_id,
                agent_preset_id=uuid.uuid4(),
                channel_type="slack",
                is_active=True,
                config={},
            )
            await service.update_token(token, AgentChannelTokenUpdate(is_active=True))
        else:
            await service.create_token(
                AgentChannelTokenCreate(
                    agent_preset_id=uuid.uuid4(),
                    channel_type=ChannelType.SLACK,
                    config=token_config,
                )
            )
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "constraint",
    [
        "uq_agent_model_access_organization_workspace_catalog",
        "fk_agent_model_access_catalog_id_agent_catalog",
        "fk_agent_model_access_workspace_id_workspace",
        "fk_synthetic_unrelated",
        "synthetic_unrelated_constraint",
    ],
)
async def test_access_classifies_only_expected_constraints(
    svc_role: Role, constraint: str
) -> None:
    session = AsyncMock()
    session.scalar.return_value = True
    if constraint.startswith("fk_"):
        cause = ForeignKeyViolationError("synthetic-diagnostic")
        cast(PostgresConstraintError, cause).constraint_name = constraint
        error = IntegrityError("synthetic statement", {}, cause)
        expected = (
            IntegrityError
            if constraint == "fk_synthetic_unrelated"
            else TracecatNotFoundError
        )
    elif constraint.startswith("uq_"):
        error = unique_error(constraint)
        expected = TracecatValidationError
    else:
        error = IntegrityError(
            "synthetic statement", {}, NotNullViolationError("synthetic-diagnostic")
        )
        expected = IntegrityError
    session.commit.side_effect = error
    with pytest.raises(expected):
        await AgentModelAccessService(session, role=svc_role).enable_model(
            catalog_id=uuid.uuid4(), workspace_id=None
        )
    session.rollback.assert_awaited_once()


@pytest.fixture
async def independent_sessions(
    svc_role: Role,
) -> AsyncGenerator[tuple[Role, async_sessionmaker[AsyncSession]], None]:
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    role = svc_role.model_copy(
        update={"workspace_id": uuid.uuid4(), "user_id": None}, deep=True
    )
    async with factory() as session:
        session.add(
            Workspace(
                id=role.workspace_id,
                organization_id=role.organization_id,
                name="synthetic-concurrency",
            )
        )
        await session.commit()
    try:
        yield role, factory
    finally:
        async with factory() as session:
            await session.execute(
                delete(Workspace).where(Workspace.id == role.workspace_id)
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("update", [False, True])
async def test_concurrent_preset_slug_writes_get_validation_error(
    independent_sessions: tuple[Role, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    update: bool,
) -> None:
    role, factory = independent_sessions
    ids: list[uuid.UUID] = []
    if update:
        async with factory() as session:
            service = AgentPresetService(session, role=role)
            for index in range(2):
                preset = await service.create_preset(
                    AgentPresetCreate(
                        name=f"Synthetic {index}",
                        slug=f"synthetic-{index}",
                        model_name="synthetic-model",
                        model_provider="openai",
                    )
                )
                ids.append(preset.id)
    barrier = asyncio.Barrier(2)
    original = AgentPresetService._normalize_and_validate_slug

    async def validate(self: AgentPresetService, *args, **kwargs) -> str:
        slug = await original(self, *args, **kwargs)
        await barrier.wait()
        return slug

    monkeypatch.setattr(AgentPresetService, "_normalize_and_validate_slug", validate)

    async def write(index: int) -> object:
        async with factory() as session:
            service = AgentPresetService(session, role=role)
            if update:
                preset = await service.get_preset(ids[index])
                assert preset is not None
                return await service.update_preset(
                    preset, AgentPresetUpdate(slug="contested-synthetic")
                )
            return await service.create_preset(
                AgentPresetCreate(
                    name="Synthetic",
                    slug="contested-synthetic",
                    model_name="synthetic-model",
                    model_provider="openai",
                )
            )

    results = await asyncio.wait_for(
        asyncio.gather(write(0), write(1), return_exceptions=True), timeout=30
    )
    assert (
        sum(isinstance(result, TracecatValidationError) for result in results) == 1
    ), results
    assert sum(not isinstance(result, BaseException) for result in results) == 1


@pytest.mark.anyio
async def test_concurrent_default_model_first_write_is_atomic(
    independent_sessions: tuple[Role, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role, factory = independent_sessions
    ids: list[uuid.UUID] = []
    async with factory() as session:
        for _index in range(2):
            row = AgentCatalog(
                organization_id=role.organization_id,
                model_provider="synthetic-provider",
                model_name=f"synthetic-model-{uuid.uuid4().hex}",
                model_metadata={},
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
            session.add(
                AgentModelAccess(
                    organization_id=role.organization_id,
                    workspace_id=None,
                    catalog_id=row.id,
                )
            )
        await session.commit()
    barrier = asyncio.Barrier(2)
    original = AgentManagementService._stage_org_setting_value

    async def stage(self: AgentManagementService, *, key: str, value: str) -> None:
        if key == "agent_default_model":
            await barrier.wait()
        await original(self, key=key, value=value)

    monkeypatch.setattr(AgentManagementService, "_stage_org_setting_value", stage)

    async def write(catalog_id: uuid.UUID) -> object:
        async with factory() as session:
            return await AgentManagementService(session, role=role).set_default_model(
                catalog_id
            )

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(write(catalog_id) for catalog_id in ids)), timeout=30
        )
        assert len(results) == 2
        async with factory() as session:
            service = AgentManagementService(session, role=role)
            default_id = await service.settings_service.get_org_setting(
                "agent_default_model_catalog_id"
            )
            assert default_id is not None
            stored_id = uuid.UUID(service.settings_service.get_value(default_id))
            stored_model = await session.scalar(
                select(AgentCatalog.model_name).where(AgentCatalog.id == stored_id)
            )
            assert stored_id in ids
            assert await service.get_default_model() == stored_model
    finally:
        async with factory() as session:
            await session.execute(delete(AgentCatalog).where(AgentCatalog.id.in_(ids)))
            await session.commit()


@pytest.mark.anyio
async def test_duplicate_session_id_real_postgres_rollback(
    session: AsyncSession, svc_role: Role
) -> None:
    service = AgentSessionService(
        session, role=svc_role.model_copy(update={"user_id": None})
    )
    params = AgentSessionCreate(
        id=uuid.uuid4(),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
        tools=["synthetic-tool"],
    )
    await service.create_session(params)
    with pytest.raises(TracecatConflictError, match="Agent session already exists"):
        await service.create_session(params)
    assert (
        await session.scalar(
            select(Workspace.id).where(Workspace.id == svc_role.workspace_id)
        )
        == svc_role.workspace_id
    )


@pytest.mark.anyio
async def test_session_unknown_integrity_rolls_back(svc_role: Role) -> None:
    session = AsyncMock()
    error = unique_error("synthetic_unrelated_constraint")
    session.commit.side_effect = error
    service = AgentSessionService(session, role=svc_role)
    with pytest.raises(IntegrityError) as raised:
        await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKFLOW,
                entity_id=uuid.uuid4(),
                tools=["synthetic-tool"],
            )
        )
    assert raised.value is error
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("at_commit", [False, True])
async def test_default_model_unknown_database_error_rolls_back(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch, at_commit: bool
) -> None:
    session = AsyncMock()
    session.scalar.return_value = AgentCatalogRead(
        id=uuid.uuid4(),
        organization_id=svc_role.organization_id,
        custom_provider_id=None,
        model_name="synthetic-model",
        model_provider="synthetic-provider",
        model_metadata=None,
    )
    service = AgentManagementService(session, role=svc_role)
    error = unique_error("synthetic_unrelated_constraint")
    if at_commit:
        session.commit.side_effect = error
    else:
        session.execute.side_effect = error
    with pytest.raises(IntegrityError) as raised:
        await service.set_default_model(uuid.uuid4())
    assert raised.value is error
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_inactive_channel_token_locks_preset(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AgentChannelService(AsyncMock(), role=svc_role)
    require = AsyncMock()
    monkeypatch.setattr(service, "_require_workspace_preset", require)
    monkeypatch.setattr(
        service, "serialize_channel_config_for_storage", lambda **kwargs: {}
    )
    preset_id = uuid.uuid4()
    await service.create_token(
        AgentChannelTokenCreate(
            agent_preset_id=preset_id,
            is_active=False,
            channel_type=ChannelType.SLACK,
            config=SlackChannelTokenConfig(
                slack_bot_token="synthetic-value",
                slack_signing_secret="synthetic-value",
            ),
        )
    )
    require.assert_awaited_once_with(preset_id, lock=True)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["agent", "skill"])
async def test_folder_subtree_conflicts_roll_back(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    session = AsyncMock()
    service = (
        AgentFolderService(session, role=svc_role)
        if kind == "agent"
        else SkillFolderService(session, role=svc_role)
    )
    constraint = (
        "uq_agent_folder_path_workspace"
        if kind == "agent"
        else "uq_skill_folder_path_workspace"
    )
    monkeypatch.setattr(
        service,
        "_update_descendant_paths",
        AsyncMock(side_effect=unique_error(constraint)),
    )
    with pytest.raises(TracecatValidationError):
        await service._write_folder_change(
            old_path="/synthetic-old", conflict_path="/synthetic-new", commit=True
        )
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["agent", "skill"])
@pytest.mark.parametrize("found", [False, True])
async def test_tag_association_locks_scoped_parents(
    svc_role: Role, kind: str, found: bool
) -> None:
    session = AsyncMock()
    session.scalar.return_value = uuid.uuid4() if found else None
    service = (
        AgentTagsService(session, role=svc_role)
        if kind == "agent"
        else SkillTagsService(session, role=svc_role)
    )
    if found:
        if isinstance(service, AgentTagsService):
            await service._require_preset_and_tag_in_workspace(
                uuid.uuid4(), uuid.uuid4()
            )
        else:
            await service._require_skill_and_tag_in_workspace(
                uuid.uuid4(), uuid.uuid4()
            )
    else:
        with pytest.raises(TracecatNotFoundError):
            if isinstance(service, AgentTagsService):
                await service._require_preset_and_tag_in_workspace(
                    uuid.uuid4(), uuid.uuid4()
                )
            else:
                await service._require_skill_and_tag_in_workspace(
                    uuid.uuid4(), uuid.uuid4()
                )
    statement = session.scalar.call_args.args[0]
    sql = str(statement.compile(dialect=dialect()))
    assert "FOR SHARE OF" in sql
    assert "workspace_id" in sql
    assert "deleted_at IS NULL" in sql


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["agent", "skill"])
async def test_move_to_concurrently_deleted_folder_is_not_found(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    session = AsyncMock()
    model = AgentPreset if kind == "agent" else Skill
    result = session.execute.return_value
    result.scalar_one_or_none = lambda: model(
        id=uuid.uuid4(), workspace_id=svc_role.workspace_id
    )
    session.scalar.return_value = None
    service = (
        AgentFolderService(session, role=svc_role)
        if kind == "agent"
        else SkillFolderService(session, role=svc_role)
    )
    monkeypatch.setattr(service, "has_entitlement", AsyncMock(return_value=True))
    with pytest.raises(TracecatNotFoundError):
        if isinstance(service, AgentFolderService):
            await service.move_preset(uuid.uuid4(), AgentFolder(id=uuid.uuid4()))
        else:
            await service.move_skill(uuid.uuid4(), SkillFolder(id=uuid.uuid4()))
    session.commit.assert_not_awaited()
    sql = str(session.scalar.call_args.args[0].compile(dialect=dialect()))
    assert "FOR KEY SHARE" in sql


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["agent", "skill"])
async def test_tag_target_cannot_be_deleted_during_association(
    independent_sessions: tuple[Role, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    role, factory = independent_sessions
    tag_model = AgentTag if kind == "agent" else SkillTag
    parent_model = AgentPreset if kind == "agent" else Skill
    async with factory() as session:
        if kind == "agent":
            parent = parent_model(
                workspace_id=role.workspace_id,
                name="Synthetic",
                slug="synthetic",
                model_name="synthetic",
                model_provider="openai",
            )
        else:
            parent = parent_model(
                workspace_id=role.workspace_id,
                name="Synthetic",
                slug="synthetic",
                description="Synthetic skill",
            )
        tag = tag_model(
            workspace_id=role.workspace_id,
            name="Synthetic",
            ref="synthetic",
            color="#000000",
        )
        session.add_all([parent, tag])
        await session.commit()
        parent_id, tag_id = parent.id, tag.id
    locked, release = asyncio.Event(), asyncio.Event()

    async def associate() -> object:
        async with factory() as session:
            service = (
                AgentTagsService(session, role=role)
                if kind == "agent"
                else SkillTagsService(session, role=role)
            )
            monkeypatch.setattr(
                service, "has_entitlement", AsyncMock(return_value=True)
            )
            method_name = (
                "_require_preset_and_tag_in_workspace"
                if kind == "agent"
                else "_require_skill_and_tag_in_workspace"
            )
            original = (
                service._require_preset_and_tag_in_workspace
                if isinstance(service, AgentTagsService)
                else service._require_skill_and_tag_in_workspace
            )

            async def check(*args) -> None:
                await original(*args)
                locked.set()
                await release.wait()

            monkeypatch.setattr(service, method_name, check)
            if isinstance(service, AgentTagsService):
                return await service.add_preset_tag(parent_id, tag_id)
            return await service.add_skill_tag(parent_id, tag_id)

    async def remove() -> None:
        async with factory() as session:
            await session.execute(delete(tag_model).where(tag_model.id == tag_id))
            await session.commit()

    writer = asyncio.create_task(associate())
    remover = None
    try:
        await asyncio.wait_for(locked.wait(), timeout=10)
        remover = asyncio.create_task(remove())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(remover), timeout=0.1)
        release.set()
        await asyncio.wait_for(writer, timeout=10)
        await asyncio.wait_for(remover, timeout=10)
    finally:
        release.set()
        await asyncio.gather(
            writer, *([remover] if remover is not None else []), return_exceptions=True
        )
