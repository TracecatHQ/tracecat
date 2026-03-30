"""Tests for platform and admin agent catalog services."""

from __future__ import annotations

import uuid

import orjson
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.builtin_catalog import BuiltInCatalogModel
from tracecat.agent.catalog.service import (
    AgentCatalogService,
    parse_catalog_offset,
)
from tracecat.agent.types import ModelSourceType
from tracecat.auth.types import PlatformRole, Role
from tracecat.db.models import (
    AgentCatalog,
    AgentModelSelectionLink,
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
    OrganizationSetting,
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
    svc_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When builtin catalog rows are replaced, stale selection links, default
    model settings, presets, versions, and sessions should be invalidated."""
    # Use the org-scoped catalog service which handles full cascade.
    service = AgentCatalogService(session, role=svc_admin_role)
    org_id = svc_admin_role.organization_id
    workspace_id = svc_admin_role.workspace_id
    assert org_id is not None
    assert workspace_id is not None

    stale_catalog_id = uuid.uuid4()
    fresh_catalog_id = uuid.uuid4()

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
            AgentModelSelectionLink(
                organization_id=org_id,
                workspace_id=None,
                catalog_id=stale_catalog_id,
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
        model_name="gpt-stale",
        model_provider="openai",
        source_id=None,
        base_url="https://stale.example/v1",
        retries=3,
        enable_internet_access=False,
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
    await session.commit()

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
                select(AgentModelSelectionLink).where(
                    AgentModelSelectionLink.organization_id == org_id
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
    svc_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy string-format default model settings should be cleared when the
    referenced builtin model is removed from the catalog."""
    service = AgentCatalogService(session, role=svc_admin_role)
    org_id = svc_admin_role.organization_id
    assert org_id is not None

    stale_catalog_id = uuid.uuid4()
    fresh_catalog_id = uuid.uuid4()

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
            AgentModelSelectionLink(
                organization_id=org_id,
                workspace_id=None,
                catalog_id=stale_catalog_id,
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
    with pytest.raises(
        ValueError, match="Invalid cursor. Expected a non-negative integer offset."
    ):
        parse_catalog_offset(cursor)
