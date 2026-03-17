"""Focused tests for the composite-key agent management service."""

import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy import insert, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.builtin_catalog import BuiltInCatalogModel
from tracecat.agent.schemas import (
    AgentModelSourceUpdate,
    DefaultModelSelection,
    EnabledModelRuntimeConfig,
    EnabledModelRuntimeConfigUpdate,
    ModelCatalogEntry,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ModelSelection,
    WorkspaceModelSubsetRead,
    WorkspaceModelSubsetUpdate,
)
from tracecat.agent.service import (
    ENABLE_ALL_MODELS_ON_UPGRADE_SETTING,
    SOURCE_RUNTIME_BASE_URL,
    AgentManagementService,
    LegacyModelRepairSummary,
    ResolvedCatalogRecord,
    SourceDiscoveryResult,
    sync_model_catalogs_on_startup,
)
from tracecat.agent.types import AgentConfig, CustomModelSourceType, ModelSourceType
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentEnabledModel,
    AgentModelSource,
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
)
from tracecat.exceptions import TracecatNotFoundError


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


@pytest.mark.anyio
async def test_resolve_runtime_agent_config_prefers_enabled_selection(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    source_id = uuid.uuid4()
    config = AgentConfig(
        model_name="stale-model",
        model_provider="stale-provider",
        source_id=source_id,
        instructions="Use this style",
        actions=["core.http_request"],
        namespaces=["tools.openai"],
        model_settings={"temperature": 0.1},
        retries=7,
        enable_internet_access=True,
        base_url="https://override.example/v1",
    )
    service.is_model_enabled = AsyncMock(return_value=True)
    service._resolve_catalog_agent_config = AsyncMock(
        return_value=(
            AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                source_id=source_id,
                base_url="https://catalog.example/v1",
            ),
            {},
        )
    )

    resolved = await service.resolve_runtime_agent_config(config)

    service.is_model_enabled.assert_awaited_once()
    assert resolved.model_name == "gpt-5.2"
    assert resolved.model_provider == "openai"
    assert resolved.source_id == source_id
    assert resolved.base_url == "https://override.example/v1"
    assert resolved.instructions == "Use this style"
    assert resolved.actions == ["core.http_request"]
    assert resolved.namespaces == ["tools.openai"]
    assert resolved.model_settings == {"temperature": 0.1}
    assert resolved.retries == 7
    assert resolved.enable_internet_access is True


@pytest.mark.anyio
async def test_sync_model_catalogs_on_startup_refreshes_platform_and_sources() -> None:
    session = AsyncMock()
    org_ids = [uuid.uuid4(), uuid.uuid4()]
    refreshed_sources_by_org: dict[uuid.UUID, list[uuid.UUID]] = {
        org_ids[0]: [uuid.uuid4()],
        org_ids[1]: [uuid.uuid4(), uuid.uuid4()],
    }
    observed_roles: list[uuid.UUID] = []
    pruned_orgs: list[uuid.UUID] = []

    @asynccontextmanager
    async def session_cm():
        yield session

    class FakeAgentManagementService:
        def __init__(self, _session: AsyncMock, role: Role) -> None:
            org_id = role.organization_id
            assert org_id is not None
            observed_roles.append(org_id)
            self.organization_id = org_id

        async def refresh_builtin_catalog(self):
            return type("BuiltinsInventory", (), {"models": []})()

        async def _ensure_default_enabled_models(self) -> None:
            return None

        async def check_provider_credentials(self, provider: str) -> bool:
            del provider
            return False

        async def prune_stale_builtin_model_selections(self) -> set[object]:
            pruned_orgs.append(self.organization_id)
            return set()

        async def prune_unconfigured_builtin_model_selections(self) -> set[object]:
            return set()

        async def list_model_sources(self) -> list[object]:
            return [
                type("SourceRow", (), {"id": source_id})()
                for source_id in refreshed_sources_by_org[self.organization_id]
            ]

        async def refresh_model_source(self, source_id: uuid.UUID) -> list[object]:
            assert source_id in refreshed_sources_by_org[self.organization_id]
            return []

        async def repair_legacy_model_selections(self) -> LegacyModelRepairSummary:
            return LegacyModelRepairSummary()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "tracecat.agent.service.get_async_session_bypass_rls_context_manager",
        session_cm,
    )
    monkeypatch.setattr(
        "tracecat.agent.service.get_async_session_context_manager",
        lambda: (_ for _ in ()).throw(
            AssertionError("startup sync must use the RLS-bypass session")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "tracecat.agent.service.try_pg_advisory_lock",
        AsyncMock(return_value=True),
    )
    unlock = AsyncMock()
    monkeypatch.setattr("tracecat.agent.service.pg_advisory_unlock", unlock)
    monkeypatch.setattr(
        "tracecat.agent.service._list_active_organization_ids",
        AsyncMock(return_value=org_ids),
    )
    monkeypatch.setattr(
        "tracecat.agent.service.AgentManagementService",
        FakeAgentManagementService,
    )
    try:
        await sync_model_catalogs_on_startup()
    finally:
        monkeypatch.undo()

    assert observed_roles == [org_ids[0], *org_ids]
    assert pruned_orgs == org_ids
    unlock.assert_awaited_once()


@pytest.mark.anyio
async def test_sync_model_catalogs_on_startup_waits_for_leader_completion() -> None:
    session = AsyncMock()

    @asynccontextmanager
    async def session_cm():
        yield session

    wait_events: list[str] = []

    @asynccontextmanager
    async def wait_for_leader(_session: AsyncMock, _key: int):
        wait_events.append("entered")
        yield
        wait_events.append("exited")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "tracecat.agent.service.get_async_session_bypass_rls_context_manager",
        session_cm,
    )
    monkeypatch.setattr(
        "tracecat.agent.service.try_pg_advisory_lock",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr("tracecat.agent.service.pg_advisory_lock", wait_for_leader)
    sync_as_leader = AsyncMock()
    monkeypatch.setattr(
        "tracecat.agent.service._sync_model_catalogs_as_leader",
        sync_as_leader,
    )
    try:
        await sync_model_catalogs_on_startup()
    finally:
        monkeypatch.undo()

    assert wait_events == ["entered", "exited"]
    sync_as_leader.assert_not_awaited()


@pytest.mark.anyio
async def test_get_workspace_model_subset_inherits_when_no_workspace_rows(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    workspace_id = uuid.uuid4()
    service._ensure_workspace_exists = AsyncMock()
    service._list_workspace_subset_rows = AsyncMock(return_value=[])

    subset = await service.get_workspace_model_subset(workspace_id)

    assert subset == WorkspaceModelSubsetRead(inherit_all=True, models=[])


@pytest.mark.anyio
async def test_replace_workspace_model_subset_rejects_explicit_empty(
    role: Role,
) -> None:
    session = AsyncMock()
    service = AgentManagementService(session, role=role)
    workspace_id = uuid.uuid4()
    service._ensure_workspace_exists = AsyncMock()

    with pytest.raises(
        ValueError,
        match="Workspace subsets must include at least one model when inherit_all is false.",
    ):
        await service.replace_workspace_model_subset(
            workspace_id,
            WorkspaceModelSubsetUpdate(inherit_all=False, models=[]),
        )


@pytest.mark.anyio
async def test_filter_enabled_rows_for_workspace_inherits_when_no_workspace_rows(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    workspace_id = uuid.uuid4()
    row = AgentEnabledModel(
        organization_id=role.organization_id,
        workspace_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
        enabled_config=None,
    )
    service._ensure_workspace_exists = AsyncMock()
    service._list_workspace_subset_rows = AsyncMock(return_value=[])

    filtered = await service._filter_enabled_rows_for_workspace([row], workspace_id)

    assert filtered == [row]


@pytest.mark.anyio
async def test_ensure_default_enabled_models_defers_when_catalog_is_empty(
    role: Role,
) -> None:
    session = AsyncMock()
    select_result = Mock()
    select_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=select_result)

    service = AgentManagementService(session, role=role)
    upgrade_setting = object()
    service.settings_service.get_org_setting = AsyncMock(return_value=upgrade_setting)
    service.settings_service.delete_org_setting = AsyncMock()

    await service._ensure_default_enabled_models()

    service.settings_service.get_org_setting.assert_awaited_once_with(
        ENABLE_ALL_MODELS_ON_UPGRADE_SETTING
    )
    service.settings_service.delete_org_setting.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_default_enabled_models_only_enables_configured_builtin_rows(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    catalog_rows = [
        type(
            "CatalogRow",
            (),
            {
                "source_id": None,
                "model_provider": "openai",
                "model_name": "gpt-5.2",
            },
        )(),
        type(
            "CatalogRow",
            (),
            {
                "source_id": None,
                "model_provider": "anthropic",
                "model_name": "claude-sonnet-4-5",
            },
        )(),
        type(
            "CatalogRow",
            (),
            {
                "source_id": None,
                "model_provider": "openai",
                "model_name": "text-embedding-3-large",
            },
        )(),
        type(
            "CatalogRow",
            (),
            {
                "source_id": uuid.uuid4(),
                "model_provider": "openai_compatible_gateway",
                "model_name": "qwen2.5:7b",
            },
        )(),
    ]
    select_result = Mock()
    select_result.scalars.return_value.all.return_value = catalog_rows
    insert_result = Mock()
    session.execute = AsyncMock(side_effect=[select_result, insert_result])

    service = AgentManagementService(session, role=role)
    upgrade_setting = object()
    service.settings_service.get_org_setting = AsyncMock(return_value=upgrade_setting)
    service.settings_service.delete_org_setting = AsyncMock()
    monkeypatch.setattr(
        "tracecat.agent.service.get_builtin_catalog_models",
        lambda: [
            BuiltInCatalogModel(
                agent_catalog_id=uuid.uuid4(),
                source_type=ModelSourceType.OPENAI,
                model_provider="openai",
                model_id="gpt-5.2",
                display_name="GPT 5.2",
                mode="chat",
                enableable=True,
                readiness_message=None,
                metadata={},
            ),
            BuiltInCatalogModel(
                agent_catalog_id=uuid.uuid4(),
                source_type=ModelSourceType.OPENAI,
                model_provider="openai",
                model_id="text-embedding-3-large",
                display_name="text-embedding-3-large",
                mode="embedding",
                enableable=False,
                readiness_message="Embeddings are not enableable for agents.",
                metadata={},
            ),
        ],
    )
    service.get_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "sk-test"} if provider == "openai" else None
        )
    )

    await service._ensure_default_enabled_models()

    service.settings_service.get_org_setting.assert_awaited_once_with(
        ENABLE_ALL_MODELS_ON_UPGRADE_SETTING
    )
    service.settings_service.delete_org_setting.assert_awaited_once_with(
        upgrade_setting
    )
    assert session.execute.await_count == 2
    insert_stmt = session.execute.await_args_list[1].args[0]
    compiled_params = insert_stmt.compile().params
    assert compiled_params["model_provider_m0"] == "openai"
    assert compiled_params["model_name_m0"] == "gpt-5.2"
    assert compiled_params["model_provider_m1"] == "openai_compatible_gateway"
    assert compiled_params["model_name_m1"] == "qwen2.5:7b"
    assert "model_provider_m2" not in compiled_params


@pytest.mark.anyio
async def test_ensure_default_enabled_models_defers_when_no_rows_are_eligible(
    role: Role,
) -> None:
    session = AsyncMock()
    catalog_rows = [
        type(
            "CatalogRow",
            (),
            {
                "source_id": None,
                "model_provider": "openai",
                "model_name": "gpt-5.2",
            },
        )(),
        type(
            "CatalogRow",
            (),
            {
                "source_id": None,
                "model_provider": "anthropic",
                "model_name": "claude-sonnet-4-5",
            },
        )(),
    ]
    select_result = Mock()
    select_result.scalars.return_value.all.return_value = catalog_rows
    session.execute = AsyncMock(return_value=select_result)

    service = AgentManagementService(session, role=role)
    upgrade_setting = object()
    service.settings_service.get_org_setting = AsyncMock(return_value=upgrade_setting)
    service.settings_service.delete_org_setting = AsyncMock()
    service.get_provider_credentials = AsyncMock(return_value=None)

    await service._ensure_default_enabled_models()

    service.settings_service.delete_org_setting.assert_not_called()
    session.commit.assert_not_awaited()
    session.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_create_provider_credentials_retries_deferred_upgrade_enable_all(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    created_secret = Mock()
    service.secrets_service.get_org_secret_by_name = AsyncMock(
        side_effect=[TracecatNotFoundError("missing"), created_secret]
    )
    service.secrets_service.create_org_secret = AsyncMock()
    service._ensure_default_enabled_models = AsyncMock()

    secret = await service.create_provider_credentials(
        ModelCredentialCreate(
            provider="openai",
            credentials={"OPENAI_API_KEY": "sk-test"},
        )
    )

    assert secret is created_secret
    service._ensure_default_enabled_models.assert_awaited_once()


@pytest.mark.anyio
async def test_update_provider_credentials_retries_deferred_upgrade_enable_all(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    secret = Mock()
    service.secrets_service.get_org_secret_by_name = AsyncMock(return_value=secret)
    service.secrets_service.update_org_secret = AsyncMock()
    service._ensure_default_enabled_models = AsyncMock()

    updated = await service.update_provider_credentials(
        "openai",
        ModelCredentialUpdate(credentials={"OPENAI_API_KEY": "sk-test"}),
    )

    assert updated is secret
    service._ensure_default_enabled_models.assert_awaited_once()


@pytest.mark.anyio
async def test_deserialize_sensitive_config_treats_invalid_payload_as_empty_dict(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)

    config = service._deserialize_sensitive_config(b"not-json-and-not-fernet")

    assert config == {}


@pytest.mark.anyio
async def test_refresh_model_source_retries_deferred_upgrade_enable_all(
    role: Role,
) -> None:
    session = AsyncMock()
    service = AgentManagementService(session, role=role)
    source_id = uuid.uuid4()
    source = AgentModelSource(
        id=source_id,
        organization_id=role.organization_id,
        model_provider=CustomModelSourceType.MANUAL_CUSTOM.value,
        display_name="Manual source",
        declared_models=[],
        encrypted_config=b"{}",
    )
    persisted_row = AgentCatalog(
        organization_id=role.organization_id,
        source_id=source_id,
        model_provider="openai",
        model_name="qwen-new",
    )
    service.get_model_source = AsyncMock(return_value=source)
    service._validate_source_uniqueness = AsyncMock()
    service._discover_source_models = AsyncMock(
        return_value=SourceDiscoveryResult(
            models=[
                {
                    "model_provider": persisted_row.model_provider,
                    "model_id": persisted_row.model_name,
                    "display_name": persisted_row.model_name,
                    "metadata": {"declared": True},
                }
            ]
        )
    )
    service._upsert_catalog_rows = AsyncMock(return_value=[persisted_row])
    service._ensure_default_enabled_models = AsyncMock()
    service._list_org_enabled_rows = AsyncMock(return_value=[])

    await service.refresh_model_source(source_id)

    service._ensure_default_enabled_models.assert_awaited_once()


@pytest.mark.anyio
async def test_update_model_source_retries_deferred_upgrade_enable_all(
    role: Role,
) -> None:
    session = AsyncMock()
    service = AgentManagementService(session, role=role)
    source_id = uuid.uuid4()
    source = AgentModelSource(
        id=source_id,
        organization_id=role.organization_id,
        model_provider=CustomModelSourceType.MANUAL_CUSTOM.value,
        display_name="Manual source",
        declared_models=[],
        encrypted_config=b"{}",
    )
    service.get_model_source = AsyncMock(return_value=source)
    service._validate_source_uniqueness = AsyncMock()
    service._ensure_default_enabled_models = AsyncMock()

    updated = await service.update_model_source(
        source_id,
        AgentModelSourceUpdate(),
    )

    assert updated.id == source_id
    service._ensure_default_enabled_models.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_prune_unconfigured_builtin_model_selections_disables_only_disconnected_providers(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    enabled_rows = [
        AgentEnabledModel(
            organization_id=role.organization_id,
            workspace_id=None,
            source_id=None,
            model_provider="openai",
            model_name="gpt-5.2",
            enabled_config=None,
        ),
        AgentEnabledModel(
            organization_id=role.organization_id,
            workspace_id=None,
            source_id=None,
            model_provider="gemini",
            model_name="gemini-2.5-pro",
            enabled_config=None,
        ),
        AgentEnabledModel(
            organization_id=role.organization_id,
            workspace_id=None,
            source_id=uuid.uuid4(),
            model_provider="openai_compatible_gateway",
            model_name="qwen2.5:7b",
            enabled_config=None,
        ),
    ]
    service._list_org_enabled_rows = AsyncMock(return_value=enabled_rows)
    service.get_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "sk-test"} if provider == "openai" else None
        )
    )
    service._disable_model_selections = AsyncMock(
        return_value={
            service._selection_key(
                source_id=None,
                model_provider="gemini",
                model_name="gemini-2.5-pro",
            )
        }
    )

    disabled = await service.prune_unconfigured_builtin_model_selections()

    service._disable_model_selections.assert_awaited_once_with(
        [
            service._selection_key(
                source_id=None,
                model_provider="gemini",
                model_name="gemini-2.5-pro",
            )
        ]
    )
    assert disabled == {
        service._selection_key(
            source_id=None,
            model_provider="gemini",
            model_name="gemini-2.5-pro",
        )
    }
    service.get_provider_credentials.assert_any_await("openai")
    service.get_provider_credentials.assert_any_await("gemini")


@pytest.mark.anyio
async def test_openai_compatible_discovery_ignores_unsupported_provider_hints(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)

    normalized = service._normalize_openai_compatible_entries(
        source_type=ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
        source_name="Gateway",
        items=[
            {"id": "gpt-4o-mini", "owned_by": "openrouter"},
            {"id": "claude-3-7-sonnet", "owned_by": "anthropic"},
            {"id": "llama-3.3-70b", "provider": "groq"},
        ],
    )

    assert [item["model_provider"] for item in normalized] == [
        "openai_compatible_gateway",
        "anthropic",
        "openai_compatible_gateway",
    ]


@pytest.mark.anyio
async def test_fetch_openai_compatible_models_retries_after_invalid_json(
    role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)

    class _FakeResponse:
        def __init__(self, payload: object, *, should_raise_json: bool = False):
            self._payload = payload
            self._should_raise_json = should_raise_json

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            if self._should_raise_json:
                raise json.JSONDecodeError("Expecting value", "", 0)
            return self._payload

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(
            self, url: str, headers: dict[str, str] | None = None
        ) -> _FakeResponse:
            del headers
            self.calls += 1
            if self.calls == 1:
                assert url == "https://gateway.example"
                return _FakeResponse({}, should_raise_json=True)
            assert url == "https://gateway.example/"
            return _FakeResponse(
                {
                    "data": [
                        {"id": "qwen2.5:0.5b"},
                        {"id": "qwen2.5:1.5b"},
                    ]
                }
            )

    monkeypatch.setattr("tracecat.agent.service.httpx.AsyncClient", _FakeClient)

    discovery = await service._fetch_openai_compatible_models(
        base_url="https://gateway.example",
        api_key=None,
        api_key_header=None,
    )

    assert [item["id"] for item in discovery.items] == [
        "qwen2.5:0.5b",
        "qwen2.5:1.5b",
    ]
    assert discovery.runtime_base_url == "https://gateway.example"


@pytest.mark.anyio
async def test_fetch_openai_compatible_models_sanitizes_logged_urls(
    role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    warning_mock = Mock()
    monkeypatch.setattr(service.logger, "warning", warning_mock)

    class _FailingClient:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> object:
            del headers
            raise httpx.RequestError(
                "connect failed", request=httpx.Request("GET", url)
            )

    monkeypatch.setattr("tracecat.agent.service.httpx.AsyncClient", _FailingClient)

    with pytest.raises(TracecatNotFoundError, match="Failed to discover models"):
        await service._fetch_openai_compatible_models(
            base_url="https://user:pass@gateway.example/v1?token=secret",
            api_key=None,
            api_key_header=None,
        )

    warning_mock.assert_called_once()
    logged = warning_mock.call_args.kwargs
    assert logged["base_url"] == "https://gateway.example/v1"
    assert "user:pass@" not in logged["detail"]
    assert "token=secret" not in logged["detail"]


@pytest.mark.anyio
async def test_get_runtime_credentials_uses_discovered_runtime_base_url_for_gateway(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    source_id = uuid.uuid4()
    source = AgentModelSource(
        organization_id=role.organization_id,
        display_name="Ollama",
        model_provider="openai_compatible_gateway",
        base_url="http://host.docker.internal:11434",
        encrypted_config=service._serialize_sensitive_config(
            {
                "api_key": "not-needed",
                "runtime_base_url": "http://host.docker.internal:11434/v1",
            }
        ),
    )
    service.get_model_source = AsyncMock(return_value=source)

    credentials = await service._get_runtime_credentials(
        catalog_entry=ResolvedCatalogRecord(
            source_id=source_id,
            model_provider="openai_compatible_gateway",
            model_name="qwen2.5:0.5b",
            source_type=ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
            source_name="Ollama",
            base_url=None,
            last_refreshed_at=None,
            metadata=None,
        )
    )

    assert (
        credentials[SOURCE_RUNTIME_BASE_URL] == "http://host.docker.internal:11434/v1"
    )
    assert credentials["OPENAI_BASE_URL"] == "http://host.docker.internal:11434/v1"


@pytest.mark.anyio
async def test_prune_stale_model_selections_only_invalidates_deleted_rows(
    role: Role,
) -> None:
    session = AsyncMock()
    service = AgentManagementService(session, role=role)
    deleted_selection = service._selection_key(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    untouched_selection = service._selection_key(
        source_id=None,
        model_provider="anthropic",
        model_name="claude-3-7-sonnet",
    )
    execute_result = Mock()
    execute_result.tuples.return_value.all.return_value = [
        (
            deleted_selection.source_id,
            deleted_selection.model_provider,
            deleted_selection.model_name,
        )
    ]
    session.execute.return_value = execute_result
    service._invalidate_stale_selection_dependents = AsyncMock()
    service._revalidate_default_model_setting = AsyncMock()

    disabled = await service._prune_stale_model_selections(
        [deleted_selection, untouched_selection]
    )

    assert disabled == {deleted_selection}
    service._invalidate_stale_selection_dependents.assert_awaited_once_with(
        [deleted_selection]
    )
    service._revalidate_default_model_setting.assert_awaited_once_with(
        {deleted_selection}
    )


@pytest.mark.anyio
async def test_update_enabled_model_config_uses_composite_selection(role: Role) -> None:
    session = AsyncMock()
    session.add = Mock()
    service = AgentManagementService(session, role=role)
    enabled_row = AgentEnabledModel(
        organization_id=role.organization_id,
        workspace_id=None,
        source_id=None,
        model_provider="bedrock",
        model_name="anthropic.claude-sonnet-4-6",
        enabled_config=None,
    )
    service._get_enabled_row = AsyncMock(return_value=enabled_row)
    service._get_catalog_row_model = AsyncMock(
        return_value=type("CatalogRow", (), {"model_provider": "bedrock"})()
    )
    expected_entry = ModelCatalogEntry(
        source_id=None,
        model_provider="bedrock",
        model_name="anthropic.claude-sonnet-4-6",
        source_type=ModelSourceType.BEDROCK.value,
        source_name="Bedrock",
        enabled=True,
        enabled_config=EnabledModelRuntimeConfig(
            bedrock_inference_profile_id="profile-123"
        ),
    )
    service._build_enabled_model_entries = AsyncMock(return_value=[expected_entry])

    result = await service.update_enabled_model_config(
        EnabledModelRuntimeConfigUpdate(
            source_id=None,
            model_provider="bedrock",
            model_name="anthropic.claude-sonnet-4-6",
            config=EnabledModelRuntimeConfig(
                bedrock_inference_profile_id="profile-123"
            ),
        )
    )

    assert enabled_row.enabled_config == {"bedrock_inference_profile_id": "profile-123"}
    session.commit.assert_awaited_once()
    assert result == expected_entry


@pytest.mark.anyio
async def test_get_runtime_credentials_for_selection_prefers_enabled_bedrock_profile(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    selection = ModelSelection(
        source_id=None,
        model_provider="bedrock",
        model_name="anthropic.claude-sonnet-4-6",
    )
    service._get_catalog_row = AsyncMock(
        return_value=ResolvedCatalogRecord(
            source_id=None,
            model_provider="bedrock",
            model_name="anthropic.claude-sonnet-4-6",
            source_type=ModelSourceType.BEDROCK,
            source_name="Bedrock",
            base_url=None,
            last_refreshed_at=None,
            metadata=None,
        )
    )
    service.get_provider_credentials = AsyncMock(
        return_value={"AWS_REGION": "us-east-1"}
    )
    service._get_enabled_row = AsyncMock(
        return_value=type(
            "EnabledRow",
            (),
            {"enabled_config": {"bedrock_inference_profile_id": "profile-123"}},
        )()
    )

    credentials = await service.get_runtime_credentials_for_selection(
        selection=selection
    )

    assert credentials == {
        "AWS_REGION": "us-east-1",
        "AWS_INFERENCE_PROFILE_ID": "profile-123",
    }


@pytest.mark.anyio
async def test_get_runtime_credentials_for_selection_preserves_source_protocol_details(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    source_id = uuid.uuid4()
    selection = ModelSelection(
        source_id=source_id,
        model_provider="anthropic",
        model_name="claude-3-7-sonnet",
    )
    service._get_catalog_row = AsyncMock(
        return_value=ResolvedCatalogRecord(
            source_id=source_id,
            model_provider="anthropic",
            model_name="claude-3-7-sonnet",
            source_type=ModelSourceType.MANUAL_CUSTOM,
            source_name="Manual source",
            base_url="https://anthropic.gateway.example",
            last_refreshed_at=None,
            metadata=None,
        )
    )
    service.get_model_source = AsyncMock(
        return_value=type(
            "SourceRow",
            (),
            {
                "encrypted_config": b"encrypted",
                "base_url": "https://anthropic.gateway.example",
                "api_key_header": "X-Api-Key",
                "api_version": "2024-06-01",
                "declared_models": [],
                "model_provider": CustomModelSourceType.MANUAL_CUSTOM.value,
            },
        )()
    )
    service._deserialize_sensitive_config = Mock(return_value={"api_key": "source-key"})

    credentials = await service.get_runtime_credentials_for_selection(
        selection=selection
    )

    assert credentials == {
        "TRACECAT_SOURCE_API_KEY": "source-key",
        "TRACECAT_SOURCE_API_KEY_HEADER": "X-Api-Key",
        "TRACECAT_SOURCE_API_VERSION": "2024-06-01",
        "TRACECAT_SOURCE_BASE_URL": "https://anthropic.gateway.example",
        "ANTHROPIC_API_KEY": "source-key",
        "ANTHROPIC_BASE_URL": "https://anthropic.gateway.example",
    }


@pytest.mark.anyio
async def test_get_runtime_credentials_for_selection_uses_org_secret_only(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    selection = ModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    service._get_catalog_row = AsyncMock(
        return_value=ResolvedCatalogRecord(
            source_id=None,
            model_provider="openai",
            model_name="gpt-5.2",
            source_type=ModelSourceType.OPENAI,
            source_name="OpenAI",
            base_url=None,
            last_refreshed_at=None,
            metadata=None,
        )
    )
    service.get_provider_credentials = AsyncMock(return_value=None)
    service._get_enabled_row = AsyncMock(return_value=None)

    credentials = await service.get_runtime_credentials_for_selection(
        selection=selection
    )

    assert credentials == {}


@pytest.mark.anyio
async def test_get_providers_status_uses_internal_credential_lookup(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_provider_credentials = AsyncMock(
        side_effect=AssertionError("public credential getter should not be used")
    )
    service._load_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "org-key"} if provider == "openai" else None
        )
    )

    status = await service.get_providers_status()

    assert status["openai"] is True
    assert status["anthropic"] is False
    service.get_provider_credentials.assert_not_awaited()
    service._load_provider_credentials.assert_any_await("openai")
    service._load_provider_credentials.assert_any_await("anthropic")


@pytest.mark.anyio
async def test_resolve_enableable_catalog_row_uses_org_provider_credentials(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    selection = ModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    provider_credentials_cache: dict[str, dict[str, str] | None] = {}
    service._get_catalog_row = AsyncMock(
        return_value=ResolvedCatalogRecord(
            source_id=None,
            model_provider="openai",
            model_name="gpt-5.2",
            source_type=ModelSourceType.OPENAI,
            source_name="OpenAI",
            base_url=None,
            last_refreshed_at=None,
            metadata=None,
        )
    )
    service.get_provider_credentials = AsyncMock(
        return_value={"OPENAI_API_KEY": "workspace-key"}
    )
    monkeypatch.setattr(
        "tracecat.agent.service.get_builtin_catalog_by_provider",
        lambda: {
            "openai": [
                type(
                    "BuiltinModel",
                    (),
                    {
                        "model_provider": "openai",
                        "model_id": "gpt-5.2",
                        "enableable": True,
                        "readiness_message": None,
                    },
                )()
            ]
        },
    )

    resolved = await service._resolve_enableable_catalog_row(
        service._selection_key_from_model_selection(selection),
        provider_credentials_cache,
    )

    assert resolved.model_provider == "openai"
    assert provider_credentials_cache == {"openai": {"OPENAI_API_KEY": "workspace-key"}}
    service.get_provider_credentials.assert_awaited_once_with("openai")


@pytest.mark.anyio
async def test_get_runtime_credentials_for_config_prefers_enabled_selection(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    config = AgentConfig(
        model_name="claude-3-7-sonnet",
        model_provider="bedrock",
    )
    service.is_model_enabled = AsyncMock(return_value=True)
    service.get_runtime_credentials_for_selection = AsyncMock(
        return_value={"AWS_INFERENCE_PROFILE_ID": "profile-123"}
    )
    service.get_provider_credentials = AsyncMock(
        return_value={"AWS_ACCESS_KEY_ID": "raw-key"}
    )

    credentials = await service.get_runtime_credentials_for_config(config)

    assert credentials == {"AWS_INFERENCE_PROFILE_ID": "profile-123"}
    service.get_runtime_credentials_for_selection.assert_awaited_once()
    service.get_provider_credentials.assert_not_awaited()


@pytest.mark.anyio
async def test_get_runtime_credentials_for_config_falls_back_when_selection_disabled(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
    )
    service.is_model_enabled = AsyncMock(return_value=False)
    service.get_runtime_credentials_for_selection = AsyncMock()
    service.get_provider_credentials = AsyncMock(
        return_value={"OPENAI_API_KEY": "raw-key"}
    )

    credentials = await service.get_runtime_credentials_for_config(config)

    assert credentials == {"OPENAI_API_KEY": "raw-key"}
    service.get_runtime_credentials_for_selection.assert_not_awaited()
    service.get_provider_credentials.assert_awaited_once_with("openai")


@pytest.mark.anyio
async def test_get_default_model_reads_composite_setting(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    setting = type(
        "Setting",
        (),
        {
            "value": {
                "source_id": None,
                "model_provider": "openai",
                "model_name": "gpt-5.2",
            }
        },
    )()
    service.settings_service.get_org_setting = AsyncMock(return_value=setting)
    service.settings_service.get_value = lambda setting: setting.value
    service._get_catalog_row = AsyncMock(
        return_value=ResolvedCatalogRecord(
            source_id=None,
            model_provider="openai",
            model_name="gpt-5.2",
            source_type=ModelSourceType.OPENAI,
            source_name="OpenAI",
            base_url=None,
            last_refreshed_at=None,
            metadata=None,
        )
    )

    default_model = await service.get_default_model()

    assert default_model == DefaultModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
        source_type=ModelSourceType.OPENAI,
        source_name="OpenAI",
    )


@pytest.mark.anyio
async def test_with_model_config_rejects_workspace_excluded_default_model(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    default_selection = DefaultModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
        source_type=ModelSourceType.OPENAI,
        source_name="OpenAI",
    )
    service.get_default_model = AsyncMock(return_value=default_selection)
    service.require_enabled_model_selection = AsyncMock(
        side_effect=TracecatNotFoundError("Model openai/gpt-5.2 is not enabled")
    )
    service._resolve_catalog_agent_config = AsyncMock()

    with pytest.raises(
        TracecatNotFoundError,
        match="Model openai/gpt-5.2 is not enabled",
    ):
        async with service.with_model_config():
            pytest.fail(
                "with_model_config should not yield when the default is excluded"
            )

    service.require_enabled_model_selection.assert_awaited_once_with(
        default_selection,
        workspace_id=role.workspace_id,
    )
    service._resolve_catalog_agent_config.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_model_source_skips_enabled_model_delete_when_catalog_is_empty(
    role: Role,
) -> None:
    session = AsyncMock()
    select_result = Mock()
    select_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=select_result)
    source = Mock()

    service = AgentManagementService(session, role=role)
    service.get_model_source = AsyncMock(return_value=source)
    service._validate_source_uniqueness = AsyncMock()

    await service.delete_model_source(uuid.uuid4())

    session.execute.assert_awaited_once()
    session.flush.assert_not_awaited()
    session.delete.assert_awaited_once_with(source)
    session.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_delete_model_source_clears_default_model_selection(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    service = AgentManagementService(session, role=svc_admin_role)
    source = AgentModelSource(
        organization_id=svc_admin_role.organization_id,
        model_provider=CustomModelSourceType.MANUAL_CUSTOM.value,
        display_name="Manual source",
        declared_models=[],
    )
    session.add(source)
    await session.flush()

    selection = ModelSelection(
        source_id=source.id,
        model_provider="anthropic",
        model_name="claude-3-7-sonnet",
    )
    session.add_all(
        [
            AgentCatalog(
                organization_id=svc_admin_role.organization_id,
                source_id=source.id,
                model_provider=selection.model_provider,
                model_name=selection.model_name,
            ),
            AgentEnabledModel(
                organization_id=svc_admin_role.organization_id,
                workspace_id=None,
                source_id=source.id,
                model_provider=selection.model_provider,
                model_name=selection.model_name,
                enabled_config=None,
            ),
        ]
    )
    await session.commit()

    await service.set_default_model_selection(selection)

    await service.delete_model_source(source.id)

    assert await service.get_default_model() is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_upsert_catalog_rows_prunes_stale_workspace_subset_and_default(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    service = AgentManagementService(session, role=svc_admin_role)
    workspace_id = svc_admin_role.workspace_id
    assert workspace_id is not None
    source = AgentModelSource(
        organization_id=svc_admin_role.organization_id,
        model_provider=CustomModelSourceType.MANUAL_CUSTOM.value,
        display_name="Manual source",
        declared_models=[],
    )
    session.add(source)
    await session.flush()

    stale_selection = ModelSelection(
        source_id=source.id,
        model_provider="openai",
        model_name="qwen-old",
    )
    session.add_all(
        [
            AgentCatalog(
                organization_id=svc_admin_role.organization_id,
                source_id=source.id,
                model_provider=stale_selection.model_provider,
                model_name=stale_selection.model_name,
            ),
            AgentEnabledModel(
                organization_id=svc_admin_role.organization_id,
                workspace_id=None,
                source_id=source.id,
                model_provider=stale_selection.model_provider,
                model_name=stale_selection.model_name,
                enabled_config=None,
            ),
        ]
    )
    await session.commit()
    await service.set_default_model_selection(stale_selection)
    await session.execute(
        insert(AgentEnabledModel).values(
            organization_id=svc_admin_role.organization_id,
            workspace_id=workspace_id,
            source_id=source.id,
            model_provider=stale_selection.model_provider,
            model_name=stale_selection.model_name,
            enabled_config=null(),
        )
    )
    await session.commit()

    await service._upsert_catalog_rows(
        source_type=ModelSourceType.MANUAL_CUSTOM,
        source_name="Manual source",
        source_id=source.id,
        organization_scoped=True,
        models=[
            {
                "model_provider": "openai",
                "model_name": "qwen-new",
                "metadata": {"slot": "new"},
            }
        ],
    )

    assert await service.get_default_model() is None
    assert await service.get_workspace_model_subset(workspace_id) == (
        WorkspaceModelSubsetRead(inherit_all=True, models=[])
    )
    assert [
        (row.source_id, row.model_provider, row.model_name)
        for row in await service._list_org_enabled_rows()
    ] == []


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_upsert_catalog_rows_invalidates_stale_preset_versions_and_sessions(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    service = AgentManagementService(session, role=svc_admin_role)
    workspace_id = svc_admin_role.workspace_id
    assert workspace_id is not None

    source = AgentModelSource(
        organization_id=svc_admin_role.organization_id,
        model_provider=CustomModelSourceType.MANUAL_CUSTOM.value,
        display_name="Manual source",
        declared_models=[],
    )
    session.add(source)
    await session.flush()

    stale_selection = ModelSelection(
        source_id=source.id,
        model_provider="openai",
        model_name="qwen-old",
    )
    preset = AgentPreset(
        workspace_id=workspace_id,
        name="Stale preset",
        slug="stale-preset",
        instructions="Use the source-backed model",
        model_name=stale_selection.model_name,
        model_provider=stale_selection.model_provider,
        source_id=stale_selection.source_id,
        base_url="https://source.example/v1",
        retries=3,
        enable_internet_access=False,
    )
    session.add(preset)
    await session.flush()
    version = AgentPresetVersion(
        workspace_id=workspace_id,
        preset_id=preset.id,
        version=1,
        instructions=preset.instructions,
        model_name=stale_selection.model_name,
        model_provider=stale_selection.model_provider,
        source_id=stale_selection.source_id,
        base_url=preset.base_url,
        retries=preset.retries,
        enable_internet_access=preset.enable_internet_access,
    )
    agent_session = AgentSession(
        workspace_id=workspace_id,
        title="Stale session",
        created_by=None,
        entity_type="case",
        entity_id=uuid.uuid4(),
        source_id=stale_selection.source_id,
        model_name=stale_selection.model_name,
        model_provider=stale_selection.model_provider,
    )
    session.add_all(
        [
            version,
            agent_session,
            AgentCatalog(
                organization_id=svc_admin_role.organization_id,
                source_id=source.id,
                model_provider=stale_selection.model_provider,
                model_name=stale_selection.model_name,
            ),
            AgentEnabledModel(
                organization_id=svc_admin_role.organization_id,
                workspace_id=None,
                source_id=source.id,
                model_provider=stale_selection.model_provider,
                model_name=stale_selection.model_name,
                enabled_config=None,
            ),
        ]
    )
    await session.flush()
    preset.current_version_id = version.id
    session.add(preset)
    await session.commit()

    await service._upsert_catalog_rows(
        source_type=ModelSourceType.MANUAL_CUSTOM,
        source_name="Manual source",
        source_id=source.id,
        organization_scoped=True,
        models=[
            {
                "model_provider": "openai",
                "model_name": "qwen-new",
                "metadata": {"slot": "new"},
            }
        ],
    )
    await session.commit()
    await session.refresh(preset)
    await session.refresh(version)
    await session.refresh(agent_session)

    assert preset.model_name == "qwen-old [unavailable]"
    assert preset.model_provider == stale_selection.model_provider
    assert preset.source_id is None
    assert preset.base_url is None
    assert version.model_name == "qwen-old [unavailable]"
    assert version.model_provider == stale_selection.model_provider
    assert version.source_id is None
    assert version.base_url is None
    assert agent_session.source_id is None
    assert agent_session.model_provider is None
    assert agent_session.model_name is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_upsert_builtin_catalog_rows_prunes_stale_workspace_subset_and_default(
    session: AsyncSession,
    svc_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AgentManagementService(session, role=svc_admin_role)
    workspace_id = svc_admin_role.workspace_id
    assert workspace_id is not None
    stale_selection = ModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-stale",
    )
    session.add_all(
        [
            AgentCatalog(
                id=uuid.uuid4(),
                organization_id=None,
                source_id=None,
                model_provider=stale_selection.model_provider,
                model_name=stale_selection.model_name,
            ),
            AgentEnabledModel(
                organization_id=svc_admin_role.organization_id,
                workspace_id=None,
                source_id=None,
                model_provider=stale_selection.model_provider,
                model_name=stale_selection.model_name,
                enabled_config=None,
            ),
        ]
    )
    await session.commit()
    await service.set_default_model_selection(stale_selection)
    await session.execute(
        insert(AgentEnabledModel).values(
            organization_id=svc_admin_role.organization_id,
            workspace_id=workspace_id,
            source_id=None,
            model_provider=stale_selection.model_provider,
            model_name=stale_selection.model_name,
            enabled_config=null(),
        )
    )
    await session.commit()

    monkeypatch.setattr(
        "tracecat.agent.service.get_builtin_catalog_models",
        lambda: [
            BuiltInCatalogModel(
                agent_catalog_id=uuid.uuid4(),
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

    assert await service.get_default_model() is None
    assert await service.get_workspace_model_subset(workspace_id) == (
        WorkspaceModelSubsetRead(inherit_all=True, models=[])
    )
    assert [
        (row.source_id, row.model_provider, row.model_name)
        for row in await service._list_org_enabled_rows()
    ] == []


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_disable_model_removes_workspace_subset_rows(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = AgentManagementService(session, role=svc_role)
    workspace_id = svc_role.workspace_id
    assert workspace_id is not None
    selection = ModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    session.add_all(
        [
            AgentCatalog(
                id=uuid.uuid4(),
                organization_id=None,
                source_id=None,
                model_provider=selection.model_provider,
                model_name=selection.model_name,
            ),
            AgentEnabledModel(
                organization_id=svc_role.organization_id,
                workspace_id=None,
                source_id=None,
                model_provider=selection.model_provider,
                model_name=selection.model_name,
                enabled_config=None,
            ),
        ]
    )
    await session.commit()
    await session.execute(
        insert(AgentEnabledModel).values(
            organization_id=svc_role.organization_id,
            workspace_id=workspace_id,
            source_id=None,
            model_provider=selection.model_provider,
            model_name=selection.model_name,
            enabled_config=null(),
        )
    )
    await session.commit()

    await service.disable_model(selection)

    assert await service.get_workspace_model_subset(workspace_id) == (
        WorkspaceModelSubsetRead(inherit_all=True, models=[])
    )
    assert await service._list_org_enabled_rows() == []


@pytest.mark.anyio
async def test_resolve_preset_selection_rejects_disabled_catalog_selection(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
    )
    service.is_model_enabled = AsyncMock(return_value=False)
    service._get_catalog_row_model = AsyncMock(return_value=Mock())

    with pytest.raises(
        TracecatNotFoundError,
        match="Model openai/gpt-5.2 is not enabled",
    ):
        await service._resolve_preset_selection(config)


@pytest.mark.anyio
async def test_resolve_preset_selection_does_not_fallback_when_source_backed_model_is_missing(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    config = AgentConfig(
        source_id=uuid.uuid4(),
        model_name="gpt-5.2",
        model_provider="openai",
    )
    service.is_model_enabled = AsyncMock(return_value=False)
    service._get_catalog_row_model = AsyncMock(
        side_effect=TracecatNotFoundError("missing")
    )
    monkeypatch.setattr(
        "tracecat.agent.service.resolve_enabled_catalog_match_for_provider_model",
        AsyncMock(side_effect=AssertionError("legacy fallback should not run")),
    )

    with pytest.raises(
        TracecatNotFoundError,
        match="Source-backed model selection openai/gpt-5.2 is no longer available",
    ):
        await service._resolve_preset_selection(config)


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_upsert_catalog_rows_keeps_duplicate_model_names_across_providers(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = AgentManagementService(session, role=svc_role)
    source = AgentModelSource(
        organization_id=svc_role.organization_id,
        model_provider=CustomModelSourceType.MANUAL_CUSTOM.value,
        display_name="Manual source",
        declared_models=[],
    )
    session.add(source)
    await session.flush()
    session.add_all(
        [
            AgentCatalog(
                organization_id=svc_role.organization_id,
                source_id=source.id,
                model_provider="openai",
                model_name="shared-model",
            ),
            AgentCatalog(
                organization_id=svc_role.organization_id,
                source_id=source.id,
                model_provider="anthropic",
                model_name="shared-model",
            ),
        ]
    )
    await session.commit()

    await service._upsert_catalog_rows(
        source_type=ModelSourceType.MANUAL_CUSTOM,
        source_name="Manual source",
        source_id=source.id,
        organization_scoped=True,
        models=[
            {
                "model_provider": "openai",
                "model_name": "shared-model",
                "metadata": {"slot": "openai"},
            },
            {
                "model_provider": "anthropic",
                "model_name": "shared-model",
                "metadata": {"slot": "anthropic"},
            },
        ],
    )

    rows = list(
        (
            await session.execute(
                select(AgentCatalog).where(
                    AgentCatalog.organization_id == svc_role.organization_id,
                    AgentCatalog.source_id == source.id,
                    AgentCatalog.model_name == "shared-model",
                )
            )
        )
        .scalars()
        .all()
    )

    assert {(row.model_provider, row.model_name) for row in rows} == {
        ("anthropic", "shared-model"),
        ("openai", "shared-model"),
    }
