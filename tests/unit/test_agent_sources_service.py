"""Tests for AgentSourceService from tracecat.agent.sources.service."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.schemas import (
    AgentModelSourceUpdate,
    ModelSelection,
)
from tracecat.agent.sources.service import AgentSourceService, SourceDiscoveryResult
from tracecat.agent.types import (
    CustomModelSourceType,
    ModelSourceType,
)
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentModelSelectionLink,
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
    AgentSource,
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


# ---------------------------------------------------------------------------
# Mock-based tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_openai_compatible_discovery_ignores_unsupported_provider_hints(
    role: Role,
) -> None:
    """_normalize_openai_compatible_entries should map unsupported owned_by
    values to the generic 'openai_compatible_gateway' provider while
    recognising known providers like 'anthropic'."""
    service = AgentSourceService(AsyncMock(), role=role)

    normalized = service._normalize_openai_compatible_entries(
        source_type=ModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
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
    """When the first discovery URL returns invalid JSON, the service should
    try the next candidate URL and succeed if it returns valid data."""
    service = AgentSourceService(AsyncMock(), role=role)

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

    monkeypatch.setattr("tracecat.agent.sources.service.httpx.AsyncClient", _FakeClient)

    discovery = await service._fetch_openai_compatible_models(
        base_url="https://gateway.example",
        api_key=None,
        api_key_header=None,
    )

    # The new SourceDiscoveryResult stores models as a list of dicts.
    assert [item["id"] for item in discovery.models] == [
        "qwen2.5:0.5b",
        "qwen2.5:1.5b",
    ]
    assert discovery.runtime_base_url == "https://gateway.example"


@pytest.mark.anyio
async def test_fetch_openai_compatible_models_sanitizes_logged_urls(
    role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When all discovery URLs fail, the warning log should not contain
    credentials or query parameters from the base URL."""
    service = AgentSourceService(AsyncMock(), role=role)
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

    monkeypatch.setattr(
        "tracecat.agent.sources.service.httpx.AsyncClient", _FailingClient
    )

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
async def test_refresh_model_source_retries_deferred_upgrade_enable_all(
    role: Role,
) -> None:
    """refresh_model_source should call
    self.selections.ensure_default_enabled_models() after upserting."""
    session = AsyncMock()
    service = AgentSourceService(session, role=role)
    source_id = uuid.uuid4()

    source = AgentSource(
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
    service.selections = Mock()
    service.selections.ensure_default_enabled_models = AsyncMock()
    service.selections._list_selection_links = AsyncMock(return_value=[])

    await service.refresh_model_source(source_id)

    service.selections.ensure_default_enabled_models.assert_awaited_once()


@pytest.mark.anyio
async def test_update_model_source_retries_deferred_upgrade_enable_all(
    role: Role,
) -> None:
    """update_model_source should call
    self.selections.ensure_default_enabled_models() after persisting."""
    session = AsyncMock()
    service = AgentSourceService(session, role=role)
    source_id = uuid.uuid4()

    source = AgentSource(
        id=source_id,
        organization_id=role.organization_id,
        model_provider=CustomModelSourceType.MANUAL_CUSTOM.value,
        display_name="Manual source",
        declared_models=[],
        encrypted_config=b"{}",
    )

    service.get_model_source = AsyncMock(return_value=source)
    service.selections = Mock()
    service.selections.ensure_default_enabled_models = AsyncMock()

    updated = await service.update_model_source(
        source_id,
        AgentModelSourceUpdate(),
    )

    assert updated.id == source_id
    service.selections.ensure_default_enabled_models.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_delete_model_source_skips_enabled_model_delete_when_catalog_is_empty(
    role: Role,
) -> None:
    """When there are no catalog rows for the source, delete_model_source
    should not call any invalidation methods -- it should simply delete
    the source row."""
    session = AsyncMock()
    select_result = Mock()
    select_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=select_result)
    source = Mock()

    service = AgentSourceService(session, role=role)
    service.get_model_source = AsyncMock(return_value=source)
    service.selections = Mock()
    service.selections._invalidate_disabled_dependents = AsyncMock()
    service.selections._revalidate_default_model_setting = AsyncMock()

    await service.delete_model_source(uuid.uuid4())

    # With no catalog rows, the selections invalidation should not be called.
    service.selections._invalidate_disabled_dependents.assert_not_awaited()
    service.selections._revalidate_default_model_setting.assert_not_awaited()
    session.delete.assert_awaited_once_with(source)
    session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_delete_model_source_clears_default_model_selection(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    """When a source with an org-level default is deleted, the default model
    setting should be cleared."""
    from tracecat.agent.selections.service import AgentSelectionsService

    source_service = AgentSourceService(session, role=svc_admin_role)
    selections_service = AgentSelectionsService(session, role=svc_admin_role)

    source = AgentSource(
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

    # Create a catalog row for the source-backed model.
    catalog = AgentCatalog(
        organization_id=svc_admin_role.organization_id,
        source_id=source.id,
        model_provider=selection.model_provider,
        model_name=selection.model_name,
    )
    session.add(catalog)
    await session.flush()

    # Create an org-level selection link for the catalog row.
    session.add(
        AgentModelSelectionLink(
            organization_id=svc_admin_role.organization_id,
            workspace_id=None,
            catalog_id=catalog.id,
            enabled_config=None,
        )
    )
    await session.commit()

    # Set the default model to the source-backed selection.
    await selections_service.set_default_model_selection(selection)

    # Now delete the source.
    await source_service.delete_model_source(source.id)

    # The default model should have been cleared.
    assert await selections_service.get_default_model() is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_upsert_catalog_rows_prunes_stale_workspace_subset_and_default(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    """When _upsert_catalog_rows replaces the catalog with a new model set,
    selection links and default model settings referencing stale rows
    should be removed."""
    from tracecat.agent.selections.service import AgentSelectionsService

    source_service = AgentSourceService(session, role=svc_admin_role)
    selections_service = AgentSelectionsService(session, role=svc_admin_role)
    workspace_id = svc_admin_role.workspace_id
    assert workspace_id is not None

    source = AgentSource(
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

    # Insert a stale catalog row and org-level + workspace-level links.
    stale_catalog = AgentCatalog(
        organization_id=svc_admin_role.organization_id,
        source_id=source.id,
        model_provider=stale_selection.model_provider,
        model_name=stale_selection.model_name,
    )
    session.add(stale_catalog)
    await session.flush()

    session.add(
        AgentModelSelectionLink(
            organization_id=svc_admin_role.organization_id,
            workspace_id=None,
            catalog_id=stale_catalog.id,
            enabled_config=None,
        )
    )
    await session.commit()

    # Set the default model to the stale selection.
    await selections_service.set_default_model_selection(stale_selection)

    # Create a workspace-level link for the stale catalog row.
    session.add(
        AgentModelSelectionLink(
            organization_id=svc_admin_role.organization_id,
            workspace_id=workspace_id,
            catalog_id=stale_catalog.id,
        )
    )
    await session.commit()

    # Replace the catalog with a fresh model.
    await source_service._upsert_catalog_rows(
        source_id=source.id,
        models=[
            {
                "model_provider": "openai",
                "model_id": "qwen-new",
                "metadata": {"slot": "new"},
            }
        ],
    )
    await session.commit()

    # The default should have been cleared because the stale catalog row was removed.
    assert await selections_service.get_default_model() is None

    # The org-level and workspace-level links for the stale catalog should be gone.
    remaining_links = (
        (
            await session.execute(
                select(AgentModelSelectionLink).where(
                    AgentModelSelectionLink.organization_id
                    == svc_admin_role.organization_id,
                    AgentModelSelectionLink.catalog_id == stale_catalog.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert remaining_links == []


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_upsert_catalog_rows_invalidates_stale_preset_versions_and_sessions(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    """When a source-backed catalog row is removed during upsert, presets,
    versions, and sessions referencing the stale model should be invalidated."""
    source_service = AgentSourceService(session, role=svc_admin_role)
    workspace_id = svc_admin_role.workspace_id
    assert workspace_id is not None

    source = AgentSource(
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

    # Create a preset referencing the stale model.
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

    stale_catalog = AgentCatalog(
        organization_id=svc_admin_role.organization_id,
        source_id=source.id,
        model_provider=stale_selection.model_provider,
        model_name=stale_selection.model_name,
    )
    session.add_all([version, agent_session, stale_catalog])
    await session.flush()

    # Wire up the preset -> version link.
    preset.current_version_id = version.id
    session.add(preset)

    # Create an org-level selection link for the stale catalog row.
    session.add(
        AgentModelSelectionLink(
            organization_id=svc_admin_role.organization_id,
            workspace_id=None,
            catalog_id=stale_catalog.id,
            enabled_config=None,
        )
    )
    await session.commit()

    # Replace the catalog with a fresh model.
    await source_service._upsert_catalog_rows(
        source_id=source.id,
        models=[
            {
                "model_provider": "openai",
                "model_id": "qwen-new",
                "metadata": {"slot": "new"},
            }
        ],
    )
    await session.commit()

    await session.refresh(preset)
    await session.refresh(version)
    await session.refresh(agent_session)

    # The preset and version model names should be invalidated.
    assert preset.model_name == "qwen-old [unavailable]"
    assert preset.model_provider == stale_selection.model_provider
    assert preset.source_id is None
    assert preset.base_url is None
    assert version.model_name == "qwen-old [unavailable]"
    assert version.model_provider == stale_selection.model_provider
    assert version.source_id is None
    assert version.base_url is None
    # The agent session model fields should be nulled out.
    assert agent_session.source_id is None
    assert agent_session.model_provider is None
    assert agent_session.model_name is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_upsert_catalog_rows_keeps_duplicate_model_names_across_providers(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Two catalog rows with the same model_name but different model_provider
    under the same source should both survive an upsert."""
    service = AgentSourceService(session, role=svc_role)

    source = AgentSource(
        organization_id=svc_role.organization_id,
        model_provider=CustomModelSourceType.MANUAL_CUSTOM.value,
        display_name="Manual source",
        declared_models=[],
    )
    session.add(source)
    await session.flush()

    # Seed existing catalog rows so the upsert path exercises ON CONFLICT.
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
        source_id=source.id,
        models=[
            {
                "model_provider": "openai",
                "model_id": "shared-model",
                "metadata": {"slot": "openai"},
            },
            {
                "model_provider": "anthropic",
                "model_id": "shared-model",
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
