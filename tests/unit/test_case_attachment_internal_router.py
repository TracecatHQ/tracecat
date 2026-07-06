import base64
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

from tracecat.auth.types import Role
from tracecat.cases.attachments import internal_router
from tracecat.storage.exceptions import FileExtensionError


@pytest.mark.anyio
async def test_internal_create_attachment_maps_file_extension_error_to_415(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    workspace_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
    organization_id = uuid.UUID("00000000-0000-4000-8000-000000000003")
    case = SimpleNamespace(id=case_id)

    class AttachmentsStub:
        create_attachment = AsyncMock(
            side_effect=FileExtensionError(
                "File extension '.txt' is not allowed",
                extension=".txt",
                allowed_extensions=[],
            )
        )

    class CasesServiceStub:
        def __init__(self, session: object, role: Role) -> None:
            self.session = session
            self.role = role
            self.attachments = AttachmentsStub()

        async def get_case(self, case_id: uuid.UUID) -> object:
            return case

    monkeypatch.setattr(internal_router, "CasesService", CasesServiceStub)

    role = Role(
        type="service",
        organization_id=organization_id,
        workspace_id=workspace_id,
        service_id="tracecat-runner",
        scopes=frozenset({"case:update"}),
    )
    params = internal_router.ExecutorAttachmentCreateRequest(
        filename="hello.txt",
        content_type="text/plain",
        content_base64=base64.b64encode(b"hello tracecat").decode("utf-8"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await internal_router.create_attachment(
            role=role,
            session=cast(Any, object()),
            case_id=case_id,
            params=params,
        )

    exc = exc_info.value
    detail = cast(dict[str, object], exc.detail)
    assert exc.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    assert detail["error"] == "unsupported_file_extension"
    assert detail["extension"] == ".txt"
    assert detail["allowed_extensions"] == []
