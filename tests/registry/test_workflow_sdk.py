"""Tests for the Workflows SDK client.

These tests use mocking and don't require database or other infrastructure.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.sdk.workflows import WorkflowsClient


@pytest.fixture
def mock_tracecat_client() -> MagicMock:
    """Create a mock TracecatClient."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.patch = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.fixture
def workflows_client(mock_tracecat_client: MagicMock) -> WorkflowsClient:
    """Create a WorkflowsClient with mocked HTTP client."""
    return WorkflowsClient(mock_tracecat_client)


class TestWorkflowsClientExecute:
    """Tests for WorkflowsClient.execute()."""

    @pytest.mark.anyio
    async def test_execute_detach_with_alias(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test execute with detach strategy using alias."""
        mock_tracecat_client.post.return_value = {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "message": "Workflow execution started",
        }

        result = await workflows_client.execute(
            workflow_alias="my-workflow",
            trigger_inputs={"key": "value"},
        )

        assert result == {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "status": "STARTED",
        }
        mock_tracecat_client.post.assert_called_once_with(
            "/workflows/executions",
            json={
                "workflow_alias": "my-workflow",
                "trigger_inputs": {"key": "value"},
            },
        )

    @pytest.mark.anyio
    async def test_execute_detach_with_id(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test execute with detach strategy using workflow ID."""
        mock_tracecat_client.post.return_value = {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "message": "Workflow execution started",
        }

        result = await workflows_client.execute(
            workflow_id="wf-00000000000000000000000000000123",
        )

        assert result["workflow_id"] == "wf-00000000000000000000000000000123"
        assert result["status"] == "STARTED"

    @pytest.mark.anyio
    async def test_execute_requires_id_or_alias(
        self, workflows_client: WorkflowsClient
    ):
        """Test that execute requires either workflow_id or workflow_alias."""
        with pytest.raises(ValueError, match="Either workflow_id or workflow_alias"):
            await workflows_client.execute()


class TestWorkflowsClientGetStatus:
    """Tests for WorkflowsClient.get_status()."""

    @pytest.mark.anyio
    async def test_get_status(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test get_status returns execution status."""
        mock_tracecat_client.get.return_value = {
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "status": "RUNNING",
            "start_time": "2024-01-01T00:00:00Z",
            "close_time": None,
            "result": None,
        }

        result = await workflows_client.get_status(
            "wf-00000000000000000000000000000123/exec-456"
        )

        assert result["status"] == "RUNNING"
        mock_tracecat_client.get.assert_called_once_with(
            "/workflows/executions/wf-00000000000000000000000000000123/exec-456"
        )


class TestWorkflowsClientListExecutions:
    """Tests for WorkflowsClient.list_executions()."""

    @pytest.mark.anyio
    async def test_list_executions_defaults_omit_cursor(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test list_executions hits the workflow executions route."""
        mock_tracecat_client.get.return_value = {
            "items": [],
            "next_cursor": None,
            "prev_cursor": None,
            "has_more": False,
            "has_previous": False,
        }

        result = await workflows_client.list_executions(workflow_id="wf_abc")

        assert result["items"] == []
        mock_tracecat_client.get.assert_called_once_with(
            "/workflows/wf_abc/executions",
            params={"limit": 20},
        )

    @pytest.mark.anyio
    async def test_list_executions_passes_limit_and_cursor(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test list_executions forwards pagination params."""
        mock_tracecat_client.get.return_value = {"items": []}

        await workflows_client.list_executions(
            workflow_id="wf_abc", limit=5, cursor="opaque-cursor"
        )

        mock_tracecat_client.get.assert_called_once_with(
            "/workflows/wf_abc/executions",
            params={"limit": 5, "cursor": "opaque-cursor"},
        )


class TestWorkflowsClientGetStatusWithEvents:
    """Tests for WorkflowsClient.get_status(include_events=True)."""

    @pytest.mark.anyio
    async def test_get_status_requests_events(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test include_events hits the status route with the events param."""
        mock_tracecat_client.get.return_value = {
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "status": "COMPLETED",
            "history_length": 12,
            "events": [],
        }

        result = await workflows_client.get_status(
            "wf-00000000000000000000000000000123/exec-456",
            include_events=True,
        )

        assert result.get("history_length") == 12
        mock_tracecat_client.get.assert_called_once_with(
            "/workflows/executions/wf-00000000000000000000000000000123/exec-456",
            params={"include_events": True},
        )


class TestWorkflowsClientCreate:
    """Tests for WorkflowsClient.create_workflow()."""

    @pytest.mark.anyio
    async def test_create_empty_omits_definition(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Create without definition_yaml posts only title/description."""
        mock_tracecat_client.post.return_value = {"id": "wf_abc", "title": "T"}

        await workflows_client.create_workflow(title="T", description="D")

        mock_tracecat_client.post.assert_called_once_with(
            "/workflows",
            json={"title": "T", "description": "D"},
        )

    @pytest.mark.anyio
    async def test_create_with_definition_yaml(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Create with definition_yaml forwards it in the body."""
        mock_tracecat_client.post.return_value = {"id": "wf_abc", "title": "T"}

        await workflows_client.create_workflow(
            title="T", definition_yaml="definition:\n  title: T\n"
        )

        mock_tracecat_client.post.assert_called_once_with(
            "/workflows",
            json={"title": "T", "definition_yaml": "definition:\n  title: T\n"},
        )


class TestWorkflowsClientGetWorkflow:
    """Tests for WorkflowsClient.get_workflow()."""

    @pytest.mark.anyio
    async def test_get_workflow_hits_edit_document(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """get_workflow reads the draft edit-document endpoint."""
        mock_tracecat_client.get.return_value = {
            "workflow_id": "wf_abc",
            "draft_revision": "rev1",
            "draft_document": {},
        }

        result = await workflows_client.get_workflow(workflow_id="wf_abc")

        assert result["draft_revision"] == "rev1"
        mock_tracecat_client.get.assert_called_once_with(
            "/workflows/wf_abc/edit-document"
        )


class TestWorkflowsClientEditWorkflow:
    """Tests for WorkflowsClient.edit_workflow()."""

    @pytest.mark.anyio
    async def test_edit_workflow_patches_edit_document(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """edit_workflow PATCHes the edit-document endpoint with the patch body."""
        mock_tracecat_client.patch.return_value = {
            "message": "ok",
            "workflow_id": "wf_abc",
            "draft_revision": "rev2",
        }
        patch_ops = [{"op": "add", "path": "/definition/actions/-", "value": {}}]

        result = await workflows_client.edit_workflow(
            workflow_id="wf_abc",
            base_revision="rev1",
            patch_ops=patch_ops,
            validate_only=True,
        )

        assert result["draft_revision"] == "rev2"
        mock_tracecat_client.patch.assert_called_once_with(
            "/workflows/wf_abc/edit-document",
            json={
                "base_revision": "rev1",
                "patch_ops": patch_ops,
                "validate_only": True,
            },
        )


class TestWorkflowsClientPublish:
    """Tests for WorkflowsClient.publish()."""

    @pytest.mark.anyio
    async def test_publish_posts_to_publish_route(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """publish POSTs the workflow publish endpoint and returns the new version."""
        mock_tracecat_client.post.return_value = {
            "workflow_id": "wf_abc",
            "version": 3,
            "message": "Workflow published successfully",
        }

        result = await workflows_client.publish(workflow_id="wf_abc")

        assert result["version"] == 3
        mock_tracecat_client.post.assert_called_once_with("/workflows/wf_abc/publish")


class TestWorkflowsClientWebhook:
    """Tests for WorkflowsClient webhook methods."""

    @pytest.mark.anyio
    async def test_get_webhook_hits_webhook_route(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """get_webhook reads the workflow webhook endpoint."""
        mock_tracecat_client.get.return_value = {
            "status": "online",
            "url": "https://example/webhook",
        }

        result = await workflows_client.get_webhook(workflow_id="wf_abc")

        assert result["status"] == "online"
        mock_tracecat_client.get.assert_called_once_with("/workflows/wf_abc/webhook")

    @pytest.mark.anyio
    async def test_update_webhook_patches_webhook_route(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """update_webhook PATCHes the webhook endpoint with the status body."""
        mock_tracecat_client.patch.return_value = None

        await workflows_client.update_webhook(workflow_id="wf_abc", status="online")

        mock_tracecat_client.patch.assert_called_once_with(
            "/workflows/wf_abc/webhook",
            json={"status": "online"},
        )


class TestWorkflowsClientCaseTrigger:
    """Tests for WorkflowsClient case-trigger methods."""

    @pytest.mark.anyio
    async def test_get_case_trigger_hits_case_trigger_route(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """get_case_trigger reads the workflow case-trigger endpoint."""
        mock_tracecat_client.get.return_value = {
            "status": "online",
            "event_types": ["case_created"],
            "tag_filters": [],
        }

        result = await workflows_client.get_case_trigger(workflow_id="wf_abc")

        assert result["status"] == "online"
        mock_tracecat_client.get.assert_called_once_with(
            "/workflows/wf_abc/case-trigger"
        )

    @pytest.mark.anyio
    async def test_update_case_trigger_omits_unset_fields(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """update_case_trigger PATCHes only the provided fields."""
        mock_tracecat_client.patch.return_value = None

        await workflows_client.update_case_trigger(
            workflow_id="wf_abc",
            status="online",
            event_types=["case_created"],
        )

        mock_tracecat_client.patch.assert_called_once_with(
            "/workflows/wf_abc/case-trigger",
            json={"status": "online", "event_types": ["case_created"]},
        )


class TestWorkflowsClientGetAuthoringContext:
    """Tests for WorkflowsClient.get_authoring_context()."""

    @pytest.mark.anyio
    async def test_get_authoring_context_by_names(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """By-name lookup POSTs only action_names to the authoring-context route."""
        mock_tracecat_client.post.return_value = {
            "actions": [],
            "variable_hints": [],
            "secret_hints": [],
        }

        result = await workflows_client.get_authoring_context(
            action_names=["core.http_request", "ai.agent"]
        )

        assert result == {"actions": [], "variable_hints": [], "secret_hints": []}
        mock_tracecat_client.post.assert_called_once_with(
            "/workflows/authoring-context",
            json={"action_names": ["core.http_request", "ai.agent"]},
        )

    @pytest.mark.anyio
    async def test_get_authoring_context_by_query(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """By-query lookup POSTs only the query to the authoring-context route."""
        mock_tracecat_client.post.return_value = {
            "actions": [],
            "variable_hints": [],
            "secret_hints": [],
        }

        await workflows_client.get_authoring_context(query="reshape")

        mock_tracecat_client.post.assert_called_once_with(
            "/workflows/authoring-context",
            json={"query": "reshape"},
        )


class TestWorkflowExecutionIDPattern:
    """Pin the registry's execution ID pattern to the tracecat source of truth."""

    def test_pattern_matches_tracecat_schema_pattern(self):
        """The registry copy must stay byte-identical to WF_EXEC_ID_SCHEMA_PATTERN."""
        from tracecat_registry._internal.models import WF_EXEC_ID_PATTERN

        from tracecat.identifiers.workflow import WF_EXEC_ID_SCHEMA_PATTERN

        assert WF_EXEC_ID_PATTERN == WF_EXEC_ID_SCHEMA_PATTERN


class TestWorkflowsClientDeleteTag:
    @pytest.mark.anyio
    async def test_delete_tag(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        await workflows_client.delete_tag("triage")

        mock_tracecat_client.delete.assert_awaited_once_with("/tags/triage")


class TestCoreDeleteWorkflowTag:
    @pytest.mark.anyio
    async def test_delete_workflow_tag(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from tracecat_registry.core.workflow import delete_workflow_tag

        workflows = AsyncMock()
        workflows.aio = workflows
        with patch(
            "tracecat_registry.ctx.get_context",
            return_value=SimpleNamespace(workflows=workflows),
        ):
            result = await delete_workflow_tag(tag="triage")

        workflows.delete_tag.assert_awaited_once_with("triage")
        assert result is None


class TestCoreExecuteDirect:
    @pytest.mark.anyio
    async def test_direct_execute_never_waits(self):
        """A direct call starts the child and returns; `wait` is DSL-only."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from tracecat_registry.core.workflow import execute

        started = {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "status": "STARTED",
        }
        workflows = MagicMock()
        workflows.execute = AsyncMock(return_value=started)
        workflows.get_status = AsyncMock()
        context = SimpleNamespace(workflows=workflows, wf_exec_id="wf-parent/exec-1")
        with patch("tracecat_registry.core.workflow.get_context", return_value=context):
            result = await execute(
                workflow_alias="child",
                trigger_inputs={"key": "value"},
                wait_strategy="wait",
                timeout=60,
            )

        assert result["status"] == "STARTED"
        assert "never wait" in result["message"]
        workflows.execute.assert_awaited_once_with(
            workflow_alias="child",
            trigger_inputs={"key": "value"},
            environment=None,
            parent_workflow_execution_id="wf-parent/exec-1",
        )
        workflows.get_status.assert_not_awaited()
