"""Focused tests for AgentSelectionsService (workspace subsets, enable/disable,
defaults, ensure-defaults, and pruning flows).

Migrated from the selections-related tests in the former
test_agent_management_service.py, updated for the AgentCatalog +
AgentModelSelectionLink schema.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.builtin_catalog import BuiltInCatalogModel
from tracecat.agent.schemas import (
    DefaultModelSelection,
    EnabledModelRuntimeConfig,
    EnabledModelRuntimeConfigUpdate,
    ModelSelection,
    WorkspaceModelSubsetRead,
    WorkspaceModelSubsetUpdate,
)
from tracecat.agent.selections.service import (
    ENABLE_ALL_MODELS_ON_UPGRADE_SETTING,
    AgentSelectionsService,
)
from tracecat.agent.types import ModelSourceType
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentModelSelectionLink,
    AgentSource,
    OrganizationSetting,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ===========================================================================
# 1. Workspace subset tests (mock-based)
# ===========================================================================


@pytest.mark.anyio
async def test_get_workspace_model_subset_inherits_when_no_workspace_rows(
    role: Role,
) -> None:
    """When no workspace-scoped links exist the subset should report inherit_all."""
    service = AgentSelectionsService(AsyncMock(), role=role)
    workspace_id = uuid.uuid4()
    service._get_workspace = AsyncMock()
    service._list_selection_links = AsyncMock(return_value=[])

    subset = await service.get_workspace_model_subset(workspace_id)

    assert subset == WorkspaceModelSubsetRead(inherit_all=True, models=[])


@pytest.mark.anyio
async def test_replace_workspace_model_subset_rejects_explicit_empty(
    role: Role,
) -> None:
    """Setting inherit_all=False with an empty model list must raise."""
    session = AsyncMock()
    service = AgentSelectionsService(session, role=role)
    workspace_id = uuid.uuid4()
    service._get_workspace = AsyncMock()

    with pytest.raises(
        ValueError,
        match="Workspace subsets must include at least one model when inherit_all is false.",
    ):
        await service.replace_workspace_model_subset(
            workspace_id,
            WorkspaceModelSubsetUpdate(inherit_all=False, models=[]),
        )


# ===========================================================================
# 2. Enable / disable tests
# ===========================================================================


@pytest.mark.anyio
async def test_update_enabled_model_config_uses_composite_selection(
    role: Role,
) -> None:
    """update_enabled_model_config should resolve the catalog row via
    CatalogSelectionLookup, update the org-level selection link, and return
    a ModelCatalogEntry."""
    session = AsyncMock()
    session.add = Mock()
    service = AgentSelectionsService(session, role=role)

    catalog = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=None,
        source_id=None,
        model_provider="bedrock",
        model_name="anthropic.claude-sonnet-4-6",
        model_metadata=None,
    )
    link = AgentModelSelectionLink(
        id=uuid.uuid4(),
        organization_id=role.organization_id,
        workspace_id=None,
        catalog_id=catalog.id,
        enabled_config=None,
    )

    service._resolve_catalog_row = AsyncMock(return_value=catalog)
    service._get_org_selection_link = AsyncMock(return_value=link)
    service._load_sources_by_id = AsyncMock(return_value={})
    # has_entitlement check
    service.has_entitlement = AsyncMock(return_value=True)

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

    assert link.enabled_config == {"bedrock_inference_profile_id": "profile-123"}
    session.commit.assert_awaited_once()
    assert result.model_provider == "bedrock"
    assert result.model_name == "anthropic.claude-sonnet-4-6"
    assert result.enabled is True
    assert result.enabled_config is not None
    assert result.enabled_config.bedrock_inference_profile_id == "profile-123"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_disable_model_removes_workspace_subset_rows(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Disabling an org-level model must also remove workspace subset links
    and leave the workspace in inherit_all state."""
    service = AgentSelectionsService(session, role=svc_role)
    # Bypass entitlement check
    service.has_entitlement = AsyncMock(return_value=True)
    workspace_id = svc_role.workspace_id
    assert workspace_id is not None

    selection = ModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )

    # Create catalog row (builtin, organization_id=None)
    catalog = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=None,
        source_id=None,
        model_provider=selection.model_provider,
        model_name=selection.model_name,
    )
    session.add(catalog)
    await session.flush()

    # Create org-level selection link
    org_link = AgentModelSelectionLink(
        organization_id=svc_role.organization_id,
        workspace_id=None,
        catalog_id=catalog.id,
        enabled_config=None,
    )
    session.add(org_link)
    await session.flush()

    # Create workspace-level selection link
    ws_link = AgentModelSelectionLink(
        organization_id=svc_role.organization_id,
        workspace_id=workspace_id,
        catalog_id=catalog.id,
    )
    session.add(ws_link)
    await session.commit()

    await service.disable_model(selection)

    assert await service.get_workspace_model_subset(workspace_id) == (
        WorkspaceModelSubsetRead(inherit_all=True, models=[])
    )
    # Org-level links should also be gone
    org_links = await service._list_selection_links(workspace_id=None)
    assert org_links == []


# ===========================================================================
# 3. Default model tests
# ===========================================================================


@pytest.mark.anyio
async def test_get_default_model_reads_composite_setting(role: Role) -> None:
    """When the default model setting is a composite dict, get_default_model
    should resolve it via the catalog and return a DefaultModelSelection."""
    service = AgentSelectionsService(AsyncMock(), role=role)
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

    catalog = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    service._resolve_catalog_row = AsyncMock(return_value=catalog)
    service._load_sources_by_id = AsyncMock(return_value={})

    default_model = await service.get_default_model()

    assert default_model == DefaultModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
        source_type=ModelSourceType.OPENAI,
        source_name="OpenAI",
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_set_default_model_selection_persists_selection_ref(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    """set_default_model_selection must persist a JSON ref in the
    'agent_default_model_ref' org setting."""
    service = AgentSelectionsService(session, role=svc_admin_role)
    service.has_entitlement = AsyncMock(return_value=True)

    selection = ModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )

    # Create catalog + org-level selection link
    catalog = AgentCatalog(
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    session.add(catalog)
    await session.flush()

    org_link = AgentModelSelectionLink(
        organization_id=svc_admin_role.organization_id,
        workspace_id=None,
        catalog_id=catalog.id,
        enabled_config=None,
    )
    session.add(org_link)
    await session.commit()

    await service.set_default_model_selection(selection)

    ref_setting = (
        await session.execute(
            select(OrganizationSetting).where(
                OrganizationSetting.organization_id == svc_admin_role.organization_id,
                OrganizationSetting.key == "agent_default_model_ref",
            )
        )
    ).scalar_one()
    ref_payload = orjson.loads(orjson.loads(ref_setting.value))
    assert ref_payload == {
        "source_id": None,
        "model_provider": "openai",
        "model_name": "gpt-5.2",
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_get_default_model_prefers_selection_ref_for_legacy_string_duplicates(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    """When the stored default is a legacy string and multiple catalog rows share
    the same model_name, the ref selection should break the tie."""
    service = AgentSelectionsService(session, role=svc_admin_role)
    service.has_entitlement = AsyncMock(return_value=True)

    source = AgentSource(
        organization_id=svc_admin_role.organization_id,
        model_provider="manual_custom",
        display_name="Manual source",
        declared_models=[],
    )
    session.add(source)
    await session.flush()

    selection = ModelSelection(
        source_id=source.id,
        model_provider="openai",
        model_name="shared-model",
    )

    # Two catalog rows with the same model_name: builtin and source-backed
    builtin_catalog = AgentCatalog(
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="shared-model",
    )
    source_catalog = AgentCatalog(
        organization_id=svc_admin_role.organization_id,
        source_id=source.id,
        model_provider="openai",
        model_name="shared-model",
    )
    session.add_all([builtin_catalog, source_catalog])
    await session.flush()

    # Org-level selection links for both
    session.add_all(
        [
            AgentModelSelectionLink(
                organization_id=svc_admin_role.organization_id,
                workspace_id=None,
                catalog_id=builtin_catalog.id,
                enabled_config=None,
            ),
            AgentModelSelectionLink(
                organization_id=svc_admin_role.organization_id,
                workspace_id=None,
                catalog_id=source_catalog.id,
                enabled_config=None,
            ),
        ]
    )
    await session.commit()

    # Set the default via the service (this sets the ref properly)
    await service.set_default_model_selection(selection)

    # Now downgrade the stored setting to a legacy bare string
    default_setting = (
        await session.execute(
            select(OrganizationSetting).where(
                OrganizationSetting.organization_id == svc_admin_role.organization_id,
                OrganizationSetting.key == "agent_default_model",
            )
        )
    ).scalar_one()
    default_setting.value = orjson.dumps("shared-model")
    default_setting.is_encrypted = False
    await session.commit()

    default_model = await service.get_default_model()

    assert default_model == DefaultModelSelection(
        source_id=source.id,
        model_provider="openai",
        model_name="shared-model",
        source_type=ModelSourceType.MANUAL_CUSTOM,
        source_name="Manual source",
    )


@pytest.mark.anyio
async def test_get_default_model_uses_internal_persist_path_for_legacy_selection(
    role: Role,
) -> None:
    """When get_default_model encounters a legacy string setting but has a
    ref selection, it should use _persist_default_model_selection (the internal
    helper) rather than the public setter."""
    service = AgentSelectionsService(AsyncMock(), role=role)
    selection = ModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5",
    )
    service.settings_service = Mock(
        get_org_setting=AsyncMock(return_value=object()),
        get_value=Mock(return_value="gpt-5"),
    )
    service._get_default_model_ref_selection = AsyncMock(return_value=selection)
    service.require_enabled_model_selection = AsyncMock()
    service._persist_default_model_selection = AsyncMock()
    service.set_default_model_selection = AsyncMock(
        side_effect=AssertionError("public setter should not be called from read path")
    )

    catalog = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-5",
    )
    service._resolve_catalog_row = AsyncMock(return_value=catalog)
    service._load_sources_by_id = AsyncMock(return_value={})

    result = await service.get_default_model()

    assert result is not None
    assert result.model_provider == "openai"
    service._persist_default_model_selection.assert_awaited_once_with(selection)


# ===========================================================================
# 4. Ensure default enabled models tests (mock-based)
# ===========================================================================


@pytest.mark.anyio
async def test_ensure_default_enabled_models_defers_when_catalog_is_empty(
    role: Role,
) -> None:
    """When the catalog query returns no rows, no links should be created and
    the upgrade setting should NOT be consumed."""
    session = AsyncMock()
    select_result = Mock()
    select_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=select_result)

    service = AgentSelectionsService(session, role=role)
    upgrade_setting = object()
    service.settings_service.get_org_setting = AsyncMock(return_value=upgrade_setting)
    service.settings_service.delete_org_setting = AsyncMock()

    await service.ensure_default_enabled_models()

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
    """Only catalog rows whose provider is credentialed AND whose
    (provider, model_name) is in the builtin enableable set should be
    enabled. Source-backed rows are always eligible."""
    session = AsyncMock()
    catalog_rows = [
        type(
            "CatalogRow",
            (),
            {
                "id": uuid.uuid4(),
                "source_id": None,
                "model_provider": "openai",
                "model_name": "gpt-5.2",
            },
        )(),
        type(
            "CatalogRow",
            (),
            {
                "id": uuid.uuid4(),
                "source_id": None,
                "model_provider": "anthropic",
                "model_name": "claude-sonnet-4-5",
            },
        )(),
        type(
            "CatalogRow",
            (),
            {
                "id": uuid.uuid4(),
                "source_id": None,
                "model_provider": "openai",
                "model_name": "text-embedding-3-large",
            },
        )(),
        type(
            "CatalogRow",
            (),
            {
                "id": uuid.uuid4(),
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

    service = AgentSelectionsService(session, role=role)
    upgrade_setting = object()
    service.settings_service.get_org_setting = AsyncMock(return_value=upgrade_setting)
    service.settings_service.delete_org_setting = AsyncMock()
    monkeypatch.setattr(
        "tracecat.agent.selections.service.get_builtin_catalog_models",
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
    service._load_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "sk-test"} if provider == "openai" else None
        )
    )

    await service.ensure_default_enabled_models()

    service.settings_service.get_org_setting.assert_awaited_once_with(
        ENABLE_ALL_MODELS_ON_UPGRADE_SETTING
    )
    service.settings_service.delete_org_setting.assert_awaited_once_with(
        upgrade_setting
    )
    assert session.execute.await_count == 2
    insert_stmt = session.execute.await_args_list[1].args[0]
    compiled_params = insert_stmt.compile().params
    # The openai/gpt-5.2 row (enableable + credentialed) and the
    # source-backed qwen row (always eligible) should be included.
    enabled_catalog_ids = {
        compiled_params[k] for k in compiled_params if k.startswith("catalog_id")
    }
    assert catalog_rows[0].id in enabled_catalog_ids  # openai/gpt-5.2
    assert catalog_rows[3].id in enabled_catalog_ids  # source-backed qwen
    # anthropic (no credentials) and text-embedding (not enableable) excluded
    assert catalog_rows[1].id not in enabled_catalog_ids
    assert catalog_rows[2].id not in enabled_catalog_ids


@pytest.mark.anyio
async def test_ensure_default_enabled_models_defers_when_no_rows_are_eligible(
    role: Role,
) -> None:
    """When none of the catalog rows are eligible (no credentials), neither
    commit nor setting deletion should occur."""
    session = AsyncMock()
    catalog_rows = [
        type(
            "CatalogRow",
            (),
            {
                "id": uuid.uuid4(),
                "source_id": None,
                "model_provider": "openai",
                "model_name": "gpt-5.2",
            },
        )(),
        type(
            "CatalogRow",
            (),
            {
                "id": uuid.uuid4(),
                "source_id": None,
                "model_provider": "anthropic",
                "model_name": "claude-sonnet-4-5",
            },
        )(),
    ]
    select_result = Mock()
    select_result.scalars.return_value.all.return_value = catalog_rows
    session.execute = AsyncMock(return_value=select_result)

    service = AgentSelectionsService(session, role=role)
    upgrade_setting = object()
    service.settings_service.get_org_setting = AsyncMock(return_value=upgrade_setting)
    service.settings_service.delete_org_setting = AsyncMock()
    service._load_provider_credentials = AsyncMock(return_value=None)

    await service.ensure_default_enabled_models()

    service.settings_service.delete_org_setting.assert_not_called()
    session.commit.assert_not_awaited()
    session.execute.assert_awaited_once()


# ===========================================================================
# 5. Prune tests (mock-based)
# ===========================================================================


@pytest.mark.anyio
async def test_prune_unconfigured_builtin_model_selections_disables_only_disconnected_providers(
    role: Role,
) -> None:
    """Only builtin provider rows whose credentials are incomplete should be
    pruned.  Source-backed rows must be left untouched."""
    service = AgentSelectionsService(AsyncMock(), role=role)

    catalog_openai = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=None,
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    catalog_gemini = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=None,
        source_id=None,
        model_provider="gemini",
        model_name="gemini-2.5-pro",
    )
    source_id = uuid.uuid4()
    catalog_gateway = AgentCatalog(
        id=uuid.uuid4(),
        organization_id=role.organization_id,
        source_id=source_id,
        model_provider="openai_compatible_gateway",
        model_name="qwen2.5:7b",
    )

    link_openai = AgentModelSelectionLink(
        id=uuid.uuid4(),
        organization_id=role.organization_id,
        workspace_id=None,
        catalog_id=catalog_openai.id,
        enabled_config=None,
    )
    link_gemini = AgentModelSelectionLink(
        id=uuid.uuid4(),
        organization_id=role.organization_id,
        workspace_id=None,
        catalog_id=catalog_gemini.id,
        enabled_config=None,
    )
    link_gateway = AgentModelSelectionLink(
        id=uuid.uuid4(),
        organization_id=role.organization_id,
        workspace_id=None,
        catalog_id=catalog_gateway.id,
        enabled_config=None,
    )

    service._list_selection_links = AsyncMock(
        return_value=[
            (link_openai, catalog_openai, None),
            (link_gemini, catalog_gemini, None),
            (link_gateway, catalog_gateway, None),
        ]
    )
    service._load_provider_credentials = AsyncMock(
        side_effect=lambda provider: (
            {"OPENAI_API_KEY": "sk-test"} if provider == "openai" else None
        )
    )
    # _disable_catalog_ids should only receive the gemini catalog id
    service._disable_catalog_ids = AsyncMock(return_value={catalog_gemini.id})

    disabled = await service.prune_unconfigured_builtin_model_selections()

    service._disable_catalog_ids.assert_awaited_once_with([catalog_gemini.id])
    assert disabled == {catalog_gemini.id}


# ===========================================================================
# Dropped tests with brief rationale
# ===========================================================================

# test_filter_enabled_rows_for_workspace_inherits_when_no_workspace_rows (old L283):
#   The new service does not expose _filter_enabled_rows_for_workspace.
#   Workspace filtering is handled internally by _list_selection_links and
#   _workspace_subset_exists. The get_workspace_model_subset test above covers
#   the observable behavior.

# test_prune_stale_model_selections_only_invalidates_deleted_rows (old L833):
#   The old service had a _prune_stale_model_selections method that accepted
#   selection keys. The new service does not have this method in the same form;
#   stale selection pruning is now handled by _disable_catalog_ids which takes
#   catalog UUIDs. The prune_unconfigured test above validates the equivalent
#   path via the public API.

# test_upsert_catalog_rows_prunes_stale_workspace_subset_and_default (old L1445)
# test_upsert_catalog_rows_invalidates_stale_preset_versions_and_sessions (old L1524):
#   Both of these test _upsert_catalog_rows which now lives in
#   tracecat.agent.sources.service. They belong in test_api_agent_sources.py.
