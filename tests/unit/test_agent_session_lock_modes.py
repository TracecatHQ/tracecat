"""Row lock modes on agent sessions and workspaces that agent runtimes write under.

Invariant: Tracecat must never take a lock on an ``agent_session`` or
``workspace`` row that conflicts with the FOR KEY SHARE lock a foreign-key
check takes while an agent runtime may be writing. Runtime inserts of history,
session, command and approval rows reference those rows, so a FOR UPDATE held
by Tracecat stalls them; a runtime write that stalls past its deadline is
fenced and spends the turn's restart budget. Tracecat locks these rows with
FOR NO KEY UPDATE and never changes their key columns.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock

import pytest
import sqlalchemy as sa
from asyncpg.exceptions import LockNotAvailableError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.types import SessionTurnContext
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.types import AgentConfig
from tracecat.artifacts.bindings import ArtifactSideEffect
from tracecat.artifacts.schemas import CaseArtifact
from tracecat.auth.types import Role
from tracecat.cases.enums import CaseSeverity, CaseStatus
from tracecat.db.models import AgentPreset, AgentSession, AgentSessionHistory, Workspace
from tracecat.workspace_sync.adapters import AGENT_PRESET_RESOURCE_ADAPTER
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.schemas import AgentPresetResourceSpec

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]

RUNTIME_LOCK_TIMEOUT = "500ms"


@dataclass(frozen=True, slots=True)
class Seeded:
    sessions: async_sessionmaker[AsyncSession]
    role: Role
    session_id: uuid.UUID
    preset_id: uuid.UUID


@pytest.fixture
async def seeded(svc_role: Role) -> AsyncIterator[Seeded]:
    role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
    assert role.workspace_id is not None
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as seed:
            seed.add(
                Workspace(
                    id=role.workspace_id,
                    name="Lock mode test",
                    organization_id=role.organization_id,
                )
            )
            await seed.flush()
            agent_session = session_row(role.workspace_id)
            preset = AgentPreset(
                workspace_id=role.workspace_id,
                name="qa",
                slug="qa",
                model_name="test-model",
                model_provider="test-provider",
                agents={"subagents": []},
            )
            seed.add_all([agent_session, preset])
            await seed.commit()
        yield Seeded(
            sessions=sessions,
            role=role,
            session_id=agent_session.id,
            preset_id=preset.id,
        )
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                sa.delete(Workspace).where(Workspace.id == role.workspace_id)
            )
            await cleanup.commit()
        await engine.dispose()


def session_row(workspace_id: uuid.UUID) -> AgentSession:
    return AgentSession(
        workspace_id=workspace_id,
        title="Synthetic chat",
        entity_type="copilot",
        entity_id=workspace_id,
    )


async def runtime_write(
    seeded: Seeded, row_factory: Callable[[], AgentSession | AgentSessionHistory]
) -> None:
    """Insert a child row on its own connection, as the agent runtime does.

    The insert's foreign-key checks take FOR KEY SHARE on the referenced
    session and workspace rows; the short lock timeout turns a stall into an
    error instead of a hang.
    """
    async with seeded.sessions() as runtime:
        await runtime.execute(
            sa.text(f"SET LOCAL lock_timeout = '{RUNTIME_LOCK_TIMEOUT}'")
        )
        runtime.add(row_factory())
        await runtime.commit()


def history_write(seeded: Seeded) -> Callable[[], Awaitable[None]]:
    assert seeded.role.workspace_id is not None
    workspace_id = seeded.role.workspace_id

    def history_row() -> AgentSessionHistory:
        return AgentSessionHistory(
            workspace_id=workspace_id,
            session_id=seeded.session_id,
            content={"role": "assistant", "text": "Synthetic runtime message"},
        )

    return lambda: runtime_write(seeded, history_row)


def session_insert(seeded: Seeded) -> Callable[[], Awaitable[None]]:
    assert seeded.role.workspace_id is not None
    workspace_id = seeded.role.workspace_id
    return lambda: runtime_write(seeded, lambda: session_row(workspace_id))


async def count_rows(
    seeded: Seeded, model: type[AgentSession | AgentSessionHistory]
) -> int:
    async with seeded.sessions() as reader:
        count = await reader.scalar(
            sa.select(sa.func.count())
            .select_from(model)
            .where(model.workspace_id == seeded.role.workspace_id)
        )
    assert count is not None
    return count


def write_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    db: AsyncSession,
    write: Callable[[], Awaitable[None]],
) -> None:
    """Run ``write`` while ``db`` still holds its row locks and pending update."""
    commit = db.commit

    async def flush_write_commit() -> None:
        await db.flush()
        await write()
        await commit()

    monkeypatch.setattr(db, "commit", flush_write_commit)


@pytest.mark.parametrize(
    ("key_share", "admitted"),
    [(True, True), (False, False)],
    ids=["for_no_key_update", "for_update"],
)
async def test_session_lock_mode_decides_whether_runtime_history_write_waits(
    seeded: Seeded, key_share: bool, admitted: bool
) -> None:
    """Only FOR NO KEY UPDATE on a session admits a concurrent history insert."""
    async with seeded.sessions() as holder:
        await holder.execute(
            sa.select(AgentSession)
            .where(AgentSession.id == seeded.session_id)
            .with_for_update(key_share=key_share)
        )
        await assert_write_outcome(history_write(seeded), admitted=admitted)
        await holder.rollback()


@pytest.mark.parametrize(
    ("key_share", "admitted"),
    [(True, True), (False, False)],
    ids=["for_no_key_update", "for_update"],
)
async def test_workspace_lock_mode_decides_whether_runtime_insert_waits(
    seeded: Seeded, key_share: bool, admitted: bool
) -> None:
    """Only FOR NO KEY UPDATE on a workspace admits a workspace-scoped insert."""
    async with seeded.sessions() as holder:
        await holder.execute(
            sa.select(Workspace)
            .where(Workspace.id == seeded.role.workspace_id)
            .with_for_update(key_share=key_share)
        )
        await assert_write_outcome(session_insert(seeded), admitted=admitted)
        await holder.rollback()


async def assert_write_outcome(
    write: Callable[[], Awaitable[None]], *, admitted: bool
) -> None:
    if admitted:
        await write()
        return
    with pytest.raises(DBAPIError) as error:
        await write()
    assert error.value.orig is not None
    assert isinstance(error.value.orig.__cause__, LockNotAvailableError)


async def test_apply_artifact_side_effects_admits_runtime_history_write(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The artifact upsert's session lock and update admit history inserts."""
    artifact = CaseArtifact(
        id="case_1",
        title="Synthetic case",
        severity=CaseSeverity.LOW,
        status=CaseStatus.NEW,
    )
    async with seeded.sessions() as db:
        write_before_commit(monkeypatch, db, history_write(seeded))
        artifacts = await AgentSessionService(
            db, role=seeded.role
        ).apply_artifact_side_effects(
            seeded.session_id, [ArtifactSideEffect(op="upsert", artifact=artifact)]
        )
    assert [item.id for item in artifacts] == ["case_1"]
    assert await count_rows(seeded, AgentSessionHistory) == 1


async def test_remove_artifact_admits_runtime_history_write(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The artifact removal's session lock and update admit history inserts."""
    async with seeded.sessions() as db:
        write_before_commit(monkeypatch, db, history_write(seeded))
        artifacts = await AgentSessionService(db, role=seeded.role).remove_artifact(
            seeded.session_id, artifact_type="case", artifact_id="case_1"
        )
    assert artifacts == []
    assert await count_rows(seeded, AgentSessionHistory) == 1


async def test_start_turn_admission_lock_admits_runtime_history_write(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turn admission holds the session lock across dispatch; history still lands."""
    write = history_write(seeded)

    async def write_while_admitted(
        self: DefaultBackend, context: SessionTurnContext, client: object
    ) -> None:
        await write()

    monkeypatch.setattr(
        "tracecat.agent.backends.base.get_temporal_client",
        AsyncMock(return_value=Mock()),
    )
    monkeypatch.setattr(
        DefaultBackend, "_prepare_and_dispatch_turn", write_while_admitted
    )
    async with seeded.sessions() as db:
        agent_session = await db.scalar(
            sa.select(AgentSession).where(AgentSession.id == seeded.session_id)
        )
        assert agent_session is not None
        await DefaultBackend().start_turn(
            SessionTurnContext(
                db=db,
                session=agent_session,
                role=seeded.role,
                config=AgentConfig(model_name="test", model_provider="anthropic"),
                prompt="Synthetic prompt",
                run_id=uuid.uuid4(),
                stream_id=uuid.uuid4(),
            )
        )
        await db.rollback()
    assert await count_rows(seeded, AgentSessionHistory) == 1


async def test_workspace_default_reconcile_admits_runtime_session_insert(
    seeded: Seeded,
) -> None:
    """Sync's workspace lock and default-preset update admit workspace inserts."""
    async with seeded.sessions() as db:
        preset = await db.scalar(
            sa.select(AgentPreset).where(AgentPreset.id == seeded.preset_id)
        )
        assert preset is not None
        await AGENT_PRESET_RESOURCE_ADAPTER._reconcile_workspace_default(
            WorkspaceResourceImportService(db, role=seeded.role),
            {
                "qa": AgentPresetResourceSpec(
                    id="qa", slug="qa", name="qa", is_workspace_default=True
                )
            },
            {"qa": preset},
        )
        await session_insert(seeded)()
        workspace = await db.scalar(
            sa.select(Workspace).where(Workspace.id == seeded.role.workspace_id)
        )
        assert workspace is not None
        assert workspace.default_agent_preset_id == seeded.preset_id
        await db.rollback()
    assert await count_rows(seeded, AgentSession) == 2
