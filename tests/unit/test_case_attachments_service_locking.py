from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.attachments.schemas import CaseAttachmentCreate
from tracecat.cases.attachments.service import CaseAttachmentService
from tracecat.db.models import Case


class _StopAfterLimits(Exception):
    pass


class _CaseLockResult:
    def __init__(self, case: Case | None) -> None:
        self.case = case

    def scalars(self) -> _CaseLockResult:
        return self

    def first(self) -> Case | None:
        return self.case


class _RecordingSession:
    def __init__(self, locked_case: Case | None, events: list[str]) -> None:
        self.locked_case = locked_case
        self.events = events
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _CaseLockResult:
        self.events.append("execute")
        self.statements.append(stmt)
        return _CaseLockResult(self.locked_case)


def _service_role(*, workspace_id: uuid.UUID, organization_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        workspace_id=workspace_id,
        organization_id=organization_id,
        service_id="tracecat-api",
        scopes=frozenset({"case:update"}),
    )


@pytest.mark.anyio
async def test_create_attachment_locks_case_before_asserting_case_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    case_id = uuid.uuid4()
    content = b"hello tracecat"
    events: list[str] = []

    case = Case(id=case_id, workspace_id=workspace_id)
    session = _RecordingSession(case, events)
    service = CaseAttachmentService(
        session=cast(AsyncSession, session),
        role=_service_role(
            workspace_id=workspace_id,
            organization_id=organization_id,
        ),
    )

    async def assert_case_limits(locked_case: Case, new_size: int) -> None:
        events.append("assert_case_limits")
        assert locked_case.id == case_id
        assert new_size == len(content)
        raise _StopAfterLimits

    monkeypatch.setattr(service, "_assert_case_limits", assert_case_limits)

    params = CaseAttachmentCreate(
        file_name="hello.txt",
        content_type="text/plain",
        size=len(content),
        content=content,
    )

    with pytest.raises(_StopAfterLimits):
        await service.create_attachment(case, params)

    assert events == ["execute", "assert_case_limits"]

    compiled = [
        str(stmt.compile(dialect=postgresql.dialect())) for stmt in session.statements
    ]
    assert any('FROM "case"' in sql and "FOR UPDATE" in sql for sql in compiled)
    assert any('"case".workspace_id' in sql for sql in compiled)
    assert any('"case".id' in sql for sql in compiled)
