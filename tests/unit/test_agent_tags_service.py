"""Unit tests for AgentTagsService."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.tags.schemas import AgentTagCreate, AgentTagUpdate
from tracecat.agent.tags.service import AgentTagsService
from tracecat.auth.types import Role
from tracecat.db.models import AgentPreset, AgentTagLink, Workspace
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def tags_service(session: AsyncSession, svc_role: Role) -> AgentTagsService:
    return AgentTagsService(session=session, role=svc_role)


@pytest.fixture
async def preset(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[AgentPreset, None]:
    p = AgentPreset(
        name="test preset",
        slug="test-preset-tags",
        workspace_id=svc_workspace.id,
        model_name="claude-3-5-sonnet",
        model_provider="anthropic",
    )
    session.add(p)
    await session.commit()
    try:
        yield p
    finally:
        await session.delete(p)
        await session.commit()


@pytest.mark.anyio
class TestAgentTagsService:
    async def test_create_and_list(self, tags_service: AgentTagsService) -> None:
        tag = await tags_service.create_tag(
            AgentTagCreate(name="Sec Ops", color="#FF0000")
        )
        assert tag.name == "Sec Ops"
        assert tag.ref == "sec-ops"
        assert tag.color == "#FF0000"

        tags = await tags_service.list_tags()
        assert any(t.id == tag.id for t in tags)

    async def test_duplicate_slug_raises_conflict(
        self, tags_service: AgentTagsService
    ) -> None:
        await tags_service.create_tag(AgentTagCreate(name="Triage"))
        with pytest.raises(TracecatConflictError):
            await tags_service.create_tag(AgentTagCreate(name="triage"))

    async def test_blank_slug_rejected(self, tags_service: AgentTagsService) -> None:
        with pytest.raises(TracecatConflictError):
            # Pure punctuation slugifies to empty.
            await tags_service.create_tag(AgentTagCreate(name="---"))

    async def test_update_regenerates_ref(self, tags_service: AgentTagsService) -> None:
        tag = await tags_service.create_tag(AgentTagCreate(name="Old Name"))
        updated = await tags_service.update_tag(tag, AgentTagUpdate(name="New Name"))
        assert updated.name == "New Name"
        assert updated.ref == "new-name"

    async def test_update_to_existing_slug_raises_conflict(
        self, tags_service: AgentTagsService
    ) -> None:
        await tags_service.create_tag(AgentTagCreate(name="One"))
        two = await tags_service.create_tag(AgentTagCreate(name="Two"))
        with pytest.raises(TracecatConflictError):
            await tags_service.update_tag(two, AgentTagUpdate(name="One"))

    async def test_get_by_ref(self, tags_service: AgentTagsService) -> None:
        tag = await tags_service.create_tag(AgentTagCreate(name="Find Me"))
        found = await tags_service.get_tag_by_ref(tag.ref)
        assert found.id == tag.id

    async def test_get_unknown_id_raises_not_found(
        self, tags_service: AgentTagsService
    ) -> None:
        with pytest.raises(TracecatNotFoundError):
            await tags_service.get_tag(uuid.uuid4())

    async def test_add_preset_tag_idempotent(
        self,
        tags_service: AgentTagsService,
        preset: AgentPreset,
    ) -> None:
        tag = await tags_service.create_tag(AgentTagCreate(name="Attach"))
        link1 = await tags_service.add_preset_tag(preset.id, tag.id)
        link2 = await tags_service.add_preset_tag(preset.id, tag.id)
        assert isinstance(link1, AgentTagLink)
        assert isinstance(link2, AgentTagLink)
        assert link1.preset_id == link2.preset_id == preset.id
        assert link1.tag_id == link2.tag_id == tag.id

    async def test_remove_preset_tag(
        self,
        tags_service: AgentTagsService,
        preset: AgentPreset,
    ) -> None:
        tag = await tags_service.create_tag(AgentTagCreate(name="Remove me"))
        link = await tags_service.add_preset_tag(preset.id, tag.id)
        await tags_service.remove_preset_tag(link)
        with pytest.raises(TracecatNotFoundError):
            await tags_service.get_preset_tag_link(preset.id, tag.id)

    async def test_add_tag_for_unknown_preset_raises_not_found(
        self,
        tags_service: AgentTagsService,
    ) -> None:
        tag = await tags_service.create_tag(AgentTagCreate(name="Stray"))
        with pytest.raises(TracecatNotFoundError):
            await tags_service.add_preset_tag(uuid.uuid4(), tag.id)

    async def test_list_tags_for_preset_returns_only_attached(
        self,
        tags_service: AgentTagsService,
        preset: AgentPreset,
    ) -> None:
        attached = await tags_service.create_tag(AgentTagCreate(name="In"))
        await tags_service.create_tag(AgentTagCreate(name="Out"))
        await tags_service.add_preset_tag(preset.id, attached.id)

        result = await tags_service.list_tags_for_preset(preset.id)
        ids = {t.id for t in result}
        assert ids == {attached.id}
