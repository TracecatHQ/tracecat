"""Tests for platform-level admin agent catalog refresh behavior."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import orjson
import pytest
from sqlalchemy import insert, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.admin.agent.service import AdminAgentService
from tracecat.agent.builtin_catalog import BuiltInCatalogModel
from tracecat.agent.types import ModelDiscoveryStatus, ModelSourceType
from tracecat.auth.types import PlatformRole
from tracecat.db.models import (
    AgentCatalog,
    AgentEnabledModel,
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
    Organization,
    OrganizationSetting,
    Workspace,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.mark.anyio
async def test_upsert_platform_catalog_rows_prunes_stale_org_state(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AdminAgentService(session, role=platform_role)
    org_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    stale_catalog_id = uuid.uuid4()
    fresh_catalog_id = uuid.uuid4()
    session.add(
        Organization(
            id=org_id,
            name="Test Organization",
            slug=f"test-org-{org_id.hex[:8]}",
            is_active=True,
        )
    )
    session.add(
        Workspace(
            id=workspace_id,
            organization_id=org_id,
            name="Test Workspace",
        )
    )
    await session.commit()

    session.add(
        AgentCatalog(
            id=stale_catalog_id,
            organization_id=None,
            source_id=None,
            model_provider="openai",
            model_name="gpt-stale",
        )
    )
    session.add_all(
        [
            AgentEnabledModel(
                organization_id=org_id,
                workspace_id=None,
                source_id=None,
                model_provider="openai",
                model_name="gpt-stale",
                enabled_config=None,
            ),
            OrganizationSetting(
                organization_id=org_id,
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
            ),
            OrganizationSetting(
                organization_id=org_id,
                key="agent_default_model_ref",
                value=orjson.dumps("legacy-ref"),
                value_type="json",
                is_encrypted=False,
            ),
            AgentPreset(
                workspace_id=workspace_id,
                name="Stale preset",
                slug="stale-preset",
                instructions="Use the stale built-in model",
                model_name="gpt-stale",
                model_provider="openai",
                source_id=None,
                base_url="https://stale.example/v1",
                retries=3,
                enable_internet_access=False,
            ),
        ]
    )
    await session.flush()
    preset = (
        await session.execute(
            select(AgentPreset).where(AgentPreset.workspace_id == workspace_id)
        )
    ).scalar_one()
    version = AgentPresetVersion(
        workspace_id=workspace_id,
        preset_id=preset.id,
        version=1,
        instructions=preset.instructions,
        model_name=preset.model_name,
        model_provider=preset.model_provider,
        source_id=None,
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
        source_id=None,
        model_provider="openai",
        model_name="gpt-stale",
    )
    session.add_all([version, agent_session])
    await session.flush()
    preset.current_version_id = version.id
    session.add(preset)
    await session.execute(
        insert(AgentEnabledModel).values(
            organization_id=org_id,
            workspace_id=workspace_id,
            source_id=None,
            model_provider="openai",
            model_name="gpt-stale",
            enabled_config=null(),
        )
    )
    await session.commit()

    monkeypatch.setattr(
        "tracecat.admin.agent.service.get_builtin_catalog_models",
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

    await service._upsert_platform_catalog_rows()
    await session.commit()

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

    enabled_rows = (
        (
            await session.execute(
                select(AgentEnabledModel).where(
                    AgentEnabledModel.organization_id == org_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert enabled_rows == []

    settings = (
        (
            await session.execute(
                select(OrganizationSetting).where(
                    OrganizationSetting.organization_id == org_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {setting.key: orjson.loads(setting.value) for setting in settings} == {
        "agent_default_model": None,
        "agent_default_model_ref": None,
    }

    await session.refresh(preset)
    await session.refresh(version)
    await session.refresh(agent_session)
    assert preset.model_name == "gpt-stale [unavailable]"
    assert preset.base_url is None
    assert version.model_name == "gpt-stale [unavailable]"
    assert version.base_url is None
    assert agent_session.model_provider is None
    assert agent_session.model_name is None


@pytest.mark.anyio
async def test_upsert_platform_catalog_rows_clears_legacy_string_default(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AdminAgentService(session, role=platform_role)
    org_id = uuid.uuid4()
    stale_catalog_id = uuid.uuid4()
    fresh_catalog_id = uuid.uuid4()
    session.add(
        Organization(
            id=org_id,
            name="Test Organization",
            slug=f"test-org-{org_id.hex[:8]}",
            is_active=True,
        )
    )
    await session.commit()

    session.add(
        AgentCatalog(
            id=stale_catalog_id,
            organization_id=None,
            source_id=None,
            model_provider="openai",
            model_name="gpt-stale",
        )
    )
    session.add_all(
        [
            AgentEnabledModel(
                organization_id=org_id,
                workspace_id=None,
                source_id=None,
                model_provider="openai",
                model_name="gpt-stale",
                enabled_config=None,
            ),
            OrganizationSetting(
                organization_id=org_id,
                key="agent_default_model",
                value=orjson.dumps("gpt-stale"),
                value_type="json",
                is_encrypted=False,
            ),
        ]
    )
    await session.commit()

    monkeypatch.setattr(
        "tracecat.admin.agent.service.get_builtin_catalog_models",
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

    await service._upsert_platform_catalog_rows()
    await session.commit()

    settings = (
        (
            await session.execute(
                select(OrganizationSetting).where(
                    OrganizationSetting.organization_id == org_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {setting.key: orjson.loads(setting.value) for setting in settings} == {
        "agent_default_model": None
    }


@pytest.mark.anyio
@pytest.mark.parametrize("cursor", ["abc", "-1"])
async def test_list_platform_catalog_rejects_invalid_cursor(
    cursor: str,
    platform_role: PlatformRole,
) -> None:
    service = AdminAgentService(AsyncMock(), role=platform_role)
    service._get_catalog_state = AsyncMock(
        return_value=(ModelDiscoveryStatus.READY, None, None)
    )

    with pytest.raises(
        ValueError, match="Invalid cursor. Expected a non-negative integer offset."
    ):
        await service.list_platform_catalog(cursor=cursor)
