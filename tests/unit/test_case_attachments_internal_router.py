from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.cases.attachments import internal_router
from tracecat.cases.attachments.schemas import CaseAttachmentCreate
from tracecat.db.engine import get_async_session
from tracecat.executor.action_gateway.app import create_app
from tracecat.executor.action_gateway.policy import (
    enforce_agent_script_gateway_access,
)
from tracecat.storage.exceptions import (
    FileContentMismatchError,
    FileExtensionError,
    FileMimeTypeError,
    FileNameError,
    FileSizeError,
    MaxAttachmentsExceededError,
    StorageLimitExceededError,
)
from tracecat.storage.validation import FileSecurityValidator

CASE_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ATTACHMENT_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
FILE_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
MALFORMED_TEXT_CONTENT = b"\x00\xff\x01\xfe"


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, MagicMock]]:
    service = MagicMock()
    service.get_case = AsyncMock(return_value=SimpleNamespace(id=CASE_ID))
    service.attachments.create_attachment = AsyncMock()

    def service_factory(_session: AsyncSession, _role: Role) -> MagicMock:
        return service

    app = create_app()
    app.dependency_overrides[get_args(ExecutorWorkspaceRole)[1].dependency] = lambda: (
        Role(
            type="service",
            service_id="tracecat-executor",
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            scopes=frozenset({"case:update"}),
        )
    )
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    app.dependency_overrides[enforce_agent_script_gateway_access] = lambda: None

    monkeypatch.setattr(internal_router, "CasesService", service_factory)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service


def _post_attachment(
    client: TestClient,
    *,
    content: bytes = b"hello",
    filename: str = "notes.txt",
    content_type: str = "text/plain",
) -> httpx.Response:
    return client.post(
        f"/internal/cases/{CASE_ID}/attachments",
        json={
            "filename": filename,
            "content_base64": base64.b64encode(content).decode(),
            "content_type": content_type,
        },
    )


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        pytest.param(
            FileExtensionError("Unsupported file extension", ".bin", [".txt"]),
            415,
            {
                "error": "unsupported_file_extension",
                "message": "Unsupported file extension",
                "extension": ".bin",
                "allowed_extensions": [".txt"],
            },
            id="extension",
        ),
        pytest.param(
            FileMimeTypeError(
                "File MIME type application/x-test is not allowed",
                "application/x-test",
                ["text/plain"],
            ),
            415,
            {
                "error": "unsupported_content_type",
                "message": "File MIME type application/x-test is not allowed",
                "content_type": "application/x-test",
                "allowed_types": ["text/plain"],
            },
            id="mime-type",
        ),
        pytest.param(
            FileSizeError("File is too large"),
            413,
            {"error": "file_too_large", "message": "File is too large"},
            id="file-size",
        ),
        pytest.param(
            FileContentMismatchError("File content does not match"),
            400,
            {
                "error": "file_validation_failed",
                "message": "File content does not match",
            },
            id="content-mismatch",
        ),
        pytest.param(
            FileNameError("Invalid file name"),
            400,
            {"error": "file_validation_failed", "message": "Invalid file name"},
            id="file-name",
        ),
        pytest.param(
            MaxAttachmentsExceededError("Too many attachments", 5, 5),
            409,
            {
                "error": "max_attachments_exceeded",
                "message": "Too many attachments",
                "current_count": 5,
                "max_count": 5,
            },
            id="attachment-count",
        ),
        pytest.param(
            StorageLimitExceededError(
                "Storage limit exceeded",
                1_500_000,
                2_500_000,
                4_000_000,
            ),
            413,
            {
                "error": "storage_limit_exceeded",
                "message": "Storage limit exceeded",
                "current_size_mb": 1.43,
                "new_file_size_mb": 2.38,
                "max_size_mb": 3.81,
            },
            id="storage-limit",
        ),
    ],
)
def test_validation_errors_keep_public_http_contract(
    gateway: tuple[TestClient, MagicMock],
    error: Exception,
    status_code: int,
    detail: dict[str, object],
) -> None:
    client, service = gateway
    service.attachments.create_attachment.side_effect = error

    response = _post_attachment(client)

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


def test_malformed_binary_declared_text_is_415(
    gateway: tuple[TestClient, MagicMock],
) -> None:
    client, service = gateway

    async def validate_upload(_case: object, params: CaseAttachmentCreate) -> None:
        FileSecurityValidator(
            allowed_extensions=[".txt"],
            allowed_mime_types=["text/plain"],
        ).validate_file(
            content=params.content,
            filename=params.file_name,
            declared_mime_type=params.content_type,
        )

    service.attachments.create_attachment.side_effect = validate_upload

    response = _post_attachment(client, content=MALFORMED_TEXT_CONTENT)

    assert response.status_code == 415
    assert response.json() == {
        "detail": {
            "error": "unsupported_content_type",
            "message": "Unknown or unsupported file type",
            "content_type": "text/plain",
            "allowed_types": ["text/plain"],
        }
    }


def test_valid_attachment_keeps_created_response(
    gateway: tuple[TestClient, MagicMock],
) -> None:
    client, service = gateway
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    service.attachments.create_attachment.return_value = SimpleNamespace(
        id=ATTACHMENT_ID,
        case_id=CASE_ID,
        file_id=FILE_ID,
        file=SimpleNamespace(
            name="notes.txt",
            content_type="text/plain",
            size=5,
            sha256="a" * 64,
            creator_id=None,
            is_deleted=False,
        ),
        created_at=timestamp,
        updated_at=timestamp,
    )

    response = _post_attachment(client)

    assert response.status_code == 201
    assert response.json() == {
        "id": str(ATTACHMENT_ID),
        "case_id": str(CASE_ID),
        "file_id": str(FILE_ID),
        "file_name": "notes.txt",
        "content_type": "text/plain",
        "size": 5,
        "sha256": "a" * 64,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "creator_id": None,
        "is_deleted": False,
    }
    params = service.attachments.create_attachment.await_args.args[1]
    assert params.file_name == "notes.txt"
    assert params.content_type == "text/plain"
    assert params.content == b"hello"


def test_unexpected_attachment_error_stays_platform_500(
    gateway: tuple[TestClient, MagicMock],
) -> None:
    client, service = gateway
    service.attachments.create_attachment.side_effect = RuntimeError(
        "synthetic storage failure"
    )

    response = _post_attachment(client)

    assert response.status_code == 500
    assert response.json() == {
        "message": "An unexpected error occurred. Please try again later."
    }


def test_invalid_base64_stays_http_400(gateway: tuple[TestClient, MagicMock]) -> None:
    client, service = gateway

    response = client.post(
        f"/internal/cases/{CASE_ID}/attachments",
        json={
            "filename": "notes.txt",
            "content_base64": "not-base64",
            "content_type": "text/plain",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Invalid base64 content:")
    service.attachments.create_attachment.assert_not_awaited()


def test_missing_case_stays_http_404(gateway: tuple[TestClient, MagicMock]) -> None:
    client, service = gateway
    service.get_case.return_value = None

    response = _post_attachment(client)

    assert response.status_code == 404
    assert response.json() == {"detail": f"Case with ID {CASE_ID} not found"}
    service.attachments.create_attachment.assert_not_awaited()
