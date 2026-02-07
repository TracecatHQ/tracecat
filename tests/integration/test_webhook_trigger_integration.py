"""Integration tests for webhook-triggered workflow execution."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.api.app import app
from tracecat.db.engine import get_async_session_context_manager, reset_async_engine
from tracecat.db.models import Webhook
from tracecat.dsl.common import DSLInput
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage.object import InlineObject, StoredObjectValidator, get_object_storage
from tracecat.storage.utils import resolve_execution_context
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import RemoteWebhook, RemoteWorkflowDefinition

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("db", "registry_version_with_manifest"),
]


@pytest.fixture(autouse=True)
def use_test_db_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point application DB access at the per-test database."""
    monkeypatch.setenv("TRACECAT__DB_URI", TEST_DB_CONFIG.test_url)
    monkeypatch.setattr(config, "TRACECAT__DB_URI", TEST_DB_CONFIG.test_url)
    reset_async_engine()
    yield
    reset_async_engine()


@pytest.mark.anyio
async def test_webhook_trigger_creates_execution(
    svc_role,
    temporal_client,
    test_worker_factory,
) -> None:
    dsl = DSLInput.from_yaml(
        Path("tests/data/workflows/integration_webhook_concat.yml")
    )
    remote_definition = RemoteWorkflowDefinition(
        id="wf_webhookintegration",
        alias="webhook-integration",
        webhook=RemoteWebhook(methods=["POST"], status="online"),
        definition=dsl,
    )
    async with get_async_session_context_manager() as session:
        import_service = WorkflowImportService(session=session, role=svc_role)
        result = await import_service.import_workflows_atomic(
            remote_workflows=[remote_definition],
            commit_sha="a" * 40,
        )
        assert result.success is True

        workflow_id = WorkflowUUID.new(remote_definition.id)
        webhook = (
            await session.execute(
                select(Webhook).where(Webhook.workflow_id == workflow_id)
            )
        ).scalar_one()
        webhook_secret = webhook.secret

    transport = ASGITransport(app=app)
    async with test_worker_factory(temporal_client):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/webhooks/{workflow_id.short()}/{webhook_secret}",
                json={"text": "hello"},
            )

        assert response.status_code == 200
        payload = response.json()
        wf_exec_id = payload["wf_exec_id"]

        handle = temporal_client.get_workflow_handle_for(DSLWorkflow.run, wf_exec_id)
        stored_result = await asyncio.wait_for(handle.result(), timeout=30)

    stored = StoredObjectValidator.validate_python(stored_result)
    data = await get_object_storage().retrieve(stored)
    resolved_context = await resolve_execution_context(data)

    trigger = resolved_context["TRIGGER"]
    assert isinstance(trigger, InlineObject)
    assert trigger.data == {"text": "hello"}

    action_a = resolved_context["ACTIONS"]["a"]
    assert isinstance(action_a.result, InlineObject)
    assert action_a.result.data == "hello"
