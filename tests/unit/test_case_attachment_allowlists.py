import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.auth.types import Role
from tracecat.cases.attachments.schemas import CaseAttachmentCreate
from tracecat.cases.attachments.service import (
    CaseAttachmentService,
    _resolve_workspace_attachment_allowlist,
)
from tracecat.storage.exceptions import FileExtensionError
from tracecat.storage.validation import FileSecurityValidator


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, None),
        ([], []),
        ([".txt", ".pdf"], [".txt", ".pdf"]),
        ((".txt",), [".txt"]),
    ],
)
def test_resolve_workspace_attachment_allowlist_preserves_none_and_empty(
    configured: list[str] | tuple[str, ...] | None,
    expected: list[str] | None,
) -> None:
    resolved = _resolve_workspace_attachment_allowlist(configured)

    assert resolved == expected
    if configured is not None:
        assert resolved is not configured


def test_explicit_empty_workspace_allowlists_reject_attachment_like_input() -> None:
    validator = FileSecurityValidator(
        allowed_extensions=_resolve_workspace_attachment_allowlist([]),
        allowed_mime_types=_resolve_workspace_attachment_allowlist([]),
        validate_magic_number=False,
    )

    with pytest.raises(FileExtensionError):
        validator.validate_file(
            content=b"hello tracecat",
            filename="hello.txt",
            declared_mime_type="text/plain",
        )


@pytest.mark.anyio
async def test_create_attachment_rejects_explicit_empty_workspace_allowlists_before_blob_or_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    organization_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
    role = Role(
        type="service",
        organization_id=organization_id,
        workspace_id=workspace_id,
        service_id="tracecat-runner",
    )
    session = SimpleNamespace(
        execute=AsyncMock(),
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = CaseAttachmentService(session=session, role=role)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_assert_case_limits", AsyncMock())
    monkeypatch.setattr(
        service,
        "_get_workspace",
        AsyncMock(
            return_value=SimpleNamespace(
                settings={
                    "allowed_attachment_extensions": [],
                    "allowed_attachment_mime_types": [],
                    "validate_attachment_magic_number": False,
                }
            )
        ),
    )
    upload = AsyncMock()
    monkeypatch.setattr(service, "_upload_to_attachments_bucket", upload)

    content = b"hello tracecat"
    params = CaseAttachmentCreate(
        file_name="hello.txt",
        content_type="text/plain",
        size=len(content),
        content=content,
    )
    case = SimpleNamespace(id=uuid.UUID("00000000-0000-4000-8000-000000000003"))

    with pytest.raises(FileExtensionError):
        await service.create_attachment(case=case, params=params)  # type: ignore[arg-type]

    upload.assert_not_awaited()
    session.execute.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
