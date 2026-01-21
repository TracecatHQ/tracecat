"""Internal workflow execution router for SDK/UDF access."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
)
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.service import RPCError

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.common import DSLInput
from tracecat.identifiers import WorkflowExecutionID, WorkflowID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService

router = APIRouter(
    prefix="/internal/workflows",
    tags=["internal-workflows"],
    include_in_schema=False,
)


class InternalWorkflowExecuteRequest(BaseModel):
    """Request to execute a workflow."""

    workflow_id: WorkflowID | None = Field(
        default=None, description="Workflow UUID (short or full format)"
    )
    workflow_alias: str | None = Field(
        default=None, description="Workflow alias (alternative to ID)"
    )
    trigger_inputs: Any | None = Field(
        default=None, description="Inputs to pass to the workflow (arbitrary JSON)"
    )
    parent_workflow_execution_id: WorkflowExecutionID | None = Field(
        default=None,
        description="Parent workflow execution ID for correlation (stored in Temporal memo). "
        "Auto-populated by the SDK from context when available.",
    )

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID | None) -> WorkflowID | None:
        """Convert any valid workflow ID format to WorkflowUUID."""
        if v is None:
            return None
        return WorkflowUUID.new(v)


class InternalWorkflowExecuteResponse(BaseModel):
    """Response from workflow execution."""

    workflow_id: WorkflowID = Field(..., description="Workflow ID")
    workflow_execution_id: WorkflowExecutionID = Field(
        ..., description="Workflow execution ID"
    )
    message: str = Field(..., description="Status message")

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


class InternalWorkflowStatusResponse(BaseModel):
    """Response for workflow execution status."""

    workflow_execution_id: WorkflowExecutionID = Field(
        ..., description="Workflow execution ID"
    )
    status: str = Field(
        ...,
        description="Execution status: RUNNING, COMPLETED, FAILED, CANCELED, TERMINATED, TIMED_OUT",
    )
    start_time: datetime | None = Field(
        default=None, description="When the execution started"
    )
    close_time: datetime | None = Field(
        default=None, description="When the execution completed (if finished)"
    )
    result: Any | None = Field(
        default=None, description="Workflow result (if completed successfully)"
    )
    error: str | None = Field(
        default=None, description="Error message (if workflow failed)"
    )


@router.post("/executions", status_code=HTTP_201_CREATED)
async def execute_workflow(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalWorkflowExecuteRequest,
) -> InternalWorkflowExecuteResponse:
    """Execute a workflow by ID or alias.

    This endpoint is used by the SDK and AI agents to execute workflows
    directly without going through the DSLWorkflow subflow machinery.
    """
    # Resolve workflow ID
    wf_id: WorkflowID | None = None

    if params.workflow_id:
        # Direct workflow ID provided
        wf_id = WorkflowUUID.new(params.workflow_id)
    elif params.workflow_alias:
        # Resolve alias to workflow ID
        wf_service = WorkflowsManagementService(session, role=role)
        wf_id = await wf_service.resolve_workflow_alias(
            params.workflow_alias, use_committed=True
        )
        if wf_id is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Workflow with alias '{params.workflow_alias}' not found",
            )
    else:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Either workflow_id or workflow_alias must be provided",
        )

    # Get workflow definition
    defn_service = WorkflowDefinitionsService(session, role=role)
    defn = await defn_service.get_definition_by_workflow_id(wf_id)
    if defn is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Workflow definition for '{wf_id}' not found. "
            "The workflow may need to be committed first.",
        )

    # Build DSL from definition content
    try:
        dsl = DSLInput(**defn.content)
    except Exception as e:
        logger.error("Failed to build DSL from definition", error=str(e))
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build DSL for workflow '{wf_id}': {e}",
        ) from e

    # Start workflow execution
    # This follows the same code path as the regular workflow execution endpoint,
    # including StoredObject handling for trigger inputs (done in _dispatch_workflow)
    try:
        exec_service = await WorkflowExecutionsService.connect(role=role)
        # Pass registry_lock from the definition, same as regular execution
        registry_lock = (
            RegistryLock.model_validate(defn.registry_lock)
            if defn.registry_lock
            else None
        )
        # Build memo for correlation (visible in Temporal UI)
        memo: dict[str, Any] | None = None
        if params.parent_workflow_execution_id:
            memo = {"parent_workflow_execution_id": params.parent_workflow_execution_id}

        response = exec_service.create_workflow_execution_nowait(
            dsl=dsl,
            wf_id=wf_id,
            payload=params.trigger_inputs,
            trigger_type=TriggerType.MANUAL,
            registry_lock=registry_lock,
            memo=memo,
        )
        logger.info(
            "Workflow execution started via internal API",
            workflow_id=wf_id,
            workflow_execution_id=response["wf_exec_id"],
        )
        return InternalWorkflowExecuteResponse(
            workflow_id=response["wf_id"],
            workflow_execution_id=response["wf_exec_id"],
            message=response["message"],
        )
    except Exception as e:
        logger.error("Failed to start workflow execution", error=str(e))
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start workflow execution: {e}",
        ) from e


@router.get("/executions/{execution_id:path}", status_code=HTTP_200_OK)
async def get_execution_status(
    *,
    role: ExecutorWorkspaceRole,
    execution_id: WorkflowExecutionID,
) -> InternalWorkflowStatusResponse:
    """Get the status of a workflow execution.

    Returns the current status, timing information, and result (if completed).
    """
    try:
        exec_service = await WorkflowExecutionsService.connect(role=role)
        execution = await exec_service.get_execution(execution_id)

        if execution is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Workflow execution '{execution_id}' not found",
            )

        # Map Temporal status to string
        status_map = {
            WorkflowExecutionStatus.RUNNING: "RUNNING",
            WorkflowExecutionStatus.COMPLETED: "COMPLETED",
            WorkflowExecutionStatus.FAILED: "FAILED",
            WorkflowExecutionStatus.CANCELED: "CANCELED",
            WorkflowExecutionStatus.TERMINATED: "TERMINATED",
            WorkflowExecutionStatus.CONTINUED_AS_NEW: "CONTINUED_AS_NEW",
            WorkflowExecutionStatus.TIMED_OUT: "TIMED_OUT",
        }
        status = (
            status_map.get(execution.status, "UNKNOWN")
            if execution.status
            else "UNKNOWN"
        )

        # Get result or error based on status
        result = None
        error = None
        if execution.status in (
            WorkflowExecutionStatus.COMPLETED,
            WorkflowExecutionStatus.FAILED,
        ):
            try:
                handle = exec_service.handle(execution_id)
                result = await handle.result()
            except WorkflowFailureError as e:
                # Extract the failure message from the workflow error
                error = str(e.cause) if e.cause else str(e)
            except Exception as e:
                logger.warning(
                    "Failed to get workflow result",
                    execution_id=execution_id,
                    error=str(e),
                )

        return InternalWorkflowStatusResponse(
            workflow_execution_id=execution_id,
            status=status,
            start_time=execution.start_time,
            close_time=execution.close_time,
            result=result,
            error=error,
        )

    except RPCError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Workflow execution '{execution_id}' not found",
            ) from e
        logger.error("RPC error getting execution status", error=str(e))
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get execution status: {e}",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get execution status", error=str(e))
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get execution status: {e}",
        ) from e
