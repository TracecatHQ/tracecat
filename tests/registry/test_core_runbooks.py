"""Tests for core.runbooks UDFs in the registry."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tracecat_registry.core.runbooks import (
    execute,
    get_runbook,
    list_runbooks,
)


@pytest.fixture
def mock_runbook():
    """Create a mock runbook for testing."""
    runbook = MagicMock()
    runbook.id = uuid.uuid4()
    runbook.title = "Test Runbook"
    runbook.content = "Test Content"
    runbook.alias = "test-alias"
    runbook.owner_id = uuid.uuid4()
    runbook.tools = ["tools.example.query"]
    runbook.summary = "Test Summary"
    runbook.meta = {"test": "metadata"}
    return runbook


@pytest.mark.anyio
async def test_get_runbook_by_id(mock_runbook):
    """Test getting a runbook by UUID."""
    runbook_id = str(mock_runbook.id)

    with patch("tracecat_registry.core.runbooks.RunbookService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_runbook.return_value = mock_runbook
        MockService.with_session.return_value.__aenter__.return_value = mock_svc

        result = await get_runbook(runbook_id)

        # Verify the service was called with the parsed UUID
        mock_svc.get_runbook.assert_called_once_with(uuid.UUID(runbook_id))
        assert result["title"] == "Test Runbook"


@pytest.mark.anyio
async def test_get_runbook_by_alias(mock_runbook):
    """Test getting a runbook by alias."""
    alias = "test-alias"

    with patch("tracecat_registry.core.runbooks.RunbookService") as MockService:
        mock_svc = AsyncMock()
        # Mock that UUID parsing fails (ValueError)
        mock_svc.get_runbook.return_value = None
        mock_svc.get_runbook_by_alias.return_value = mock_runbook
        MockService.with_session.return_value.__aenter__.return_value = mock_svc

        result = await get_runbook(alias)

        # Verify the service tried UUID first, then alias
        mock_svc.get_runbook_by_alias.assert_called_once_with(alias)
        assert result["title"] == "Test Runbook"


@pytest.mark.anyio
async def test_get_runbook_not_found():
    """Test getting a runbook that doesn't exist."""
    with patch("tracecat_registry.core.runbooks.RunbookService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_runbook.return_value = None
        mock_svc.get_runbook_by_alias.return_value = None
        MockService.with_session.return_value.__aenter__.return_value = mock_svc

        with pytest.raises(
            ValueError, match="Runbook with ID/alias 'nonexistent' not found"
        ):
            await get_runbook("nonexistent")


@pytest.mark.anyio
async def test_execute_runbook_by_id(mock_runbook):
    """Test executing a runbook by UUID."""
    runbook_id = str(mock_runbook.id)
    case_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    with patch("tracecat_registry.core.runbooks.RunbookService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_runbook.return_value = mock_runbook

        # Mock the run_runbook response
        mock_response1 = MagicMock()
        mock_response1.chat_id = uuid.uuid4()
        mock_response1.stream_url = "https://example.com/stream1"

        mock_response2 = MagicMock()
        mock_response2.chat_id = uuid.uuid4()
        mock_response2.stream_url = "https://example.com/stream2"

        mock_svc.run_runbook.return_value = [mock_response1, mock_response2]
        MockService.with_session.return_value.__aenter__.return_value = mock_svc

        result = await execute(runbook_id, case_ids)

        # Verify the service was called correctly
        mock_svc.get_runbook.assert_called_once_with(uuid.UUID(runbook_id))
        mock_svc.run_runbook.assert_called_once()

        # Verify the result
        assert len(result) == 2
        assert "chat_id" in result[0]
        assert "stream_url" in result[0]


@pytest.mark.anyio
async def test_execute_runbook_by_alias(mock_runbook):
    """Test executing a runbook by alias."""
    alias = "test-alias"
    case_ids = [str(uuid.uuid4())]

    with patch("tracecat_registry.core.runbooks.RunbookService") as MockService:
        mock_svc = AsyncMock()
        # Mock that UUID parsing fails
        mock_svc.get_runbook.return_value = None
        mock_svc.get_runbook_by_alias.return_value = mock_runbook

        # Mock the run_runbook response
        mock_response = MagicMock()
        mock_response.chat_id = uuid.uuid4()
        mock_response.stream_url = "https://example.com/stream"

        mock_svc.run_runbook.return_value = [mock_response]
        MockService.with_session.return_value.__aenter__.return_value = mock_svc

        result = await execute(alias, case_ids)

        # Verify the service tried alias lookup
        mock_svc.get_runbook_by_alias.assert_called_once_with(alias)
        mock_svc.run_runbook.assert_called_once()

        # Verify the result
        assert len(result) == 1
        assert "chat_id" in result[0]
        assert "stream_url" in result[0]


@pytest.mark.anyio
async def test_execute_runbook_not_found():
    """Test executing a runbook that doesn't exist."""
    with patch("tracecat_registry.core.runbooks.RunbookService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_runbook.return_value = None
        mock_svc.get_runbook_by_alias.return_value = None
        MockService.with_session.return_value.__aenter__.return_value = mock_svc

        with pytest.raises(
            ValueError, match="Runbook with ID/alias 'nonexistent' not found"
        ):
            await execute("nonexistent", ["case-id"])


@pytest.mark.anyio
async def test_list_runbooks():
    """Test listing runbooks."""
    mock_runbook1 = MagicMock()
    mock_runbook1.id = uuid.uuid4()
    mock_runbook1.title = "Runbook 1"
    mock_runbook1.content = "Content 1"
    mock_runbook1.meta = {"test": "meta1"}
    mock_runbook1.alias = "alias1"
    mock_runbook1.summary = "Summary 1"
    mock_runbook1.owner_id = uuid.uuid4()
    mock_runbook1.tools = ["tool1"]

    mock_runbook2 = MagicMock()
    mock_runbook2.id = uuid.uuid4()
    mock_runbook2.title = "Runbook 2"
    mock_runbook2.content = "Content 2"
    mock_runbook2.meta = {"test": "meta2"}
    mock_runbook2.alias = "alias2"
    mock_runbook2.summary = "Summary 2"
    mock_runbook2.owner_id = uuid.uuid4()
    mock_runbook2.tools = ["tool2"]

    with patch("tracecat_registry.core.runbooks.RunbookService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_runbooks.return_value = [mock_runbook1, mock_runbook2]
        MockService.with_session.return_value.__aenter__.return_value = mock_svc

        result = await list_runbooks(limit=10, sort_by="created_at", order="desc")

        # Verify the service was called correctly
        mock_svc.list_runbooks.assert_called_once_with(
            limit=10, sort_by="created_at", order="desc"
        )

        # Verify the result
        assert len(result) == 2
        assert result[0]["title"] == "Runbook 1"
        assert result[1]["title"] == "Runbook 2"
