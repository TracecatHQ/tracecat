from __future__ import annotations

from typing import Any, Literal
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowHandle

from tracecat.identifiers.workflow import WorkflowExecutionID
from tracecat.storage.object import CollectionObject, InlineObject, ObjectRef
from tracecat.workflow.executions.service import WorkflowExecutionsService


@pytest.fixture
def workflow_exec_id() -> WorkflowExecutionID:
    return "test-workflow-execution-123"


@pytest.fixture
def workflow_executions_service() -> WorkflowExecutionsService:
    return WorkflowExecutionsService(client=Mock(spec=Client), role=None)


def create_mock_completed_event(*, event_id: int, scheduled_event_id: int) -> Mock:
    event = Mock()
    event.event_id = event_id
    event.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
    completed_attrs = Mock()
    completed_attrs.scheduled_event_id = scheduled_event_id
    event.activity_task_completed_event_attributes = completed_attrs
    return event


def create_collection_object(
    *,
    count: int = 3,
    element_kind: Literal["value", "stored_object"] = "stored_object",
) -> CollectionObject:
    return CollectionObject(
        manifest_ref=ObjectRef(
            bucket="test-bucket",
            key="wf/test/manifest.json",
            size_bytes=128,
            sha256="abc123",
        ),
        count=count,
        chunk_size=256,
        element_kind=element_kind,
    )


@pytest.mark.anyio
class TestWorkflowExecutionObjectResolution:
    async def test_resolve_completed_event_matches_source_event_id(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        completed_event = create_mock_completed_event(event_id=11, scheduled_event_id=3)

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**_kwargs: Any):
            yield completed_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        matched = await workflow_executions_service._resolve_completed_event(
            workflow_exec_id,
            3,
        )

        assert matched is completed_event

    async def test_get_collection_page_returns_refs_for_stored_object_collection(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        collection = create_collection_object(element_kind="stored_object")
        stored_item = InlineObject(data={"foo": "bar"}).model_dump(mode="json")

        with (
            patch.object(
                workflow_executions_service,
                "get_collection_action_result",
                AsyncMock(return_value=collection),
            ),
            patch(
                "tracecat.workflow.executions.service.get_storage_collection_page",
                AsyncMock(return_value=[stored_item]),
            ) as mock_get_page,
        ):
            (
                resolved_collection,
                items,
            ) = await workflow_executions_service.get_collection_page(
                workflow_exec_id,
                101,
                offset=0,
                limit=25,
            )

        assert resolved_collection == collection
        assert items == [stored_item]
        mock_get_page.assert_awaited_once_with(collection, offset=0, limit=25)

    async def test_get_collection_item_for_object_ops_returns_stored_object_handle(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        collection = create_collection_object(element_kind="stored_object")
        stored_item = InlineObject(data={"foo": "bar"}).model_dump(mode="json")

        with patch.object(
            workflow_executions_service,
            "get_collection_page",
            AsyncMock(return_value=(collection, [stored_item])),
        ):
            item = await workflow_executions_service.get_collection_item_for_object_ops(
                workflow_exec_id,
                99,
                index=0,
            )

        assert isinstance(item, InlineObject)
        assert item.data == {"foo": "bar"}

    async def test_get_collection_item_for_object_ops_returns_inline_value(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        collection = create_collection_object(element_kind="value")

        with patch.object(
            workflow_executions_service,
            "get_collection_page",
            AsyncMock(return_value=(collection, [{"n": 1}])),
        ):
            item = await workflow_executions_service.get_collection_item_for_object_ops(
                workflow_exec_id,
                99,
                index=0,
            )

        assert item == {"n": 1}
