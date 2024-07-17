from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import orjson
from sqlmodel import Session, select
from temporalio.api.enums.v1 import EventType
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowHistory,
    WorkflowHistoryEventFilterType,
)

from tracecat import config, identifiers
from tracecat.contexts import ctx_role
from tracecat.db.schemas import Webhook, Workflow
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput
from tracecat.dsl.graph import RFGraph
from tracecat.dsl.validation import validate_trigger_inputs
from tracecat.dsl.workflow import DSLRunArgs, DSLWorkflow, retry_policies
from tracecat.logging import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.models import (
    CreateWorkflowExecutionResponse,
    CreateWorkflowParams,
    DispatchWorkflowResult,
    EventFailure,
    EventGroup,
    EventHistoryResponse,
    EventHistoryType,
    UpdateWorkflowParams,
)


class WorkflowExecutionsService:
    """Workflow executions service."""

    def __init__(self, client: Client, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._client = client
        self.logger = logger.bind(service="workflow_executions")

    @staticmethod
    async def connect() -> WorkflowExecutionsService:
        """Initialize and connect to the service."""
        client = await get_temporal_client()
        return WorkflowExecutionsService(client=client)

    def handle(self, wf_exec_id: identifiers.WorkflowExecutionID) -> WorkflowHandle:
        return self._client.get_workflow_handle(wf_exec_id)

    async def query_executions(
        self, query: str | None = None, **kwargs
    ) -> list[WorkflowExecutionDescription]:
        # Invoke with async for
        return [
            wf_exec
            async for wf_exec in self._client.list_workflows(query=query, **kwargs)
        ]

    async def get_execution(
        self, wf_exec_id: identifiers.WorkflowExecutionID
    ) -> WorkflowExecutionDescription:
        return await self.handle(wf_exec_id).describe()

    async def get_execution_status(
        self, wf_exec_id: identifiers.WorkflowExecutionID
    ) -> WorkflowExecutionStatus | None:
        """Get the status of a workflow execution."""

        description = await self.handle(wf_exec_id).describe()
        return description.status if description else None

    async def list_executions(self) -> list[WorkflowExecution]:
        """List all workflow executions."""

        return await self.query_executions()

    async def list_executions_by_workflow_id(
        self, wf_id: identifiers.WorkflowID
    ) -> list[WorkflowExecution]:
        """List all workflow executions by workflow ID."""

        query = f"WorkflowId STARTS_WITH {wf_id!r}"
        return await self.query_executions(query=query)

    async def get_latest_execution_by_workflow_id(
        self, wf_id: identifiers.WorkflowID
    ) -> WorkflowExecution:
        """Get the latest workflow execution by workflow ID."""

        executions = await self.list_executions_by_workflow_id(wf_id)
        return max(executions, key=lambda exec: exec.start_time)

    async def list_workflow_execution_event_history(
        self,
        wf_exec_id: identifiers.WorkflowExecutionID,
        event_filter_type: WorkflowHistoryEventFilterType = WorkflowHistoryEventFilterType.ALL_EVENT,
        **kwargs,
    ) -> list[EventHistoryResponse]:
        """List the event history of a workflow execution."""

        history = await self.handle(wf_exec_id).fetch_history(
            event_filter_type=event_filter_type, **kwargs
        )
        event_group_names: dict[int, EventGroup | None] = {}
        events = []
        for event in history.events:
            match event.event_type:
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                    run_args_data = orjson.loads(
                        event.workflow_execution_started_event_attributes.input.payloads[
                            0
                        ].data
                    )
                    dsl_run_args = DSLRunArgs(**run_args_data)
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_STARTED,
                            task_id=event.task_id,
                            role=dsl_run_args.role,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
                    result = orjson.loads(
                        event.workflow_execution_completed_event_attributes.result.payloads[
                            0
                        ].data
                    )
                    logger.warning("Workflow execution completed", result=result)
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_COMPLETED,
                            task_id=event.task_id,
                            result=result,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_FAILED,
                            task_id=event.task_id,
                            failure=EventFailure.from_history_event(event),
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                    action_event_group = EventGroup.from_scheduled_activity(event)
                    event_group_names[event.event_id] = action_event_group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_SCHEDULED,
                            task_id=event.task_id,
                            event_group=action_event_group,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
                    # The parent event here is always the scheduled event, which has the UDF name
                    parent_event_id = (
                        event.activity_task_started_event_attributes.scheduled_event_id
                    )
                    action_event_group = event_group_names.get(parent_event_id)
                    event_group_names[event.event_id] = action_event_group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_STARTED,
                            task_id=event.task_id,
                            event_group=action_event_group,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
                    # The task completiong comes with the scheduled event ID and the started event id
                    gparent_event_id = event.activity_task_completed_event_attributes.scheduled_event_id
                    action_event_group = event_group_names.get(gparent_event_id)
                    event_group_names[event.event_id] = action_event_group
                    result = orjson.loads(
                        event.activity_task_completed_event_attributes.result.payloads[
                            0
                        ].data
                    )
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_COMPLETED,
                            task_id=event.task_id,
                            event_group=action_event_group,
                            result=result,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
                    gparent_event_id = (
                        event.activity_task_failed_event_attributes.scheduled_event_id
                    )
                    action_event_group = event_group_names.get(gparent_event_id)
                    event_group_names[event.event_id] = action_event_group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_FAILED,
                            task_id=event.task_id,
                            event_group=action_event_group,
                            failure=EventFailure.from_history_event(event),
                        )
                    )
                case _:
                    continue
        return events

    async def iter_list_workflow_execution_event_history(
        self,
        wf_exec_id: identifiers.WorkflowExecutionID,
        event_filter_type: WorkflowHistoryEventFilterType = WorkflowHistoryEventFilterType.ALL_EVENT,
        **kwargs,
    ) -> AsyncGenerator[WorkflowHistory]:
        """List the event history of a workflow execution."""

        handle = self.handle(wf_exec_id)
        async for event in handle.fetch_history_events(
            page_size=10, event_filter_type=event_filter_type, **kwargs
        ):
            yield event

    def create_workflow_execution(
        self,
        dsl: DSLInput,
        *,
        wf_id: identifiers.WorkflowID,
        payload: dict[str, Any] | None = None,
        enable_runtime_tests: bool = False,
    ) -> CreateWorkflowExecutionResponse:
        """Create a new workflow execution.

        Note: This method schedules the workflow execution and returns immediately.
        """
        configured_dsl = self._configure_dsl_inputs(
            dsl=dsl, payload=payload, enable_runtime_tests=enable_runtime_tests
        )
        wf_exec_id = identifiers.workflow.exec_id(wf_id)
        _ = asyncio.create_task(
            self._dispatch_workflow(
                dsl=configured_dsl, wf_id=wf_id, wf_exec_id=wf_exec_id
            )
        )
        return CreateWorkflowExecutionResponse(
            message="Workflow execution started",
            wf_id=wf_id,
            wf_exec_id=identifiers.workflow.exec_id(wf_id),
        )

    def _configure_dsl_inputs(
        self,
        dsl: DSLInput,
        *,
        payload: dict[str, Any] | None = None,
        enable_runtime_tests: bool = False,
    ) -> DSLInput:
        # Set runtime configuration
        validation_result = validate_trigger_inputs(dsl=dsl, payload=payload)
        if validation_result.status == "error":
            logger.error(validation_result.msg, detail=validation_result.detail)
            raise TracecatValidationError(
                validation_result.msg, detail=validation_result.detail
            )

        if payload:
            dsl.trigger_inputs = payload

        dsl.config.enable_runtime_tests = enable_runtime_tests
        return dsl

    async def _dispatch_workflow(
        self,
        dsl: DSLInput,
        wf_id: identifiers.WorkflowID,
        wf_exec_id: identifiers.WorkflowExecutionID,
        **kwargs: Any,
    ) -> DispatchWorkflowResult:
        logger.info(
            f"Executing DSL workflow: {dsl.title}",
            role=self.role,
            wf_exec_id=wf_exec_id,
        )
        try:
            result = await self._client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(dsl=dsl, role=self.role, wf_id=wf_id),
                id=wf_exec_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                retry_policy=retry_policies["workflow:fail_fast"],
                **kwargs,
            )
        except WorkflowFailureError as e:
            logger.exception(str(e), role=self.role, wf_exec_id=wf_exec_id, e=e)
            raise e
        except Exception as e:
            logger.exception(
                "Workflow exception", role=self.role, wf_exec_id=wf_exec_id, e=e
            )
            raise e
        # Write result to file for debugging
        if os.getenv("DUMP_TRACECAT_RESULT", "0") in ("1", "true"):
            path = config.TRACECAT__EXECUTIONS_DIR / f"{wf_exec_id}.json"
            path.touch()
            with path.open("w") as f:
                json.dump(result, f, indent=2)
        else:
            logger.debug(f"Workflow result:\n{json.dumps(result, indent=2)}")
        return DispatchWorkflowResult(wf_id=wf_id, final_context=result)


class WorkflowsService:
    """Manages CRUD operations for Workflows."""

    def __init__(self, session: Session, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.session = session
        self.logger = logger.bind(service="workflows")

    def list_workflows(self, library: bool = False) -> list[Workflow]:
        """List all workflows."""
        query_user_id = self.role.user_id if not library else "tracecat"
        if not query_user_id:
            raise ValueError("User ID is required to list workflows")
        statement = select(Workflow).where(Workflow.owner_id == query_user_id)
        return self.session.exec(statement).all()

    def get_workflow_by_id(
        self, workflow_id: identifiers.WorkflowID
    ) -> Workflow | None:
        """Get a workflow by ID."""
        statement = select(Workflow).where(
            Workflow.owner_id == self.role.user_id,
            Workflow.id == workflow_id,
        )
        return self.session.exec(statement).one_or_none()

    def create_workflow(self, params: CreateWorkflowParams) -> Workflow:
        """Create a new workflow."""

        if not params.definition.get("title"):
            now = datetime.now().strftime("%b %d, %Y, %H:%M:%S")
            params.definition["title"] = f"Untitled Workflow - {now}"

        if not params.definition.get("description"):
            params.definition["description"] = "No description provided."

        workflow = Workflow(
            owner_id=self.role.user_id,
            meta=params.meta.model_dump(),
            definition=params.definition,
            view=params.view,
        )

        webhook = Webhook(
            owner_id=self.role.user_id,
            workflow_id=workflow.id,
        )
        graph = RFGraph.with_defaults(workflow, webhook)
        workflow.view = graph.model_dump(by_alias=True)
        self.session.add(workflow)
        self.session.add(webhook)
        self.session.commit()
        self.session.refresh(workflow)
        self.session.refresh(webhook)
        return workflow

    def update_workflow(
        self, workflow: Workflow, params: UpdateWorkflowParams
    ) -> Workflow:
        """Update a workflow."""
        update_params = params.model_dump(exclude_unset=True)
        if view := update_params.get("view"):
            workflow.view.update(view)

        if defn := update_params.get("definition"):
            workflow.definition.update(defn)

        if meta := update_params.get("meta"):
            workflow.meta.update(meta)

        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return workflow

    def delete_workflow(self, workflow_id: identifiers.WorkflowID) -> None:
        """Delete a workflow."""
        workflow = self.get_workflow_by_id(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {workflow_id}")
        self.session.delete(workflow)
        self.session.commit()
