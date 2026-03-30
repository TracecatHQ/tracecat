"""Tests for AgentCatalogService and startup sync from tracecat.agent.catalog."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.builtin_catalog import BuiltInCatalogModel
from tracecat.agent.catalog.service import AgentCatalogService, parse_catalog_offset
from tracecat.agent.catalog.startup import sync_model_catalogs_on_startup
from tracecat.agent.types import ModelDiscoveryStatus, ModelSourceType
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentModelSelectionLink,
    OrganizationSetting,
)


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset(
            {
                "agent:read",
                "agent:update",
                "org:secret:read",
                "workspace:read",
                "workspace:update",
            }
        ),
    )


# ---------------------------------------------------------------------------
# Mock-based tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_builtin_catalog_does_not_surface_snapshot_only_rows(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list_builtin_catalog method should rely on persisted catalog rows
    and NOT call get_builtin_catalog_models() at list time."""
    service = AgentCatalogService(AsyncMock(), role=role)

    # Stub the internal helpers that list_builtin_catalog delegates to.
    monkeypatch.setattr(
        "tracecat.agent.catalog.service.load_catalog_state",
        AsyncMock(return_value=(ModelDiscoveryStatus.READY, None, None)),
    )
    mock_result = Mock()
    mock_result.scalars.return_value.all.return_value = []
    service.session.execute = AsyncMock(return_value=mock_result)
    service._load_provider_credentials = AsyncMock(
        return_value={"OPENAI_API_KEY": "sk-test"}
    )

    # If the implementation mistakenly called the snapshot function, this
    # monkeypatch would blow up the test.
    monkeypatch.setattr(
        "tracecat.agent.catalog.service.get_builtin_catalog_models",
        lambda: (_ for _ in ()).throw(
            AssertionError("snapshot builtin catalog should not be consulted")
        ),
    )

    result = await service.list_builtin_catalog()

    assert result.models == []


@pytest.mark.anyio
async def test_list_builtin_catalog_uses_persisted_rows_with_existing_readiness_state(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When there are persisted catalog rows and a matching selection link,
    the catalog entry should report enabled=True and correct metadata."""
    service = AgentCatalogService(AsyncMock(), role=role)

    catalog_id = uuid.uuid4()
    persisted_row = AgentCatalog(
        id=catalog_id,
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-persisted",
        model_metadata={"mode": "chat", "slot": "persisted"},
    )
    selection_link = AgentModelSelectionLink(
        organization_id=role.organization_id,
        workspace_id=None,
        catalog_id=catalog_id,
        enabled_config=None,
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.service.load_catalog_state",
        AsyncMock(return_value=(ModelDiscoveryStatus.READY, None, None)),
    )
    # Session returns selection links first, then catalog rows.
    links_result = Mock()
    links_result.scalars.return_value.all.return_value = [selection_link]
    rows_result = Mock()
    rows_result.scalars.return_value.all.return_value = [persisted_row]
    service.session.execute = AsyncMock(side_effect=[links_result, rows_result])
    service._load_provider_credentials = AsyncMock(
        return_value={"OPENAI_API_KEY": "sk-test"}
    )

    result = await service.list_builtin_catalog()

    assert len(result.models) == 1
    model = result.models[0]
    assert model.model_name == "gpt-persisted"
    assert model.enabled is True
    assert model.ready is True
    assert model.enableable is True
    assert model.discovered is True
    assert model.credentials_configured is True
    assert model.metadata == {"mode": "chat", "slot": "persisted"}


@pytest.mark.anyio
@pytest.mark.parametrize("cursor", ["abc", "-1"])
async def test_list_builtin_catalog_rejects_invalid_cursor(
    role: Role,
    cursor: str,
) -> None:
    """parse_catalog_offset (called at the top of list_builtin_catalog)
    should raise ValueError for non-decimal / negative cursors."""
    with pytest.raises(
        ValueError, match="Invalid cursor. Expected a non-negative integer offset."
    ):
        parse_catalog_offset(cursor)


@pytest.mark.anyio
async def test_list_providers_defaults_to_configured_only_without_discovered_models(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AgentCatalogService(AsyncMock(), role=role)
    monkeypatch.setattr(
        "tracecat.agent.catalog.service.load_catalog_state",
        AsyncMock(return_value=(ModelDiscoveryStatus.READY, None, None)),
    )
    service._load_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "sk-test"} if provider == "openai" else None
        )
    )

    execute_mock = AsyncMock()
    service.session.execute = execute_mock

    providers = await service.list_providers()

    assert [provider.provider for provider in providers] == ["openai"]
    # Session should not be queried for catalog rows or selection links
    # when include_discovered_models is False (default).
    execute_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_list_providers_can_include_unconfigured_and_discovered_models(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AgentCatalogService(AsyncMock(), role=role)
    monkeypatch.setattr(
        "tracecat.agent.catalog.service.load_catalog_state",
        AsyncMock(return_value=(ModelDiscoveryStatus.READY, None, None)),
    )
    catalog_id = uuid.uuid4()
    persisted_row = AgentCatalog(
        id=catalog_id,
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-persisted",
        model_metadata={"mode": "chat"},
    )
    selection_link = AgentModelSelectionLink(
        organization_id=role.organization_id,
        workspace_id=None,
        catalog_id=catalog_id,
        enabled_config=None,
    )
    service._load_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "sk-test"} if provider == "openai" else None
        )
    )
    # Session returns selection links first, then catalog rows.
    links_result = Mock()
    links_result.scalars.return_value.all.return_value = [selection_link]
    rows_result = Mock()
    rows_result.scalars.return_value.all.return_value = [persisted_row]
    service.session.execute = AsyncMock(side_effect=[links_result, rows_result])

    providers = await service.list_providers(
        configured_only=False,
        include_discovered_models=True,
    )

    openai = next(provider for provider in providers if provider.provider == "openai")
    anthropic = next(
        provider for provider in providers if provider.provider == "anthropic"
    )
    assert openai.discovered_models[0].model_name == "gpt-persisted"
    assert openai.discovered_models[0].enabled is True
    assert anthropic.credentials_configured is False
    assert anthropic.discovered_models == []


# ---------------------------------------------------------------------------
# DB-backed test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_upsert_builtin_catalog_rows_prunes_stale_workspace_subset_and_default(
    session: AsyncSession,
    svc_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _upsert_builtin_catalog_rows replaces the builtin catalog, stale
    selection links and stale default-model org settings should be cleaned up."""
    service = AgentCatalogService(session, role=svc_admin_role)
    workspace_id = svc_admin_role.workspace_id
    assert workspace_id is not None

    stale_catalog_id = uuid.uuid4()
    fresh_catalog_id = uuid.uuid4()

    # Insert a stale catalog row that will be superseded.
    stale_catalog = AgentCatalog(
        id=stale_catalog_id,
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-stale",
    )
    session.add(stale_catalog)
    await session.flush()

    # Create an org-level selection link for the stale catalog row.
    org_link = AgentModelSelectionLink(
        organization_id=svc_admin_role.organization_id,
        workspace_id=None,
        catalog_id=stale_catalog_id,
        enabled_config=None,
    )
    session.add(org_link)
    await session.flush()

    # Create a workspace-level selection link for the stale catalog row.
    ws_link = AgentModelSelectionLink(
        organization_id=svc_admin_role.organization_id,
        workspace_id=workspace_id,
        catalog_id=stale_catalog_id,
    )
    session.add(ws_link)

    # Point the org default model setting at the stale model.
    session.add(
        OrganizationSetting(
            organization_id=svc_admin_role.organization_id,
            key="agent_default_model",
            value=orjson.dumps(
                {
                    "source_id": None,
                    "model_provider": "openai",
                    "model_name": "gpt-stale",
                }
            ),
            value_type="json",
            is_encrypted=False,
        )
    )
    await session.commit()

    # Replace the builtin catalog with a fresh model only.
    monkeypatch.setattr(
        "tracecat.agent.catalog.service.get_builtin_catalog_models",
        lambda: [
            BuiltInCatalogModel(
                agent_catalog_id=fresh_catalog_id,
                source_type=ModelSourceType.OPENAI,
                model_provider="openai",
                model_id="gpt-fresh",
                display_name="GPT Fresh",
                mode="chat",
                enableable=True,
                readiness_message=None,
                metadata={"slot": "fresh"},
            )
        ],
    )

    await service._upsert_builtin_catalog_rows()
    await session.commit()

    # The stale catalog row should be gone; only the fresh one should remain.
    catalog_rows = (
        (
            await session.execute(
                select(AgentCatalog).where(AgentCatalog.organization_id.is_(None))
            )
        )
        .scalars()
        .all()
    )
    assert [(row.id, row.model_name) for row in catalog_rows] == [
        (fresh_catalog_id, "gpt-fresh")
    ]

    # All selection links for the stale catalog should be pruned.
    remaining_links = (
        (
            await session.execute(
                select(AgentModelSelectionLink).where(
                    AgentModelSelectionLink.organization_id
                    == svc_admin_role.organization_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert remaining_links == []

    # The default model setting should be cleared.
    settings = (
        (
            await session.execute(
                select(OrganizationSetting).where(
                    OrganizationSetting.organization_id
                    == svc_admin_role.organization_id,
                    OrganizationSetting.key == "agent_default_model",
                )
            )
        )
        .scalars()
        .all()
    )
    assert all(orjson.loads(s.value) is None for s in settings)


# ---------------------------------------------------------------------------
# Startup sync tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sync_model_catalogs_on_startup_acquires_lock_and_syncs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the advisory lock is acquired, _sync_model_catalogs_as_leader runs."""
    session = AsyncMock()

    @asynccontextmanager
    async def session_cm():
        yield session

    synced = []

    async def fake_sync(s: object) -> None:
        synced.append(True)

    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.get_async_session_bypass_rls_context_manager",
        session_cm,
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.try_pg_advisory_lock",
        AsyncMock(return_value=True),
    )
    unlock = AsyncMock()
    monkeypatch.setattr("tracecat.agent.catalog.startup.pg_advisory_unlock", unlock)
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup._sync_model_catalogs_as_leader",
        fake_sync,
    )

    await sync_model_catalogs_on_startup()

    assert synced == [True]
    unlock.assert_awaited_once()


@pytest.mark.anyio
async def test_sync_model_catalogs_on_startup_refreshes_platform_without_orgs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    platform_refreshes: list[str] = []

    @asynccontextmanager
    async def session_cm():
        yield session

    class FakeAdminAgentCatalogService:
        def __init__(self, _session: AsyncMock, role: object) -> None:
            del role

        async def refresh_platform_catalog(self) -> object:
            platform_refreshes.append("refreshed")
            return type("PlatformCatalog", (), {"models": []})()

    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.get_async_session_bypass_rls_context_manager",
        session_cm,
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.try_pg_advisory_lock",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup._list_active_organization_ids",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.AdminAgentCatalogService",
        FakeAdminAgentCatalogService,
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.AgentSourceService",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("org source refresh should be skipped without organizations")
        ),
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.AgentSelectionsService",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("selection cleanup should be skipped without organizations")
        ),
    )
    unlock = AsyncMock()
    monkeypatch.setattr("tracecat.agent.catalog.startup.pg_advisory_unlock", unlock)

    await sync_model_catalogs_on_startup()

    assert platform_refreshes == ["refreshed"]
    unlock.assert_awaited_once()


@pytest.mark.anyio
async def test_sync_model_catalogs_on_startup_waits_for_leader_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the advisory lock is NOT acquired, wait for the leader then return."""
    session = AsyncMock()

    @asynccontextmanager
    async def session_cm():
        yield session

    wait_events: list[str] = []

    @asynccontextmanager
    async def wait_for_leader(_session: object, _key: int):
        wait_events.append("entered")
        yield
        wait_events.append("exited")

    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.get_async_session_bypass_rls_context_manager",
        session_cm,
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.try_pg_advisory_lock",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup.pg_advisory_lock", wait_for_leader
    )
    sync_as_leader = AsyncMock()
    monkeypatch.setattr(
        "tracecat.agent.catalog.startup._sync_model_catalogs_as_leader",
        sync_as_leader,
    )

    await sync_model_catalogs_on_startup()

    assert wait_events == ["entered", "exited"]
    sync_as_leader.assert_not_awaited()
