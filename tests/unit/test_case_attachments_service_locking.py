from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.attachments.schemas import CaseAttachmentCreate
from tracecat.cases.attachments.service import CaseAttachmentService
from tracecat.db.models import Case, CaseAttachment, File, Workspace
from tracecat.exceptions import TracecatNotFoundError


class _StopAfterLimits(Exception):
    pass


class _ExecuteResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalars(self) -> _ExecuteResult:
        return self

    def first(self) -> Any:
        return self.value


class _RecordingSession:
    def __init__(self, results: list[Any], events: list[str]) -> None:
        self.results = results
        self.events = events
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _ExecuteResult:
        self.events.append("execute")
        self.statements.append(stmt)
        return _ExecuteResult(self.results.pop(0))

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


def _service_role(*, workspace_id: uuid.UUID, organization_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        workspace_id=workspace_id,
        organization_id=organization_id,
        service_id="tracecat-api",
        scopes=frozenset({"case:update"}),
    )


def _workspace(*, workspace_id: uuid.UUID, organization_id: uuid.UUID) -> Workspace:
    return Workspace(
        id=workspace_id,
        organization_id=organization_id,
        name="Test Workspace",
        settings={"validate_attachment_magic_number": False},
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
    session = _RecordingSession([case, None], events)
    service = CaseAttachmentService(
        session=cast(AsyncSession, session),
        role=_service_role(
            workspace_id=workspace_id,
            organization_id=organization_id,
        ),
    )

    async def get_workspace() -> Workspace:
        return _workspace(workspace_id=workspace_id, organization_id=organization_id)

    async def assert_case_limits(locked_case: Case, new_size: int) -> None:
        events.append("assert_case_limits")
        assert locked_case.id == case_id
        assert new_size == len(content)
        raise _StopAfterLimits

    monkeypatch.setattr(service, "_get_workspace", get_workspace)
    monkeypatch.setattr(service, "_assert_case_limits", assert_case_limits)

    params = CaseAttachmentCreate(
        file_name="hello.txt",
        content_type="text/plain",
        size=len(content),
        content=content,
    )

    with pytest.raises(_StopAfterLimits):
        await service.create_attachment(case, params)

    assert events == ["execute", "execute", "assert_case_limits", "rollback"]

    compiled = [
        str(stmt.compile(dialect=postgresql.dialect())) for stmt in session.statements
    ]
    assert 'FROM "case"' in compiled[0]
    assert "FOR UPDATE" in compiled[0]
    assert any('"case".workspace_id' in sql for sql in compiled)
    assert any('"case".id' in sql for sql in compiled)


@pytest.mark.anyio
async def test_lock_case_for_attachment_write_raises_when_case_disappears() -> None:
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    case = Case(id=uuid.uuid4(), workspace_id=workspace_id)
    events: list[str] = []
    session = _RecordingSession([None], events)
    service = CaseAttachmentService(
        session=cast(AsyncSession, session),
        role=_service_role(
            workspace_id=workspace_id,
            organization_id=organization_id,
        ),
    )

    with pytest.raises(TracecatNotFoundError):
        await service._lock_case_for_attachment_write(case)

    assert events == ["execute"]


@pytest.mark.anyio
async def test_existing_attachment_fast_path_commits_after_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    case_id = uuid.uuid4()
    file_id = uuid.uuid4()
    attachment_id = uuid.uuid4()
    content = b"hello tracecat"
    events: list[str] = []

    case = Case(id=case_id, workspace_id=workspace_id)
    file = File(
        id=file_id,
        workspace_id=workspace_id,
        sha256=CaseAttachmentService._compute_sha256(content),
        name="hello.txt",
        content_type="text/plain",
        size=len(content),
    )
    attachment = CaseAttachment(
        id=attachment_id,
        case_id=case_id,
        file_id=file_id,
    )
    attachment.file = file

    session = _RecordingSession([case, file, attachment], events)
    service = CaseAttachmentService(
        session=cast(AsyncSession, session),
        role=_service_role(
            workspace_id=workspace_id,
            organization_id=organization_id,
        ),
    )

    async def get_workspace() -> Workspace:
        return _workspace(workspace_id=workspace_id, organization_id=organization_id)

    async def assert_case_limits(locked_case: Case, new_size: int) -> None:
        _ = (locked_case, new_size)
        raise AssertionError("idempotent uploads must not consume quota")

    monkeypatch.setattr(service, "_get_workspace", get_workspace)
    monkeypatch.setattr(service, "_assert_case_limits", assert_case_limits)

    params = CaseAttachmentCreate(
        file_name="hello.txt",
        content_type="text/plain",
        size=len(content),
        content=content,
    )

    result = await service.create_attachment(case, params)

    assert result is attachment
    assert events == [
        "execute",
        "execute",
        "execute",
        "commit",
    ]
