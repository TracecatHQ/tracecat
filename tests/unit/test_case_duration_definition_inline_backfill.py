import contextlib
import uuid
from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.durations.service import CaseDurationDefinitionService


@pytest.mark.anyio
async def test_after_commit_inline_backfill_syncs_each_case_under_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Redis-failure fallback routes every case through the locked
    per-case sync path and reports False when any case was lock-skipped,
    so the caller's bounded retry re-runs the backfill."""
    case_ids = [uuid.uuid4(), uuid.uuid4()]
    fresh_session = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = case_ids
    fresh_session.execute = AsyncMock(return_value=execute_result)
    sync_mock = AsyncMock(side_effect=[True, False])
    role = MagicMock()

    @contextlib.asynccontextmanager
    async def fake_session_context():
        yield fresh_session

    monkeypatch.setattr(
        "tracecat.cases.durations.service.get_async_session_bypass_rls_context_manager",
        fake_session_context,
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.sync_case_duration",
        sync_mock,
    )
    definition_service = CaseDurationDefinitionService(
        session=cast(AsyncSession, MagicMock()),
        role=role,
    )

    outcome = (
        await definition_service._sync_existing_case_durations_inline_after_commit()
    )

    assert outcome is False
    assert sync_mock.await_args_list == [
        call(definition_service.workspace_id, case_ids[0], event_types=None),
        call(definition_service.workspace_id, case_ids[1], event_types=None),
    ]
