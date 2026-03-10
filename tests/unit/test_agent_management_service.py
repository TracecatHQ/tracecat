"""Tests for AgentManagementService credential context behavior."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql
from tracecat_registry._internal import secrets as registry_secrets

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import (
    DefaultModelSelection,
    EnabledModelRuntimeConfig,
    EnabledModelRuntimeConfigUpdate,
    EnabledModelsBatchOperation,
)
from tracecat.agent.service import (
    AgentManagementService,
    ResolvedCatalogRecord,
    sync_model_catalogs_on_startup,
)
from tracecat.agent.types import AgentConfig, ModelDiscoveryStatus, ModelSourceType
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError
from tracecat.secrets import secrets_manager


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:read", "agent:update", "org:secret:read"}),
    )


@pytest.mark.anyio
async def test_with_model_config_sets_registry_and_env_context(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.get_default_model = AsyncMock(
        return_value=DefaultModelSelection(
            catalog_ref="anthropic:deployment:test:claude-opus-4-5-20251101",
            model_name="claude-opus-4-5-20251101",
            model_provider="anthropic",
            display_name="Claude Opus 4.5",
        )
    )
    service._resolve_catalog_agent_config = AsyncMock(
        return_value=(
            AgentConfig(
                model_name="claude-opus-4-5-20251101",
                model_provider="anthropic",
                model_catalog_ref="anthropic:deployment:test:claude-opus-4-5-20251101",
            ),
            {"ANTHROPIC_API_KEY": "test-key"},
        )
    )

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_model_config():
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "test-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "test-key"

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None


@pytest.mark.anyio
async def test_sync_model_catalogs_on_startup_refreshes_sidecar_and_sources() -> None:
    session = AsyncMock()
    org_ids = [uuid.uuid4(), uuid.uuid4()]
    refreshed_sources_by_org: dict[uuid.UUID, list[uuid.UUID]] = {
        org_ids[0]: [uuid.uuid4()],
        org_ids[1]: [uuid.uuid4(), uuid.uuid4()],
    }
    observed_roles: list[uuid.UUID] = []

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
            return SimpleNamespace(models=[])

        async def refresh_default_sidecar_inventory(self, *, populate_defaults: bool):
            assert populate_defaults is True
            return SimpleNamespace(discovered_models=[])

        async def _ensure_default_enabled_models(self) -> None:
            return None

        async def check_provider_credentials(self, provider: str) -> bool:
            del provider
            return False

        async def list_model_sources(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(id=source_id)
                for source_id in refreshed_sources_by_org[self.organization_id]
            ]

        async def refresh_model_source(self, source_id: uuid.UUID) -> list[object]:
            assert source_id in refreshed_sources_by_org[self.organization_id]
            return []

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "tracecat.agent.service.get_async_session_context_manager",
        session_cm,
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
    unlock.assert_awaited_once()


@pytest.mark.anyio
async def test_sync_model_catalogs_on_startup_refreshes_configured_builtin_providers_only() -> (
    None
):
    session = AsyncMock()
    org_id = uuid.uuid4()
    checked_providers: list[str] = []
    refreshed_providers: list[str] = []

    @asynccontextmanager
    async def session_cm():
        yield session

    class FakeAgentManagementService:
        def __init__(self, _session: AsyncMock, role: Role) -> None:
            assert role.organization_id == org_id

        async def refresh_builtin_catalog(self):
            return SimpleNamespace(models=[])

        async def refresh_default_sidecar_inventory(self, *, populate_defaults: bool):
            assert populate_defaults is True
            return SimpleNamespace(discovered_models=[])

        async def _ensure_default_enabled_models(self) -> None:
            return None

        async def check_provider_credentials(self, provider: str) -> bool:
            checked_providers.append(provider)
            return provider in {"openai", "vertex_ai"}

        async def refresh_provider_inventory(self, provider: str):
            refreshed_providers.append(provider)
            return SimpleNamespace(discovered_models=[])

        async def list_model_sources(self) -> list[SimpleNamespace]:
            return []

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "tracecat.agent.service.get_async_session_context_manager",
        session_cm,
    )
    monkeypatch.setattr(
        "tracecat.agent.service.try_pg_advisory_lock",
        AsyncMock(return_value=True),
    )
    unlock = AsyncMock()
    monkeypatch.setattr("tracecat.agent.service.pg_advisory_unlock", unlock)
    monkeypatch.setattr(
        "tracecat.agent.service._list_active_organization_ids",
        AsyncMock(return_value=[org_id]),
    )
    monkeypatch.setattr(
        "tracecat.agent.service.AgentManagementService",
        FakeAgentManagementService,
    )
    try:
        await sync_model_catalogs_on_startup()
    finally:
        monkeypatch.undo()

    assert "openai" in checked_providers
    assert "vertex_ai" in checked_providers
    assert refreshed_providers == ["openai", "vertex_ai"]
    unlock.assert_awaited_once()


@pytest.mark.anyio
async def test_upsert_discovered_models_uses_postgres_upsert_and_dedupes_refs(
    role: Role,
) -> None:
    session = AsyncMock()
    execute_calls: list[object] = []

    def result_with_scalars(items: list[object]) -> SimpleNamespace:
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: items),
        )

    async def execute_side_effect(stmt: object) -> object:
        execute_calls.append(stmt)
        match len(execute_calls):
            case 1:
                return result_with_scalars(["stale-ref"])
            case 2 | 3 | 4:
                return SimpleNamespace()
            case 5:
                return result_with_scalars(
                    [
                        SimpleNamespace(catalog_ref="dup-ref"),
                        SimpleNamespace(catalog_ref="other-ref"),
                    ]
                )
            case _:
                raise AssertionError("Unexpected execute call")

    session.execute = AsyncMock(side_effect=execute_side_effect)
    service = AgentManagementService(session, role=role)

    persisted = await service._upsert_discovered_models(
        source_type=ModelSourceType.DEFAULT_SIDECAR,
        source_name="Default models",
        source_id=None,
        models=[
            {
                "catalog_ref": "dup-ref",
                "model_name": "gpt-4o-mini",
                "model_provider": "openai",
                "runtime_provider": "default_sidecar",
                "display_name": "gpt-4o-mini",
                "raw_model_id": "gpt-4o-mini",
                "base_url": None,
                "metadata": {"seed": "litellm_config"},
            },
            {
                "catalog_ref": "dup-ref",
                "model_name": "gpt-4o-mini",
                "model_provider": "openai",
                "runtime_provider": "default_sidecar",
                "display_name": "gpt-4o-mini",
                "raw_model_id": "gpt-4o-mini",
                "base_url": None,
                "metadata": {"seed": "litellm_config", "duplicate": True},
            },
            {
                "catalog_ref": "other-ref",
                "model_name": "gpt-5",
                "model_provider": "openai",
                "runtime_provider": "default_sidecar",
                "display_name": "gpt-5",
                "raw_model_id": "gpt-5",
                "base_url": None,
                "metadata": {"seed": "litellm_config"},
            },
        ],
        organization_scoped=False,
    )

    compiled = execute_calls[3].compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled)
    compiled_values = list(compiled.params.values())

    assert "ON CONFLICT (catalog_ref) DO UPDATE" in compiled_sql
    assert compiled_values.count("dup-ref") == 1
    assert compiled_values.count("other-ref") == 1
    assert [row.catalog_ref for row in persisted] == ["dup-ref", "other-ref"]


@pytest.mark.anyio
async def test_refresh_default_sidecar_inventory_rolls_back_before_failure_state_update(
    role: Role,
) -> None:
    session = AsyncMock()
    service = AgentManagementService(session, role=role)
    service._list_default_sidecar_rows = AsyncMock(return_value=[])
    service._get_default_sidecar_config = lambda: {
        "base_url": "http://gateway.local/v1",
        "api_key": None,
        "api_key_header": None,
    }
    service._fetch_openai_compatible_models = AsyncMock(
        return_value=[{"id": "gpt-4o-mini", "owned_by": "openai"}]
    )
    service._normalize_openai_compatible_entries = lambda **_: [
        {
            "catalog_ref": "dup-ref",
            "model_name": "gpt-4o-mini",
            "model_provider": "openai",
            "runtime_provider": "default_sidecar",
            "display_name": "gpt-4o-mini",
            "raw_model_id": "gpt-4o-mini",
            "base_url": "http://gateway.local/v1",
            "metadata": {"source": "live-discovery"},
        }
    ]
    service._upsert_discovered_models = AsyncMock(side_effect=RuntimeError("boom"))

    async def assert_rollback_happened(**_: object) -> None:
        assert session.rollback.await_count == 1

    service._set_platform_setting_value = AsyncMock(
        side_effect=assert_rollback_happened
    )
    service.get_default_sidecar_inventory = AsyncMock(
        return_value=SimpleNamespace(discovered_models=[])
    )

    result = await service.refresh_default_sidecar_inventory(populate_defaults=True)

    assert result.discovered_models == []
    session.rollback.assert_awaited_once()
    assert service._set_platform_setting_value.await_count == 3
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_enable_models_uses_single_postgres_insert_and_dedupes_refs(
    role: Role,
) -> None:
    session = AsyncMock()
    execute_calls: list[object] = []

    async def execute_side_effect(stmt: object) -> object:
        execute_calls.append(stmt)
        return SimpleNamespace()

    session.execute = AsyncMock(side_effect=execute_side_effect)
    service = AgentManagementService(session, role=role)
    rows_by_ref = {
        "openai:gpt-5": ResolvedCatalogRecord(
            catalog_ref="openai:gpt-5",
            model_name="gpt-5",
            model_provider="openai",
            runtime_provider="openai",
            display_name="GPT-5",
            source_type=ModelSourceType.OPENAI,
            source_name="OpenAI",
            source_id=None,
            base_url=None,
            last_refreshed_at=None,
            metadata=None,
        ),
        "openai:gpt-5-mini": ResolvedCatalogRecord(
            catalog_ref="openai:gpt-5-mini",
            model_name="gpt-5-mini",
            model_provider="openai",
            runtime_provider="openai",
            display_name="GPT-5 mini",
            source_type=ModelSourceType.OPENAI,
            source_name="OpenAI",
            source_id=None,
            base_url=None,
            last_refreshed_at=None,
            metadata=None,
        ),
    }
    service._resolve_enableable_catalog_row = AsyncMock(
        side_effect=lambda catalog_ref, _cache: rows_by_ref[catalog_ref]
    )

    enabled = await service.enable_models(
        EnabledModelsBatchOperation(
            catalog_refs=["openai:gpt-5", "openai:gpt-5", "openai:gpt-5-mini"]
        )
    )

    compiled = execute_calls[0].compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled)
    compiled_values = list(compiled.params.values())

    assert "ON CONFLICT (organization_id, catalog_ref) DO NOTHING" in compiled_sql
    assert compiled_values.count("openai:gpt-5") == 1
    assert compiled_values.count("openai:gpt-5-mini") == 1
    assert [item.catalog_ref for item in enabled] == [
        "openai:gpt-5",
        "openai:gpt-5-mini",
    ]
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_update_enabled_model_config_persists_bedrock_profile(
    role: Role,
) -> None:
    session = AsyncMock()
    session.add = Mock()
    service = AgentManagementService(session, role=role)
    enabled_row = SimpleNamespace(
        catalog_ref="bedrock:anthropic.claude-sonnet-4-6",
        model_name="anthropic.claude-sonnet-4-6",
        model_provider="anthropic",
        runtime_provider="bedrock",
        display_name="Claude Sonnet 4.6",
        source_type=ModelSourceType.BEDROCK.value,
        source_id=None,
        base_url=None,
        updated_at=None,
        enabled_config=None,
    )
    service._get_enabled_row = AsyncMock(return_value=enabled_row)

    updated = await service.update_enabled_model_config(
        EnabledModelRuntimeConfigUpdate(
            catalog_ref=enabled_row.catalog_ref,
            config=EnabledModelRuntimeConfig(
                bedrock_inference_profile_id=(
                    "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test"
                )
            ),
        )
    )

    assert enabled_row.enabled_config == {
        "bedrock_inference_profile_id": (
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test"
        )
    }
    assert (
        updated.enabled_config is not None
        and updated.enabled_config.bedrock_inference_profile_id
        == "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test"
    )
    session.add.assert_called_once_with(enabled_row)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(enabled_row)


@pytest.mark.anyio
async def test_get_runtime_credentials_for_catalog_ref_prefers_enabled_bedrock_profile(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service._get_catalog_row = AsyncMock(
        return_value=ResolvedCatalogRecord(
            catalog_ref="bedrock:anthropic.claude-sonnet-4-6",
            model_name="anthropic.claude-sonnet-4-6",
            model_provider="anthropic",
            runtime_provider="bedrock",
            display_name="Claude Sonnet 4.6",
            source_type=ModelSourceType.BEDROCK,
            source_name="Amazon Bedrock",
            source_id=None,
            base_url=None,
            last_refreshed_at=None,
            metadata=None,
        )
    )
    service.get_provider_credentials = AsyncMock(
        return_value={
            "AWS_REGION": "us-east-1",
            "AWS_INFERENCE_PROFILE_ID": "provider-default-profile",
        }
    )
    service._get_enabled_row = AsyncMock(
        return_value=SimpleNamespace(
            runtime_provider="bedrock",
            enabled_config={"bedrock_inference_profile_id": "model-specific-profile"},
        )
    )

    credentials = await service.get_runtime_credentials_for_catalog_ref(
        catalog_ref="bedrock:anthropic.claude-sonnet-4-6"
    )

    assert credentials["AWS_REGION"] == "us-east-1"
    assert credentials["AWS_INFERENCE_PROFILE_ID"] == "model-specific-profile"


@pytest.mark.anyio
async def test_list_models_does_not_refresh_default_inventory(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    discovered_row = SimpleNamespace(
        catalog_ref="default_sidecar:default:abc123:gpt-5",
        model_name="gpt-5",
        model_provider="openai",
        runtime_provider="default_sidecar",
        display_name="GPT-5",
        source_type=ModelSourceType.DEFAULT_SIDECAR.value,
        source_name="Default models",
        source_id=None,
        base_url=None,
        last_refreshed_at=None,
        model_metadata=None,
    )
    service.refresh_default_sidecar_inventory = AsyncMock()
    service._ensure_default_enabled_models = AsyncMock()
    service._list_default_sidecar_rows = AsyncMock(return_value=[discovered_row])
    service._list_org_discovered_rows = AsyncMock(return_value=[])
    service._list_enabled_rows = AsyncMock(
        return_value=[
            SimpleNamespace(
                catalog_ref="default_sidecar:default:abc123:gpt-5",
                model_name="gpt-5",
                model_provider="openai",
                runtime_provider="default_sidecar",
                display_name="GPT-5",
                source_type=ModelSourceType.DEFAULT_SIDECAR.value,
                source_id=None,
                base_url=None,
                updated_at=None,
                enabled_config=None,
            )
        ]
    )

    models = await service.list_models()

    assert [model.catalog_ref for model in models] == [discovered_row.catalog_ref]
    service.refresh_default_sidecar_inventory.assert_not_awaited()
    service._ensure_default_enabled_models.assert_not_awaited()


@pytest.mark.anyio
async def test_list_models_filters_enabled_catalog_by_workspace_allowlist(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service._list_enabled_rows = AsyncMock(
        return_value=[
            SimpleNamespace(
                catalog_ref="builtin:openai:1:gpt-5",
                model_name="gpt-5",
                model_provider="openai",
                runtime_provider="openai",
                display_name="GPT-5",
                source_type=ModelSourceType.OPENAI.value,
                source_id=None,
                base_url=None,
                updated_at=None,
                enabled_config=None,
            ),
            SimpleNamespace(
                catalog_ref="builtin:anthropic:2:claude",
                model_name="claude-sonnet-4-5-20250929",
                model_provider="anthropic",
                runtime_provider="anthropic",
                display_name="Claude Sonnet 4.5",
                source_type=ModelSourceType.ANTHROPIC.value,
                source_id=None,
                base_url=None,
                updated_at=None,
                enabled_config=None,
            ),
        ]
    )
    service._get_workspace_enabled_model_refs = AsyncMock(
        return_value={"builtin:anthropic:2:claude"}
    )

    models = await service.list_models(workspace_id=uuid.uuid4())

    assert [model.catalog_ref for model in models] == ["builtin:anthropic:2:claude"]


@pytest.mark.anyio
async def test_list_discovered_models_does_not_refresh_default_inventory(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    discovered_row = SimpleNamespace(
        catalog_ref="default_sidecar:default:def456:gpt-5-mini",
        model_name="gpt-5-mini",
        model_provider="openai",
        runtime_provider="default_sidecar",
        display_name="GPT-5 mini",
        source_type=ModelSourceType.DEFAULT_SIDECAR.value,
        source_name="Default models",
        source_id=None,
        base_url=None,
        last_refreshed_at=None,
        model_metadata=None,
    )
    service.refresh_default_sidecar_inventory = AsyncMock()
    service._list_default_sidecar_rows = AsyncMock(return_value=[discovered_row])
    service._list_org_discovered_rows = AsyncMock(return_value=[])
    service._list_enabled_rows = AsyncMock(return_value=[])

    models = await service.list_discovered_models()

    assert [model.catalog_ref for model in models] == [discovered_row.catalog_ref]
    assert models[0].enabled is False
    service.refresh_default_sidecar_inventory.assert_not_awaited()


@pytest.mark.anyio
async def test_list_builtin_catalog_includes_provider_credentials_and_filtering(
    role: Role,
) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service._get_builtin_catalog_state = AsyncMock(
        return_value=(ModelDiscoveryStatus.READY, None, None)
    )
    service._list_enabled_rows = AsyncMock(return_value=[])
    service._list_org_discovered_rows = AsyncMock(return_value=[])

    async def provider_credentials(provider: str):
        if provider == "openai":
            return {"OPENAI_API_KEY": "test-key"}
        return None

    service.get_provider_credentials = AsyncMock(side_effect=provider_credentials)

    inventory = await service.list_builtin_catalog(
        provider="openai",
        query="gpt",
        limit=50,
    )

    assert inventory.models
    assert len(inventory.models) <= 50
    assert all(model.runtime_provider == "openai" for model in inventory.models)
    openai_row = next(
        model
        for model in inventory.models
        if model.runtime_provider == "openai" and model.enableable
    )
    assert openai_row.credential_provider == "openai"
    assert openai_row.credentials_configured is True
    assert openai_row.enableable is True


@pytest.mark.anyio
async def test_ensure_default_enabled_models_uses_conflict_safe_bulk_insert(
    role: Role,
) -> None:
    session = AsyncMock()
    service = AgentManagementService(session, role=role)
    service._list_enabled_rows = AsyncMock(return_value=[])
    service._list_default_sidecar_rows = AsyncMock(
        return_value=[
            SimpleNamespace(
                catalog_ref="default_sidecar:default:abc123:gpt-5",
                source_id=None,
                source_type=ModelSourceType.DEFAULT_SIDECAR.value,
                model_name="gpt-5",
                model_provider="openai",
                runtime_provider="default_sidecar",
                display_name="GPT-5",
                base_url=None,
            )
        ]
    )

    await service._ensure_default_enabled_models()

    session.execute.assert_awaited_once()
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert "ON CONFLICT (organization_id, catalog_ref) DO NOTHING" in str(compiled)
    session.commit.assert_awaited_once()
    session.add.assert_not_called()


@pytest.mark.anyio
async def test_with_preset_config_sets_registry_and_env_context(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    service.presets = cast(
        AgentPresetService,
        SimpleNamespace(
            resolve_agent_preset_config=AsyncMock(
                return_value=AgentConfig(
                    model_name="claude-opus-4-5-20251101",
                    model_provider="anthropic",
                )
            )
        ),
    )
    service.get_provider_credentials = AsyncMock(
        return_value={"ANTHROPIC_API_KEY": "org-key"}
    )
    service._resolve_preset_catalog_ref = AsyncMock(return_value=None)

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None

    async with service.with_preset_config(preset_id=uuid.uuid4()):
        assert registry_secrets.get("ANTHROPIC_API_KEY") == "org-key"
        assert secrets_manager.get("ANTHROPIC_API_KEY") == "org-key"

    assert registry_secrets.get_or_default("ANTHROPIC_API_KEY") is None
    assert secrets_manager.get("ANTHROPIC_API_KEY") is None


@pytest.mark.anyio
async def test_with_preset_config_rejects_disabled_catalog_ref(role: Role) -> None:
    service = AgentManagementService(AsyncMock(), role=role)
    preset_catalog_ref = "anthropic:deployment:test:claude-opus-4-5-20251101"
    service.presets = cast(
        AgentPresetService,
        SimpleNamespace(
            resolve_agent_preset_config=AsyncMock(
                return_value=AgentConfig(
                    model_name="claude-opus-4-5-20251101",
                    model_provider="anthropic",
                    model_catalog_ref=preset_catalog_ref,
                )
            )
        ),
    )
    service.require_enabled_catalog_ref = AsyncMock(
        side_effect=TracecatNotFoundError("Model is not enabled")
    )
    service._resolve_catalog_agent_config = AsyncMock()
    service.get_provider_credentials = AsyncMock()

    with pytest.raises(TracecatNotFoundError, match="Model is not enabled"):
        async with service.with_preset_config(preset_id=uuid.uuid4()):
            pytest.fail("with_preset_config should not yield for disabled models")

    service.require_enabled_catalog_ref.assert_awaited_once_with(
        preset_catalog_ref,
        workspace_id=role.workspace_id,
    )
    service._resolve_catalog_agent_config.assert_not_awaited()
    service.get_provider_credentials.assert_not_awaited()


@pytest.mark.anyio
async def test_disable_models_deletes_in_batch_and_clears_default(role: Role) -> None:
    session = AsyncMock()
    execute_calls: list[object] = []
    default_setting = SimpleNamespace(value="provider:model-a")

    async def execute_side_effect(stmt: object) -> object:
        execute_calls.append(stmt)
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(
                all=lambda: ["provider:model-a", "provider:model-b"]
            )
        )

    session.execute = AsyncMock(side_effect=execute_side_effect)
    service = AgentManagementService(session, role=role)
    service.settings_service.get_org_setting = AsyncMock(return_value=default_setting)
    service.settings_service.get_value = lambda setting: cast(str, setting.value)
    service.settings_service.update_org_setting = AsyncMock()

    await service.disable_models(
        EnabledModelsBatchOperation(
            catalog_refs=["provider:model-a", "provider:model-b", "provider:model-a"]
        )
    )

    compiled = execute_calls[0].compile(dialect=postgresql.dialect())
    compiled_sql = str(compiled)
    compiled_values = list(compiled.params.values())

    assert "DELETE FROM agent_enabled_models" in compiled_sql
    assert "IN" in compiled_sql
    assert ["provider:model-a", "provider:model-b"] in compiled_values
    service.settings_service.update_org_setting.assert_awaited_once()
    session.commit.assert_awaited_once()
