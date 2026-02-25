"""Tests for the Cases SDK client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from tracecat_registry.sdk.cases import CasesClient


@pytest.fixture
def mock_tracecat_client() -> MagicMock:
    """Create a mock TracecatClient."""
    client = MagicMock()
    client.post = AsyncMock()
    client.patch = AsyncMock()
    return client


@pytest.fixture
def cases_client(mock_tracecat_client: MagicMock) -> CasesClient:
    """Create a CasesClient with mocked HTTP client."""
    return CasesClient(mock_tracecat_client)


@pytest.mark.anyio
async def test_create_case_serializes_dropdown_uuid_values(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    """UUID dropdown ids are serialized to strings for JSON transport."""
    definition_id = uuid4()
    option_id = uuid4()
    mock_tracecat_client.post.return_value = {"id": "case-id"}

    await cases_client.create_case(
        summary="Case summary",
        description="Case description",
        dropdown_values=[
            {"definition_id": definition_id, "option_id": option_id},
            {"definition_ref": "environment", "option_ref": "prod"},
        ],
    )

    mock_tracecat_client.post.assert_called_once_with(
        "/cases",
        json={
            "summary": "Case summary",
            "description": "Case description",
            "status": "new",
            "priority": "medium",
            "severity": "medium",
            "dropdown_values": [
                {"definition_id": str(definition_id), "option_id": str(option_id)},
                {"definition_ref": "environment", "option_ref": "prod"},
            ],
        },
    )


@pytest.mark.anyio
async def test_update_case_simple_serializes_and_keeps_null_option(
    cases_client: CasesClient, mock_tracecat_client: MagicMock
) -> None:
    """UUID dropdown ids are stringified while explicit null option_id is retained."""
    definition_id = uuid4()
    mock_tracecat_client.patch.return_value = {"id": "case-id"}

    await cases_client.update_case_simple(
        "case-id",
        dropdown_values=[{"definition_id": definition_id, "option_id": None}],
    )

    mock_tracecat_client.patch.assert_called_once_with(
        "/cases/case-id/simple",
        json={
            "dropdown_values": [
                {"definition_id": str(definition_id), "option_id": None},
            ]
        },
    )
