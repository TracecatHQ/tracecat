import pytest
from sqlalchemy import null
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.legacy_model_matching import (
    LegacyCatalogMatch,
    resolve_enabled_catalog_match_for_provider_model,
)
from tracecat.auth.types import Role
from tracecat.db.models import AgentEnabledModel


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_enabled_catalog_match_handles_null_source_ids_in_workspace_subset(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    await session.execute(
        AgentEnabledModel.__table__.insert(),
        [
            {
                "organization_id": svc_role.organization_id,
                "workspace_id": None,
                "source_id": None,
                "model_provider": "openai",
                "model_name": "gpt-5.2",
                "enabled_config": None,
            },
            {
                "organization_id": svc_role.organization_id,
                "workspace_id": svc_role.workspace_id,
                "source_id": None,
                "model_provider": "openai",
                "model_name": "gpt-5.2",
                "enabled_config": null(),
            },
        ],
    )
    await session.commit()

    match_result = await resolve_enabled_catalog_match_for_provider_model(
        session,
        organization_id=svc_role.organization_id,
        workspace_id=svc_role.workspace_id,
        model_provider="openai",
        model_name="gpt-5.2",
    )

    assert match_result == LegacyCatalogMatch(
        status="matched",
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
