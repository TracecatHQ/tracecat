"""Real PostgreSQL ownership, rollback, and concurrent selection contracts."""

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracecat.agent.references.storage import (
    DirectReferenceProjection,
    ReferenceSourceNotFound,
    ReferenceStorageConflict,
    ReferenceStorageService,
    SnapshotObject,
    SnapshotSelection,
    StoredPosition,
)
from tracecat.agent.references.uri import ReferenceKind, ReferenceTarget
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentPreset,
    AgentPresetVersion,
    AgentPresetVersionReference,
    AgentReferenceRunSnapshot,
    AgentSession,
    Organization,
    Skill,
    SkillVersion,
    SkillVersionReference,
    Workspace,
)


@dataclass
class StorageCase:
    sessions: async_sessionmaker[AsyncSession]
    role: Role
    skill_version: UUID
    preset_version: UUID
    agent_session: UUID
    other_workspace: UUID

    def store(self, session: AsyncSession) -> ReferenceStorageService:
        return ReferenceStorageService(session, role=self.role)

    def selection(self) -> SnapshotSelection:
        return SnapshotSelection(
            logical_turn_id=uuid4(),
            session_id=self.agent_session,
            backend_id="oss",
            harness_type="claude_code",
            input_hash="a" * 64,
        )


@pytest.fixture
async def case(reference_database):
    engine = create_async_engine(
        reference_database,
        connect_args={
            "server_settings": {"lock_timeout": "5000", "statement_timeout": "15000"}
        },
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    organization, workspace, other = uuid4(), uuid4(), uuid4()
    skill, preset, skill_version, preset_version, agent_session = (
        uuid4() for _ in range(5)
    )
    role = Role(
        type="service",
        service_id="tracecat-service",
        organization_id=organization,
        workspace_id=workspace,
    )
    async with sessions.begin() as session:
        session.add(
            Organization(
                id=organization, name="Synthetic references", slug=str(organization)
            )
        )
        await session.flush()
        session.add_all(
            [
                Workspace(
                    id=wid, organization_id=organization, name="Synthetic workspace"
                )
                for wid in (workspace, other)
            ]
        )
        await session.flush()
        session.add(
            Skill(
                id=skill,
                workspace_id=workspace,
                name="synthetic-skill",
                slug="synthetic-skill",
            )
        )
        session.add(
            AgentPreset(
                id=preset,
                workspace_id=workspace,
                name="Synthetic agent",
                slug="synthetic-agent",
                model_name="synthetic-model",
                model_provider="synthetic",
            )
        )
        await session.flush()
        session.add(
            SkillVersion(
                id=skill_version,
                workspace_id=workspace,
                skill_id=skill,
                version=1,
                manifest_sha256="a" * 64,
                file_count=1,
                total_size_bytes=1,
                name="synthetic-skill",
            )
        )
        session.add(
            AgentPresetVersion(
                id=preset_version,
                workspace_id=workspace,
                preset_id=preset,
                version=1,
                model_name="synthetic-model",
                model_provider="synthetic",
                instructions="Synthetic instructions",
            )
        )
        session.add(
            AgentSession(
                id=agent_session,
                workspace_id=workspace,
                entity_type="agent_preset",
                entity_id=preset,
            )
        )
    yield StorageCase(
        sessions, role, skill_version, preset_version, agent_session, other
    )
    await engine.dispose()


def projection(kind=ReferenceKind.TOOL, identity="core.http_request", path="SKILL.md"):
    return DirectReferenceProjection(
        source_path=path,
        target=ReferenceTarget(kind, identity),
        source_sha256="a" * 64,
        occurrences=(StoredPosition(line=2, column=3),),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["skill", "agent"])
async def test_projection_replacement_is_atomic_and_marks_empty(case, kind):
    source_model = SkillVersion if kind == "skill" else AgentPresetVersion
    model = SkillVersionReference if kind == "skill" else AgentPresetVersionReference
    version = case.skill_version if kind == "skill" else case.preset_version
    path = "SKILL.md" if kind == "skill" else "instructions.md"
    async with case.sessions.begin() as session:
        store = case.store(session)
        replace = (
            store.replace_skill_projection
            if kind == "skill"
            else store.replace_preset_projection
        )
        source = await session.scalar(
            select(source_model).where(source_model.id == version)
        )
        assert source.reference_schema_version is None
        item = projection(path=path)
        await replace(version, [item])
        assert source.reference_schema_version == 1
        with pytest.raises(IntegrityError):
            await replace(version, [item, item])
        rows = (
            await session.scalars(
                select(model).where(model.workspace_id == case.role.workspace_id)
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].occurrences == [{"line": 2, "column": 3}]
        await replace(version, [])
        assert source.reference_schema_version == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.workspace_id == case.role.workspace_id)
            )
            == 0
        )


@pytest.mark.anyio
async def test_projection_source_ownership_and_deletion(case):
    target = uuid4()
    async with case.sessions.begin() as session:
        session.add(
            Skill(
                id=target,
                workspace_id=case.role.workspace_id,
                name="target-skill",
                slug="target-skill",
            )
        )
        await session.flush()
        await case.store(session).replace_skill_projection(
            case.skill_version, [projection(ReferenceKind.SKILL, str(target))]
        )
        await session.execute(delete(Skill).where(Skill.id == target))
        row = await session.scalar(
            select(SkillVersionReference).where(
                SkillVersionReference.workspace_id == case.role.workspace_id
            )
        )
        assert row.target_id == target
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    update(SkillVersionReference)
                    .where(SkillVersionReference.id == row.id)
                    .values(workspace_id=case.other_workspace)
                )
        foreign = ReferenceStorageService(
            session,
            role=case.role.model_copy(update={"workspace_id": case.other_workspace}),
        )
        with pytest.raises(ReferenceSourceNotFound):
            await foreign.replace_skill_projection(case.skill_version, [])
        await session.execute(
            delete(SkillVersion).where(SkillVersion.id == case.skill_version)
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SkillVersionReference)
                .where(SkillVersionReference.id == row.id)
            )
            == 0
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "unknown"},
        {"target_id": uuid4()},
        {"action_key": None},
        {"action_key": "mcp.synthetic"},
        {"target_key": "wrong"},
        {"tool_name": "unexpected"},
        {"source_sha256": "not-a-hash"},
        {"occurrences": []},
        {"source_path": "../SKILL.md"},
    ],
)
async def test_projection_database_constraints(case, changes):
    async with case.sessions.begin() as session:
        await case.store(session).replace_skill_projection(
            case.skill_version, [projection()]
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    update(SkillVersionReference)
                    .where(SkillVersionReference.workspace_id == case.role.workspace_id)
                    .values(**changes)
                )


@pytest.mark.anyio
async def test_all_identity_kinds_and_preset_source_constraints(case):
    refs = [projection()]
    for kind in ReferenceKind:
        if kind == ReferenceKind.TOOL:
            continue
        target = ReferenceTarget(
            kind, str(uuid4()), "get_item" if kind == ReferenceKind.MCP_TOOL else None
        )
        refs.append(projection().model_copy(update={"target": target}))
    async with case.sessions.begin() as session:
        await case.store(session).replace_skill_projection(case.skill_version, refs)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SkillVersionReference)
                .where(SkillVersionReference.workspace_id == case.role.workspace_id)
            )
            == 7
        )
        with pytest.raises(ValueError, match="instructions.md"):
            await case.store(session).replace_preset_projection(
                case.preset_version, refs
            )


@pytest.mark.anyio
async def test_concurrent_snapshot_selection_reuses_winner(case):
    selection = case.selection()
    winner_inserted, contender_started = asyncio.Event(), asyncio.Event()

    async def winner():
        async with case.sessions.begin() as session:
            row = await case.store(session).select_snapshot(
                selection, SnapshotObject(key="synthetic/graph-a", sha256="a" * 64)
            )
            winner_inserted.set()
            await contender_started.wait()
            return row.id

    async def contender():
        await winner_inserted.wait()
        async with case.sessions.begin() as session:
            contender_started.set()
            row = await case.store(session).select_snapshot(
                selection, SnapshotObject(key="synthetic/graph-b", sha256="b" * 64)
            )
            assert row.graph_object_key == "synthetic/graph-a"
            return row.id

    ids = await asyncio.wait_for(asyncio.gather(winner(), contender()), timeout=10)
    assert ids[0] == ids[1]
    async with case.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentReferenceRunSnapshot)
                .where(AgentReferenceRunSnapshot.workspace_id == case.role.workspace_id)
            )
            == 1
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"input_hash": "b" * 64},
        {"backend_id": "other"},
        {"harness_type": "other"},
        {"session_id": uuid4()},
    ],
)
async def test_snapshot_retries_reject_identity_mismatch(case, changes):
    selection = case.selection()
    async with case.sessions.begin() as session:
        store = case.store(session)
        graph = SnapshotObject(key="synthetic/graph", sha256="a" * 64)
        await store.select_snapshot(selection, graph)
        with pytest.raises(ReferenceStorageConflict):
            await store.get_snapshot(selection.model_copy(update=changes))
        # Insert-or-read must enforce the same identity, even on conflict.
        with pytest.raises(ReferenceStorageConflict):
            await store.select_snapshot(selection.model_copy(update=changes), graph)


@pytest.mark.anyio
async def test_ready_is_monotonic_and_checks_original_identity(case):
    selection = case.selection()
    graph = SnapshotObject(key="synthetic/graph", sha256="a" * 64)
    bundle = SnapshotObject(key="synthetic/bundle", sha256="b" * 64)
    async with case.sessions.begin() as session:
        store = case.store(session)
        row = await store.select_snapshot(selection, graph)
        assert row.state == "resolved" and row.final_object_key is None
        row = await store.mark_snapshot_ready(selection, row.id, bundle)
        assert row.state == "ready" and row.graph_object_key == graph.key
        assert (await store.mark_snapshot_ready(selection, row.id, bundle)).id == row.id
        with pytest.raises(ReferenceStorageConflict):
            await store.mark_snapshot_ready(
                selection,
                row.id,
                SnapshotObject(key="synthetic/other", sha256="c" * 64),
            )
        with pytest.raises(ReferenceStorageConflict):
            await store.mark_snapshot_ready(
                selection.model_copy(update={"input_hash": "c" * 64}), row.id, bundle
            )
        retry = await store.select_snapshot(
            selection, SnapshotObject(key="synthetic/new-graph", sha256="c" * 64)
        )
        assert retry.state == "ready" and retry.graph_object_key == graph.key


@pytest.mark.anyio
async def test_snapshot_workspace_ownership_and_session_retention(case):
    selection = case.selection()
    graph = SnapshotObject(key="synthetic/graph", sha256="a" * 64)
    async with case.sessions.begin() as session:
        row = await case.store(session).select_snapshot(selection, graph)
        foreign = ReferenceStorageService(
            session,
            role=case.role.model_copy(update={"workspace_id": case.other_workspace}),
        )
        assert await foreign.get_snapshot(selection) is None
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await foreign.select_snapshot(selection, graph)
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    update(AgentReferenceRunSnapshot)
                    .where(AgentReferenceRunSnapshot.id == row.id)
                    .values(state="ready")
                )
        await session.execute(
            delete(AgentSession).where(AgentSession.id == case.agent_session)
        )
        assert await case.store(session).get_snapshot(selection) is None


@pytest.mark.parametrize("value", [0, -1, True, "1", 1.5])
def test_occurrences_require_positive_integer_positions(value):
    with pytest.raises(ValidationError):
        StoredPosition(line=value, column=1)


@pytest.mark.anyio
async def test_projection_respects_outer_transaction_rollback(case):
    async with case.sessions.begin() as session:
        await case.store(session).replace_skill_projection(
            case.skill_version, [projection()]
        )
    with pytest.raises(RuntimeError):
        async with case.sessions.begin() as session:
            await case.store(session).replace_skill_projection(case.skill_version, [])
            raise RuntimeError("Synthetic publication failure")
    async with case.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SkillVersionReference)
                .where(SkillVersionReference.workspace_id == case.role.workspace_id)
            )
            == 1
        )


@pytest.mark.anyio
async def test_concurrent_ready_candidates_cannot_overwrite(case):
    selection = case.selection()
    async with case.sessions.begin() as session:
        snapshot = await case.store(session).select_snapshot(
            selection, SnapshotObject(key="synthetic/graph", sha256="a" * 64)
        )
        snapshot_id = snapshot.id

    async def prepare(key):
        async with case.sessions.begin() as session:
            row = await case.store(session).mark_snapshot_ready(
                selection, snapshot_id, SnapshotObject(key=key, sha256="b" * 64)
            )
            return row.final_object_key

    results = await asyncio.wait_for(
        asyncio.gather(
            prepare("synthetic/bundle-a"),
            prepare("synthetic/bundle-b"),
            return_exceptions=True,
        ),
        timeout=10,
    )
    assert len([r for r in results if isinstance(r, ReferenceStorageConflict)]) == 1
    winner = next(r for r in results if isinstance(r, str))
    async with case.sessions() as session:
        row = await case.store(session).get_snapshot(selection)
        assert row.final_object_key == winner


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"state": "pending"},
        {"input_hash": "bad"},
        {"graph_sha256": "bad"},
        {"graph_object_key": ""},
        {"final_object_key": "synthetic/premature"},
        {
            "state": "ready",
            "final_object_key": "synthetic/bundle",
            "final_sha256": "bad",
        },
    ],
)
async def test_snapshot_database_constraints(case, changes):
    async with case.sessions.begin() as session:
        row = await case.store(session).select_snapshot(
            case.selection(), SnapshotObject(key="synthetic/graph", sha256="a" * 64)
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    update(AgentReferenceRunSnapshot)
                    .where(AgentReferenceRunSnapshot.id == row.id)
                    .values(**changes)
                )
