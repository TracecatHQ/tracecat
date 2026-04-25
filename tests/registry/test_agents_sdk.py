"""Tests for the Agents SDK client."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.sdk.agents import AgentConfig, AgentsClient


@pytest.fixture
def mock_tracecat_client() -> MagicMock:
    client = MagicMock()
    client.post = AsyncMock()
    return client


@pytest.fixture
def agents_client(mock_tracecat_client: MagicMock) -> AgentsClient:
    return AgentsClient(mock_tracecat_client)


@pytest.mark.anyio
async def test_run_serializes_config_catalog_id(
    agents_client: AgentsClient,
    mock_tracecat_client: MagicMock,
) -> None:
    catalog_id = uuid.uuid4()
    mock_tracecat_client.post.return_value = {
        "output": "ok",
        "duration": 0.1,
        "usage": {},
        "session_id": str(uuid.uuid4()),
    }

    await agents_client.run(
        user_prompt="Summarize this",
        config=AgentConfig(
            model_name="gpt-4.1",
            model_provider="openai",
            catalog_id=catalog_id,
        ),
    )

    mock_tracecat_client.post.assert_awaited_once()
    _, kwargs = mock_tracecat_client.post.await_args
    assert kwargs["json"]["config"]["catalog_id"] == str(catalog_id)
