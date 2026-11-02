"""HTTP regression tests for removing an unattached case tag."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import CaseTag


@pytest.mark.anyio
@pytest.mark.parametrize("internal", [False, True], ids=["public", "internal"])
async def test_remove_unattached_tag_returns_404(
    client: TestClient,
    action_gateway_client: TestClient,
    test_role: Role,
    internal: bool,
) -> None:
    """Both routes map the service's missing association error to HTTP 404."""
    case_id = uuid.uuid4()
    tag = CaseTag(id=uuid.uuid4(), name="Escalated", ref="escalated")
    http_client = action_gateway_client if internal else client
    prefix = "/internal" if internal else ""

    with (
        patch.object(CaseTagsService, "_get_case", new_callable=AsyncMock),
        patch.object(
            CaseTagsService,
            "get_tag_by_ref_or_id",
            new_callable=AsyncMock,
            return_value=tag,
        ),
        patch.object(
            CaseTagsService,
            "get_case_tag",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = http_client.delete(f"{prefix}/cases/{case_id}/tags/{tag.ref}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == f"Tag {tag.ref} not found on case {case_id}"
