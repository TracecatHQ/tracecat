from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.durations.schemas import CaseDurationAnchorSelection
from tracecat.cases.enums import CaseEventType
from tracecat.workspace_sync.schemas import (
    CaseDurationAnchorSpec,
    CaseDurationResourceSpec,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
)
from tracecat.workspace_sync.service import WorkspaceSyncService

pytestmark = pytest.mark.usefixtures("db")


def _duration_spec(
    source_id: str,
    *,
    description: str | None = None,
    end_event: CaseEventType = CaseEventType.CASE_CLOSED,
) -> CaseDurationResourceSpec:
    return CaseDurationResourceSpec(
        id=source_id,
        name=source_id,
        description=description,
        start=CaseDurationAnchorSpec(
            event=CaseEventType.CASE_CREATED,
            selection=CaseDurationAnchorSelection.FIRST,
        ),
        end=CaseDurationAnchorSpec(
            event=end_event,
            selection=CaseDurationAnchorSelection.FIRST,
        ),
    )


def _snapshot(
    *durations: CaseDurationResourceSpec,
    commit_sha: str,
) -> WorkspaceRemoteSnapshot:
    return WorkspaceRemoteSnapshot(
        commit_sha=commit_sha,
        files={},
        spec=WorkspaceSpec(
            case_durations={duration.id: duration for duration in durations}
        ),
    )


async def _let_after_commit_callbacks_run() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.anyio
async def test_workspace_sync_backfills_only_materialization_changes(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_mock = AsyncMock(return_value="1-0")
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        publish_mock,
    )
    service = WorkspaceSyncService(session=session, role=svc_role)
    first = _duration_spec("first", description="First duration")
    second = _duration_spec("second", description="Second duration")

    created = await service._import_snapshot(
        _snapshot(first, second, commit_sha="a" * 40),
        sync_schedules=False,
    )
    await _let_after_commit_callbacks_run()

    assert created.success is True
    publish_mock.assert_awaited_once()
    publish_call = publish_mock.await_args
    assert publish_call is not None
    assert publish_call.kwargs["workspace_id"] == svc_role.workspace_id
    assert publish_call.kwargs["reason"] == "duration_definition_updated"

    publish_mock.reset_mock()
    descriptions_only = await service._import_snapshot(
        _snapshot(
            first.model_copy(update={"description": "Renamed description"}),
            second,
            commit_sha="b" * 40,
        ),
        sync_schedules=False,
    )
    await _let_after_commit_callbacks_run()

    assert descriptions_only.success is True
    publish_mock.assert_not_awaited()

    changed_anchor = await service._import_snapshot(
        _snapshot(
            first.model_copy(
                update={
                    "description": "Renamed description",
                    "end": first.end.model_copy(
                        update={"event": CaseEventType.STATUS_CHANGED}
                    ),
                }
            ),
            second,
            commit_sha="c" * 40,
        ),
        sync_schedules=False,
    )
    await _let_after_commit_callbacks_run()

    assert changed_anchor.success is True
    publish_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_workspace_sync_discards_backfill_when_outer_commit_fails(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_mock = AsyncMock(return_value="1-0")
    monkeypatch.setattr(
        "tracecat.cases.durations.sync_queue.publish_case_duration_sync",
        publish_mock,
    )
    monkeypatch.setattr(
        session,
        "commit",
        AsyncMock(side_effect=RuntimeError("commit failed")),
    )
    service = WorkspaceSyncService(session=session, role=svc_role)

    result = await service._import_snapshot(
        _snapshot(_duration_spec("new-duration"), commit_sha="d" * 40),
        sync_schedules=False,
    )
    await _let_after_commit_callbacks_run()

    assert result.success is False
    publish_mock.assert_not_awaited()
