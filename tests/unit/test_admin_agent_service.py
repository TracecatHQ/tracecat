"""Tests for platform-level admin agent catalog refresh behavior."""

from __future__ import annotations

import uuid

import orjson
import pytest
from sqlalchemy import insert, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.admin.agent.service import AdminAgentService
from tracecat.agent.builtin_catalog import BuiltInCatalogModel
from tracecat.agent.types import ModelSourceType
from tracecat.auth.types import PlatformRole
from tracecat.db.models import (
    AgentCatalog,
    AgentEnabledModel,
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
        ]
    )
    await session.flush()
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
