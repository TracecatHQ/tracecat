from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.api.workflowservice.v1 import StartWorkflowExecutionResponse
from temporalio.client import Client
from temporalio.service import ConnectConfig, ServiceClient

from tracecat import config
from tracecat.dsl._converter import get_data_converter


@pytest.fixture
def temporal_start_client() -> tuple[Client, AsyncMock]:
    """Exercise Temporal request encoding while mocking only the start RPC."""
    service = Mock(spec=ServiceClient)
    service.config = ConnectConfig(target_host="localhost:7233", identity="test-client")
    rpc = AsyncMock(return_value=StartWorkflowExecutionResponse(run_id="run-1"))
    service._rpc_call = rpc
    return Client(service, data_converter=get_data_converter()), rpc


@pytest.fixture
def smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "TRACECAT__SMTP_PORT", 587)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", "relay")
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", "secret")
    monkeypatch.setattr(
        config, "TRACECAT__EMAIL_FROM", "Tracecat <no-reply@example.com>"
    )


@pytest.fixture
def smtp_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", None)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", None)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", None)
    monkeypatch.setattr(config, "TRACECAT__EMAIL_FROM", None)
