import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.durations.router import list_case_durations
from tracecat.cases.durations.service import CaseDurationService

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_list_case_durations_is_read_only(
    session: AsyncSession,
    svc_role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_mock = AsyncMock()
    list_mock = AsyncMock(return_value=[])
    commit_mock = AsyncMock()

    monkeypatch.setattr(CaseDurationService, "sync_case_durations", sync_mock)
    monkeypatch.setattr(CaseDurationService, "list_durations", list_mock)
    monkeypatch.setattr(session, "commit", commit_mock)

    case_id = uuid.uuid4()
    result = await list_case_durations(
        role=svc_role,
        session=session,
        case_id=case_id,
    )

    assert result == []
    sync_mock.assert_not_awaited()
    commit_mock.assert_not_awaited()
    list_mock.assert_awaited_once_with(case_id)
