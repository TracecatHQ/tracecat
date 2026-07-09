from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from tracecat.db.engine import (
    get_async_engine,
    get_async_session_bypass_rls_context_manager,
)
from tracecat.db.models import AgentFolder, AgentPreset, Skill, Workspace
from tracecat.db.soft_delete import (
    assert_soft_delete_listener_registered,
    with_deleted,
)

pytestmark = pytest.mark.usefixtures("db")


def _slug(label: str) -> str:
    return f"soft-delete-{label}-{uuid.uuid4().hex[:8]}"


def _agent_preset(
    workspace_id: uuid.UUID,
    *,
    slug: str,
    folder_id: uuid.UUID | None = None,
    deleted: bool = False,
) -> AgentPreset:
    return AgentPreset(
        workspace_id=workspace_id,
        name=slug,
        slug=slug,
        model_name="gpt-4o-mini",
        model_provider="openai",
        folder_id=folder_id,
        deleted_at=datetime.now(UTC) if deleted else None,
    )


def _skill(workspace_id: uuid.UUID, *, name: str, deleted: bool = False) -> Skill:
    return Skill(
        workspace_id=workspace_id,
        name=name,
        slug=name,
        draft_revision=0,
        deleted_at=datetime.now(UTC) if deleted else None,
    )


async def _create_live_and_deleted_presets(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> tuple[str, str]:
    live_slug = _slug("live")
    deleted_slug = _slug("deleted")
    session.add_all(
        [
            _agent_preset(workspace_id, slug=live_slug),
            _agent_preset(workspace_id, slug=deleted_slug, deleted=True),
        ]
    )
    await session.commit()
    return live_slug, deleted_slug


@pytest.mark.anyio
async def test_entity_select_hides_tombstones_and_with_deleted_opts_out(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> None:
    """Entity selects hide tombstones unless the statement opts out."""
    live_slug, deleted_slug = await _create_live_and_deleted_presets(
        session,
        svc_workspace.id,
    )
    stmt = (
        select(AgentPreset)
        .where(AgentPreset.slug.in_([live_slug, deleted_slug]))
        .order_by(AgentPreset.slug)
    )

    active = (await session.scalars(stmt)).all()
    all_rows = (await session.scalars(with_deleted(stmt))).all()

    assert [preset.slug for preset in active] == [live_slug]
    assert [preset.slug for preset in all_rows] == sorted([live_slug, deleted_slug])


@pytest.mark.anyio
async def test_skill_select_hides_tombstones_and_with_deleted_opts_out(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> None:
    """Skill entity selects hide tombstones unless the statement opts out."""
    live_name = _slug("skill-live")
    deleted_name = _slug("skill-deleted")
    session.add_all(
        [
            _skill(svc_workspace.id, name=live_name),
            _skill(svc_workspace.id, name=deleted_name, deleted=True),
        ]
    )
    await session.commit()
    stmt = (
        select(Skill)
        .where(Skill.name.in_([live_name, deleted_name]))
        .order_by(Skill.name)
    )

    active = (await session.scalars(stmt)).all()
    all_rows = (await session.scalars(with_deleted(stmt))).all()

    assert [skill.name for skill in active] == [live_name]
    assert [skill.name for skill in all_rows] == sorted([live_name, deleted_name])


@pytest.mark.anyio
async def test_mapped_column_tuple_select_is_filtered(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> None:
    """Mapped-column tuple selects are filtered by the ORM criteria."""
    live_slug, deleted_slug = await _create_live_and_deleted_presets(
        session,
        svc_workspace.id,
    )

    result = await session.execute(
        select(AgentPreset.id, AgentPreset.slug)
        .where(AgentPreset.slug.in_([live_slug, deleted_slug]))
        .order_by(AgentPreset.slug)
    )
    rows = result.tuples().all()

    # SQLAlchemy keeps mapped-column tuples ORM-enabled, so the global criteria
    # applies; explicit active-only predicates still stay on service projections.
    assert [slug for _preset_id, slug in rows] == [live_slug]


@pytest.mark.anyio
async def test_joined_entity_filters_aliased_soft_deleted_rows(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> None:
    """Aliased joins to soft-deletable entities filter tombstones."""
    live_folder = AgentFolder(
        workspace_id=svc_workspace.id,
        name=_slug("live-folder"),
        path=f"/{_slug('live-folder')}/",
    )
    deleted_folder = AgentFolder(
        workspace_id=svc_workspace.id,
        name=_slug("deleted-folder"),
        path=f"/{_slug('deleted-folder')}/",
    )
    session.add_all([live_folder, deleted_folder])
    await session.flush()

    session.add_all(
        [
            _agent_preset(
                svc_workspace.id,
                slug=_slug("joined-live"),
                folder_id=live_folder.id,
            ),
            _agent_preset(
                svc_workspace.id,
                slug=_slug("joined-deleted"),
                folder_id=deleted_folder.id,
                deleted=True,
            ),
        ]
    )
    await session.commit()

    preset_alias = aliased(AgentPreset)
    result = await session.scalars(
        select(AgentFolder)
        .join(preset_alias, preset_alias.folder_id == AgentFolder.id)
        .where(AgentFolder.id.in_([live_folder.id, deleted_folder.id]))
        .order_by(AgentFolder.name)
    )

    assert [folder.id for folder in result.all()] == [live_folder.id]


@pytest.mark.anyio
async def test_relationship_loads_see_tombstones(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> None:
    """Relationship loads that reach AgentPreset rows see tombstones."""
    live_slug, deleted_slug = await _create_live_and_deleted_presets(
        session,
        svc_workspace.id,
    )

    session.expire(svc_workspace, ["agent_presets"])
    await session.refresh(svc_workspace, ["agent_presets"])

    relationship_slugs = {
        preset.slug
        for preset in svc_workspace.agent_presets
        if preset.slug in {live_slug, deleted_slug}
    }
    assert relationship_slugs == {live_slug, deleted_slug}


@pytest.mark.anyio
async def test_session_get_cold_filters_and_warm_identity_map_bypasses(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> None:
    """Cold Session.get filters tombstones; warm identity-map hits return them."""
    deleted_preset = _agent_preset(
        svc_workspace.id,
        slug=_slug("get-cold-deleted"),
        deleted=True,
    )
    live_preset = _agent_preset(svc_workspace.id, slug=_slug("get-warm-live"))
    session.add_all([deleted_preset, live_preset])
    await session.commit()
    deleted_pk = deleted_preset.surrogate_id
    live_pk = live_preset.surrogate_id

    session.expunge_all()
    cold_deleted = await session.get(AgentPreset, deleted_pk)
    assert cold_deleted is None

    loaded = await session.get(AgentPreset, live_pk)
    assert loaded is not None
    loaded.deleted_at = datetime.now(UTC)
    await session.flush()

    warm_deleted = await session.get(AgentPreset, live_pk)
    assert warm_deleted is loaded
    assert warm_deleted is not None
    assert warm_deleted.deleted_at is not None


@pytest.mark.anyio
async def test_bypass_rls_session_still_filters_soft_deleted_rows(
    svc_workspace: Workspace,
) -> None:
    """RLS-bypass sessions still filter tombstones unless opted out."""
    live_slug = _slug("bypass-live")
    deleted_slug = _slug("bypass-deleted")
    slugs = [live_slug, deleted_slug]

    async with get_async_session_bypass_rls_context_manager() as bypass_session:
        try:
            bypass_session.add_all(
                [
                    _agent_preset(svc_workspace.id, slug=live_slug),
                    _agent_preset(svc_workspace.id, slug=deleted_slug, deleted=True),
                ]
            )
            await bypass_session.commit()

            stmt = (
                select(AgentPreset)
                .where(
                    AgentPreset.workspace_id == svc_workspace.id,
                    AgentPreset.slug.in_(slugs),
                )
                .order_by(AgentPreset.slug)
            )
            active = (await bypass_session.scalars(stmt)).all()
            all_rows = (await bypass_session.scalars(with_deleted(stmt))).all()

            assert [preset.slug for preset in active] == [live_slug]
            assert [preset.slug for preset in all_rows] == sorted(slugs)
        finally:
            await bypass_session.execute(
                delete(AgentPreset).where(
                    AgentPreset.workspace_id == svc_workspace.id,
                    AgentPreset.slug.in_(slugs),
                )
            )
            await bypass_session.commit()


@pytest.mark.anyio
async def test_update_and_delete_statements_are_unfiltered(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> None:
    """ORM UPDATE and DELETE statements do not receive soft-delete criteria."""
    update_slug = _slug("update-deleted")
    delete_slug = _slug("delete-deleted")
    session.add_all(
        [
            _agent_preset(svc_workspace.id, slug=update_slug, deleted=True),
            _agent_preset(svc_workspace.id, slug=delete_slug, deleted=True),
        ]
    )
    await session.commit()

    updated_ids = (
        await session.scalars(
            update(AgentPreset)
            .where(AgentPreset.slug == update_slug)
            .values(name="updated tombstone")
            .returning(AgentPreset.id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    deleted_ids = (
        await session.scalars(
            delete(AgentPreset)
            .where(AgentPreset.slug == delete_slug)
            .returning(AgentPreset.id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    await session.commit()

    updated = await session.scalar(
        with_deleted(select(AgentPreset).where(AgentPreset.slug == update_slug))
    )
    deleted = await session.scalar(
        with_deleted(select(AgentPreset).where(AgentPreset.slug == delete_slug))
    )

    assert len(updated_ids) == 1
    assert len(deleted_ids) == 1
    assert updated is not None
    assert updated.name == "updated tombstone"
    assert deleted is None


def test_soft_delete_listener_startup_assertion_passes() -> None:
    """Startup assertion passes when the global listener is imported."""
    assert_soft_delete_listener_registered()


def test_engine_import_wires_listener_in_fresh_interpreter() -> None:
    """Importing tracecat.db.engine alone registers the listener (fresh interpreter).

    This test process already imported tracecat.db.soft_delete, so in-process
    checks cannot detect a lost side-effect import in engine.py. A subprocess
    proves the engine import chain does the wiring on its own.
    """
    code = (
        "import sys\n"
        "import tracecat.db.engine\n"
        "assert 'tracecat.db.soft_delete' in sys.modules, (\n"
        "    'tracecat.db.engine no longer imports tracecat.db.soft_delete; '\n"
        "    'the global soft-delete listener would be unregistered in prod'\n"
        ")\n"
        "from tracecat.db.soft_delete import assert_soft_delete_listener_registered\n"
        "assert_soft_delete_listener_registered()\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)


@pytest.mark.anyio
async def test_direct_engine_session_uses_soft_delete_filter(
    svc_workspace: Workspace,
) -> None:
    """Brand-new engine sessions use the listener without conftest sessions."""
    live_slug = _slug("direct-live")
    deleted_slug = _slug("direct-deleted")
    slugs = [live_slug, deleted_slug]

    async with AsyncSession(
        get_async_engine(), expire_on_commit=False
    ) as direct_session:
        try:
            direct_session.add_all(
                [
                    _agent_preset(svc_workspace.id, slug=live_slug),
                    _agent_preset(svc_workspace.id, slug=deleted_slug, deleted=True),
                ]
            )
            await direct_session.commit()

            stmt = (
                select(AgentPreset)
                .where(
                    AgentPreset.workspace_id == svc_workspace.id,
                    AgentPreset.slug.in_(slugs),
                )
                .order_by(AgentPreset.slug)
            )
            active = (await direct_session.scalars(stmt)).all()
            all_rows = (await direct_session.scalars(with_deleted(stmt))).all()

            assert [preset.slug for preset in active] == [live_slug]
            assert [preset.slug for preset in all_rows] == sorted(slugs)
        finally:
            await direct_session.execute(
                delete(AgentPreset).where(
                    AgentPreset.workspace_id == svc_workspace.id,
                    AgentPreset.slug.in_(slugs),
                )
            )
            await direct_session.commit()
