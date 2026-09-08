"""Exercise publication/deletion serialization with real PostgreSQL locks."""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat.agent.skill.service import ManifestValidationResult, SkillService
from tracecat.agent.skill.types import ResolvedSkillMcpTool, SkillToolProjection
from tracecat.auth.types import Role
from tracecat.db.models import MCPIntegration, Skill, SkillVersionMcpTool, Workspace
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.service import IntegrationService


@pytest.mark.anyio
async def test_deletion_rechecks_references_after_concurrent_publication(
    svc_role: Role,
) -> None:
    role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()})
    integration = MCPIntegration(
        workspace_id=role.workspace_id,
        name="Synthetic MCP",
        slug=f"synthetic-{uuid.uuid4().hex}",
        server_type="http",
        server_uri="https://mcp.example.test",
        auth_type=MCPAuthType.NONE,
        tools=[],
    )
    skill = Skill(
        workspace_id=role.workspace_id,
        name="concurrent-skill",
        slug=f"concurrent-{uuid.uuid4().hex}",
    )
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as seed:
            seed.add(
                Workspace(
                    id=role.workspace_id,
                    name="concurrent-projection-test",
                    organization_id=role.organization_id,
                )
            )
            await seed.flush()
            seed.add_all([integration, skill])
            await seed.commit()
        integration_id, skill_id = integration.id, skill.id
        async with (
            factory() as publishing,
            factory() as deleting,
            factory() as observer,
        ):
            publisher = SkillService(session=publishing, role=role)
            published_skill = await publishing.scalar(
                select(Skill).where(Skill.id == skill_id)
            )
            assert published_skill is not None
            version = await publisher.publish_version_from_blob_refs(
                skill=published_skill,
                file_refs=[],
                validation=ManifestValidationResult(
                    name="concurrent-skill",
                    tool_projection=SkillToolProjection(
                        mcp_tools=(
                            ResolvedSkillMcpTool(
                                tool_id=f"mcp.{integration.slug}",
                                mcp_integration_id=integration_id,
                                tool_name=None,
                            ),
                        )
                    ),
                ),
            )
            # Leave the actual publication uncommitted while deletion starts.
            publisher_pid = await publishing.scalar(text("SELECT pg_backend_pid()"))
            deleter_pid = await deleting.scalar(text("SELECT pg_backend_pid()"))
            deletion = asyncio.create_task(
                IntegrationService(session=deleting, role=role).delete_mcp_integration(
                    mcp_integration_id=integration_id
                )
            )
            try:
                # Synchronize on a real lock wait, not an assumed scheduling delay.
                async with asyncio.timeout(10):
                    while not await observer.scalar(
                        text("SELECT :publisher = ANY(pg_blocking_pids(:deleter))"),
                        {"publisher": publisher_pid, "deleter": deleter_pid},
                    ):
                        if deletion.done():
                            await deletion
                            pytest.fail("Deletion did not wait for publication")
                        await asyncio.sleep(0.01)
                await publishing.commit()
                with pytest.raises(TracecatValidationError) as exc_info:
                    await asyncio.wait_for(deletion, timeout=10)
                assert exc_info.value.detail is not None
                assert (
                    exc_info.value.detail["code"]
                    == "mcp_integration_referenced_by_skill"
                )
            finally:
                if not deletion.done():
                    deletion.cancel()
                await asyncio.gather(deletion, return_exceptions=True)
                await deleting.rollback()

            projected_id = await observer.scalar(
                select(SkillVersionMcpTool.mcp_integration_id).where(
                    SkillVersionMcpTool.skill_version_id == version.id
                )
            )
            assert projected_id == integration_id
    finally:
        await engine.dispose()
