from __future__ import annotations

import asyncio
import datetime
import json
import os
from collections.abc import AsyncGenerator
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
from tracecat.db.schemas import Workflow, WorkflowDefinition
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput
from tracecat.dsl.validation import validate_trigger_inputs
from tracecat.dsl.workflow import DSLRunArgs, DSLWorkflow, retry_policies
from tracecat.logging import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.models import (
    CreateWorkflowExecutionResponse,
    DispatchWorkflowResult,
    EventFailure,
    EventGroup,
    EventHistoryResponse,
    EventHistoryType,
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
                case EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED:
                    group = EventGroup.from_initiated_child_workflow(event)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.START_CHILD_WORKFLOW_EXECUTION_INITIATED,
                            task_id=event.task_id,
                            role=group.action_input.role,
                            input=group.action_input,
                        )
                    )
                case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED:
                    parent_event_id = event.child_workflow_execution_started_event_attributes.initiated_event_id
                    group = event_group_names.get(parent_event_id)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.CHILD_WORKFLOW_EXECUTION_STARTED,
                            task_id=event.task_id,
                            event_group=group,
                        )
                    )
                case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED:
                    result = orjson.loads(
                        event.child_workflow_execution_completed_event_attributes.result.payloads[
                            0
                        ].data
                    )
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.CHILD_WORKFLOW_EXECUTION_COMPLETED,
                            task_id=event.task_id,
                            result=result,
                        )
                    )
                case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_FAILED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.CHILD_WORKFLOW_EXECUTION_FAILED,
                            task_id=event.task_id,
                            failure=EventFailure.from_history_event(event),
                        )
                    )

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
                            input=dsl_run_args,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
                    result = orjson.loads(
                        event.workflow_execution_completed_event_attributes.result.payloads[
                            0
                        ].data
                    )
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
                    group = EventGroup.from_scheduled_activity(event)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_SCHEDULED,
                            task_id=event.task_id,
                            event_group=group,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
                    # The parent event here is always the scheduled event, which has the UDF name
                    parent_event_id = (
                        event.activity_task_started_event_attributes.scheduled_event_id
                    )
                    group = event_group_names.get(parent_event_id)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_STARTED,
                            task_id=event.task_id,
                            event_group=group,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
                    # The task completiong comes with the scheduled event ID and the started event id
                    gparent_event_id = event.activity_task_completed_event_attributes.scheduled_event_id
                    group = event_group_names.get(gparent_event_id)
                    event_group_names[event.event_id] = group
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
                            event_group=group,
                            result=result,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
                    gparent_event_id = (
                        event.activity_task_failed_event_attributes.scheduled_event_id
                    )
                    group = event_group_names.get(gparent_event_id)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_FAILED,
                            task_id=event.task_id,
                            event_group=group,
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

        validation_result = validate_trigger_inputs(dsl=dsl, payload=payload)
        if validation_result.status == "error":
            logger.error(validation_result.msg, detail=validation_result.detail)
            raise TracecatValidationError(
                validation_result.msg, detail=validation_result.detail
            )

        dsl.config.enable_runtime_tests = enable_runtime_tests
        wf_exec_id = identifiers.workflow.exec_id(wf_id)
        _ = asyncio.create_task(
            self._dispatch_workflow(
                dsl=dsl,
                wf_id=wf_id,
                wf_exec_id=wf_exec_id,
                trigger_inputs=payload,
            )
        )
        return CreateWorkflowExecutionResponse(
            message="Workflow execution started",
            wf_id=wf_id,
            wf_exec_id=identifiers.workflow.exec_id(wf_id),
        )

    async def _dispatch_workflow(
        self,
        dsl: DSLInput,
        wf_id: identifiers.WorkflowID,
        wf_exec_id: identifiers.WorkflowExecutionID,
        trigger_inputs: dict[str, Any] | None = None,
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
                DSLRunArgs(
                    dsl=dsl, role=self.role, wf_id=wf_id, trigger_inputs=trigger_inputs
                ),
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


class WorkflowDefinitionsService:
    def __init__(self, session: Session, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._session = session
        self.logger = logger.bind(service="workflow_definitions")

    def get_definition_by_workflow_id(
        self, workflow_id: identifiers.WorkflowID, *, version: int | None = None
    ) -> WorkflowDefinition | None:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == self.role.user_id,
            WorkflowDefinition.workflow_id == workflow_id,
        )
        if version:
            statement = statement.where(WorkflowDefinition.version == version)
        else:
            # Get the latest version
            statement = statement.order_by(WorkflowDefinition.version.desc())

        return self._session.exec(statement).first()

    def get_definition_by_workflow_title(
        self, workflow_title: str, *, version: int | None = None
    ) -> WorkflowDefinition | None:
        self.logger.warning(
            "Getting workflow definition by ref",
            workflow_title=workflow_title,
            role=self.role,
        )
        wf_statement = select(Workflow.id).where(
            Workflow.owner_id == self.role.user_id,
            Workflow.title == workflow_title,
        )

        wf_id = self._session.exec(wf_statement).one_or_none()
        self.logger.warning("Workflow ID", wf_id=wf_id)
        if not wf_id:
            self.logger.error("Workflow name not found", workflow_title=workflow_title)
            return None

        wf_defn_statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == self.role.user_id,
            WorkflowDefinition.workflow_id == wf_id,
        )

        if version:
            wf_defn_statement = wf_defn_statement.where(
                WorkflowDefinition.version == version
            )
        else:
            # Get the latest version
            wf_defn_statement = wf_defn_statement.order_by(
                WorkflowDefinition.version.desc()
            )

        return self._session.exec(wf_defn_statement).first()


if __name__ == "__main__":
    from dotenv import find_dotenv, load_dotenv

    from tracecat.db.engine import create_db_engine

    load_dotenv(find_dotenv())

    engine = create_db_engine()

    with Session(engine) as session:
        service = WorkflowDefinitionsService(
            session,
            role=Role(
                type="user",
                user_id="default-tracecat-user",
                service_id="tracecat-service",
            ),
        )
        res = service.get_definition_by_workflow_title("Child workflow")
        service.logger.warning("Result", res=res)
