from __future__ import annotations

import base64
import uuid
from typing import Any, cast

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.attachments import internal_router
from tracecat.cases.attachments.internal_router import ExecutorAttachmentCreateRequest
from tracecat.db.models import Case
from tracecat.storage.exceptions import (
    MaxAttachmentsExceededError,
    StorageLimitExceededError,
)


def _role(*, workspace_id: uuid.UUID, organization_id: uuid.UUID) -> Role:
    return Role(
        type="service",
        workspace_id=workspace_id,
        organization_id=organization_id,
        service_id="tracecat-api",
        scopes=frozenset({"case:update"}),
    )


async def _invoke_internal_create_with_attachment_error(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> HTTPException:
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    case_id = uuid.uuid4()
    case = Case(id=case_id, workspace_id=workspace_id)

    class _Attachments:
        async def create_attachment(self, *args: Any, **kwargs: Any) -> Any:
            _ = (args, kwargs)
            raise error

    class _CasesService:
        attachments = _Attachments()

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        async def get_case(self, requested_case_id: uuid.UUID) -> Case | None:
            assert requested_case_id == case_id
            return case

    monkeypatch.setattr(internal_router, "CasesService", _CasesService)

    content = b"hello tracecat"
    params = ExecutorAttachmentCreateRequest(
        filename="hello.txt",
        content_base64=base64.b64encode(content).decode(),
        content_type="text/plain",
    )

    with pytest.raises(HTTPException) as exc_info:
        await internal_router.create_attachment(
            role=_role(
                workspace_id=workspace_id,
                organization_id=organization_id,
            ),
            session=cast(AsyncSession, object()),
            case_id=case_id,
            params=params,
        )
    return exc_info.value


@pytest.mark.anyio
async def test_internal_create_attachment_maps_max_attachments_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = await _invoke_internal_create_with_attachment_error(
        monkeypatch,
        MaxAttachmentsExceededError(
            "Maximum attachments per case exceeded", current_count=1, max_count=1
        ),
    )

    assert exc.status_code == status.HTTP_409_CONFLICT
    assert exc.detail == {
        "error": "max_attachments_exceeded",
        "message": "Maximum attachments per case exceeded",
        "current_count": 1,
        "max_count": 1,
    }


@pytest.mark.anyio
async def test_internal_create_attachment_maps_storage_limit_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = await _invoke_internal_create_with_attachment_error(
        monkeypatch,
        StorageLimitExceededError(
            "Case attachment storage limit exceeded",
            current_size=1024 * 1024,
            new_file_size=512 * 1024,
            max_size=1024 * 1024,
        ),
    )

    assert exc.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert exc.detail == {
        "error": "storage_limit_exceeded",
        "message": "Case attachment storage limit exceeded",
        "current_size_mb": 1.0,
        "new_file_size_mb": 0.5,
        "max_size_mb": 1.0,
    }
