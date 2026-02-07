from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import status
from sqlalchemy import select

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Webhook
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import RemoteWebhook, RemoteWorkflowDefinition

pytestmark = [
    pytest.mark.usefixtures("db"),
    pytest.mark.usefixtures("registry_version_with_manifest"),
]


async def _import_webhook_workflow(role: Role) -> tuple[str, str]:
    workflow_id = WorkflowUUID.new_uuid4().short()
    remote_workflow = RemoteWorkflowDefinition(
        id=workflow_id,
        alias=f"webhook-start-ack-{uuid.uuid4().hex[:8]}",
        webhook=RemoteWebhook(methods=["POST"], status="online"),
        definition=DSLInput(
            title="Webhook Start Ack Integration",
            description="Validates webhook status reflects workflow start outcome.",
            entrypoint=DSLEntrypoint(ref="start"),
            actions=[
                ActionStatement(
                    ref="start",
                    action="core.transform.reshape",
                    args={"value": "ok"},
                )
            ],
        ),
    )

    async with get_async_session_context_manager() as session:
        importer = WorkflowImportService(session=session, role=role)
        result = await importer.import_workflows_atomic(
            remote_workflows=[remote_workflow],
            commit_sha="a" * 40,
        )
        assert result.success is True

        webhook_result = await session.execute(
            select(Webhook).where(Webhook.workflow_id == WorkflowUUID.new(workflow_id))
        )
        webhook = webhook_result.scalar_one()
        return workflow_id, webhook.secret


@pytest.mark.anyio
async def test_webhook_returns_500_when_temporal_start_fails(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id, secret = await _import_webhook_workflow(svc_role)

    class FailingTemporalClient:
        async def start_workflow(self, *_args, **_kwargs):
            raise RuntimeError("simulated temporal start failure")

    async def fake_get_temporal_client(*_args, **_kwargs):
        return FailingTemporalClient()

    monkeypatch.setattr(
        "tracecat.workflow.executions.service.get_temporal_client",
        fake_get_temporal_client,
    )

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/webhooks/{workflow_id}/{secret}",
            json={"text": "hello"},
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
