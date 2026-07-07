import base64
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

from tracecat.auth.types import Role
from tracecat.cases.attachments import internal_router
from tracecat.storage.exceptions import (
    FileContentMismatchError,
    FileExtensionError,
    FileMimeTypeError,
    FileSizeError,
    MaxAttachmentsExceededError,
    StorageLimitExceededError,
)


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("service_error", "expected_status", "expected_detail"),
    [
        pytest.param(
            FileMimeTypeError(
                "Content type 'text/plain' is not allowed",
                mime_type="text/plain",
                allowed_types=["image/png"],
            ),
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            {
                "error": "unsupported_content_type",
                "content_type": "text/plain",
                "allowed_types": ["image/png"],
            },
            id="mime-type",
        ),
        pytest.param(
            FileSizeError("File size exceeds the configured limit"),
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            {"error": "file_too_large"},
            id="file-size",
        ),
        pytest.param(
            MaxAttachmentsExceededError(
                "Maximum attachments per case exceeded",
                current_count=5,
                max_count=5,
            ),
            status.HTTP_409_CONFLICT,
            {
                "error": "max_attachments_exceeded",
                "current_count": 5,
                "max_count": 5,
            },
            id="max-attachments",
        ),
        pytest.param(
            StorageLimitExceededError(
                "Case storage limit exceeded",
                current_size=2 * 1024 * 1024,
                new_file_size=1024 * 1024,
                max_size=2 * 1024 * 1024,
            ),
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            {
                "error": "storage_limit_exceeded",
                "current_size_mb": 2.0,
                "new_file_size_mb": 1.0,
                "max_size_mb": 2.0,
            },
            id="storage-limit",
        ),
        pytest.param(
            FileContentMismatchError("File content does not match metadata"),
            status.HTTP_400_BAD_REQUEST,
            {"error": "file_validation_failed"},
            id="content-mismatch",
        ),
    ],
)
async def test_internal_create_attachment_maps_service_errors_to_structured_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    service_error: Exception,
    expected_status: int,
    expected_detail: dict[str, object],
) -> None:
    case_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    workspace_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
    organization_id = uuid.UUID("00000000-0000-4000-8000-000000000003")
    case = SimpleNamespace(id=case_id)

    class AttachmentsStub:
        create_attachment = AsyncMock(side_effect=service_error)

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
        filename="hello.png",
        content_type="image/png",
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
    assert exc.status_code == expected_status
    assert detail["message"] == str(service_error)
    for key, value in expected_detail.items():
        assert detail[key] == value
