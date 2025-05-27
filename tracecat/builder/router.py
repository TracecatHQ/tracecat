import asyncio
import uuid
from typing import Literal

import orjson
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic_core import to_jsonable_python
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from tracecat.auth.credentials import RoleACL
from tracecat.builder.models import (
    BuilderActionRun,
    BuilderActionRunResult,
    BuilderResource,
    BuilderWorkflowDefinitionValidate,
    BuilderWorkflowDefinitionValidateResult,
    BuilderWorkflowExecute,
    BuilderWorkflowExecuteResult,
)
from tracecat.concurrency import run_coro_threadsafe
from tracecat.config import TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES
from tracecat.contexts import ctx_logger
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput, DSLRunArgs, create_default_execution_context
from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext
from tracecat.dsl.worker import get_activities, new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.executor.models import ExecutorActionErrorInfo
from tracecat.executor.service import dispatch_action_on_cluster
from tracecat.expressions.expectations import type_grammar as expectation_grammar
from tracecat.expressions.parser.grammar import grammar as expression_grammar
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionValidateResponse
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    ExecutionError,
    LoopExecutionError,
    PayloadSizeExceeded,
    TracecatSettingsError,
)
from tracecat.validation.service import validate_dsl

router = APIRouter(prefix="/builder", tags=["builder"])


@router.get("/resources/{resource_id}")
async def get_resource(
    *,
    resource_id: Literal["grammar", "workflow-definition", "action-template"],
) -> BuilderResource:
    """Get a resource by ID."""
    if resource_id == "grammar":
        data = {
            "expressions": expression_grammar,
            "expectations": expectation_grammar,
        }
        content_type = "application/vnd.tc+json"
    elif resource_id == "workflow-definition":
        data = DSLInput.model_json_schema()
        content_type = "application/vnd.tc+json"
    elif resource_id == "action-template":
        raise NotImplementedError("Action templates are not yet implemented")
        # data = ActionTemplate.model_json_schema()
        # content_type = "application/vnd.tc.action+json"
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource {resource_id} not found",
        )
    return BuilderResource(
        id=resource_id,
        content_type=content_type,
        data=data,
    )


@router.post("/actions/run", status_code=status.HTTP_200_OK)
async def run_action(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
    ),
    session: AsyncDBSession,
    action_input: BuilderActionRun,
) -> BuilderActionRunResult:
    """Run an action in a builder environment."""
    action = action_input.action
    log = logger.bind(role=role, action_name=action)
    ctx_logger.set(log)

    log.info("Running action in builder context", role=role)

    wf_id = WorkflowUUID.new_uuid4()
    input = RunActionInput(
        task=ActionStatement(ref="standalone", action=action, args=action_input.args),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=generate_exec_id(wf_id),
            wf_run_id=uuid.uuid4(),
            environment="standalone",
        ),
    )
    try:
        result = await dispatch_action_on_cluster(input=input, session=session)
        serialized = orjson.dumps(result, default=to_jsonable_python)
        ser_size = len(serialized)
        if ser_size > TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES:
            raise PayloadSizeExceeded(
                f"The action's return value exceeds the size limit of"
                f" {TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES / 1000}KB"
            )
        return BuilderActionRunResult(
            status="success",
            message="Action executed successfully",
            result=result,
        )
    except ExecutionError as e:
        return BuilderActionRunResult(
            status="error",
            message=str(e),
            result=None,
            error=e.info.model_dump(mode="json"),
        )
    except LoopExecutionError as e:
        return BuilderActionRunResult(
            status="error",
            message=str(e),
            result=None,
            error=[e.info.model_dump(mode="json") for e in e.loop_errors],
        )
    except PayloadSizeExceeded as e:
        return BuilderActionRunResult(
            status="error",
            message=str(e),
            result=None,
            error=str(e),
        )

    # Platform errors
    except (TracecatSettingsError, orjson.JSONDecodeError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": str(e)},
        ) from e
    except Exception as e:
        logger.warning("Unexpected error running action", exc_info=e)
        err_info = ExecutorActionErrorInfo.from_exc(e, action)
        err_info_dict = err_info.model_dump(mode="json")
        log.error("Error running action", **err_info_dict)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_info_dict,
        ) from e


@router.post("/workflows/validate-definition")
async def validate_workflow_definition(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",  # Need to validate secrets
    ),
    session: AsyncDBSession,
    params: BuilderWorkflowDefinitionValidate,
) -> BuilderWorkflowDefinitionValidateResult:
    """Validate a workflow definition."""
    try:
        results = await validate_dsl(session=session, dsl=params.dsl)
    except Exception as e:
        return BuilderWorkflowDefinitionValidateResult(
            status="error",
            message=str(e),
        )

    return BuilderWorkflowDefinitionValidateResult(
        status="success",
        message="Workflow definition validated successfully",
        errors=[
            RegistryActionValidateResponse.from_validation_result(r) for r in results
        ],
    )


def get_builder_loop(request: Request) -> asyncio.AbstractEventLoop:
    return request.app.state.builder_loop


@router.post("/workflows/execute")
async def execute_workflow(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
    params: BuilderWorkflowExecute,
    builder_loop: asyncio.AbstractEventLoop = Depends(get_builder_loop),
) -> BuilderWorkflowExecuteResult:
    """Execute a workflow."""
    logger.info("Executing workflow in builder", role=role, params=params)

    result = await run_coro_threadsafe(
        _run_workflow_async(params, role),
        other_loop=builder_loop,
    )
    logger.info("Done workflow execution", result=result)
    return result


async def _run_workflow_async(
    params: BuilderWorkflowExecute, role: Role
) -> BuilderWorkflowExecuteResult:
    """Run a workflow in a"""
    client = await get_temporal_client()
    wf_id = WorkflowUUID.new_uuid4()
    try:
        async with Worker(
            client,
            task_queue="tracecat-builder-task-queue",
            activities=get_activities(),
            workflows=[DSLWorkflow],
            workflow_runner=new_sandbox_runner(),
        ):
            result = await client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(
                    dsl=params.dsl,
                    role=role,
                    wf_id=wf_id,
                    trigger_inputs=params.trigger_inputs,
                ),
                id=f"{generate_exec_id(wf_id)}/builder",
                task_queue="tracecat-builder-task-queue",
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
    except Exception as e:
        return BuilderWorkflowExecuteResult(
            wf_id=wf_id.short(),
            status="error",
            message=f"Workflow execution failed: {e}",
            result=None,
        )
    else:
        return BuilderWorkflowExecuteResult(
            wf_id=wf_id.short(),
            status="success",
            message="Workflow ran successfully",
            result=result,
        )
