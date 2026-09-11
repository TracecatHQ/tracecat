"""Temporal-backed registry UDF execution for trusted MCP calls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import anyio
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.common import SearchAttributePair, TypedSearchAttributes
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from tracecat_ee.agent.workflows.registry_tool import ExecuteRegistryToolWorkflow

from tracecat import config
from tracecat.agent.tokens import MCPTokenClaims
from tracecat.agent.workflows.tool_execution import (
    AGENT_TOOL_PRIORITY,
    ExecuteRegistryToolWorkflowInput,
    ExecuteRegistryToolWorkflowMemo,
    build_agent_tool_workflow_id,
)
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.identifiers import WorkflowUUID
from tracecat.identifiers.workflow import ExecutionUUID
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import (
    StoredObject,
    StoredObjectValidator,
    retrieve_stored_object,
)
from tracecat.workflow.executions.correlation import build_agent_session_correlation_id
from tracecat.workflow.executions.enums import TemporalSearchAttr

_WORKFLOW_RPC_TIMEOUT = timedelta(seconds=10)


class ActionNotAllowedError(Exception):
    """Raised when an action is not in the allowed actions list."""


class ActionExecutionError(Exception):
    """Raised when action execution fails."""


def build_tracecat_mcp_role(
    *,
    workspace_id: UUID | None,
    organization_id: UUID | None,
    user_id: UUID | None,
) -> Role:
    """Build the trusted MCP service role for action execution."""
    return Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-mcp"],
    )


def build_role_from_claims(claims: MCPTokenClaims) -> Role:
    """Reconstruct the trusted service role from MCP token claims."""
    return build_tracecat_mcp_role(
        workspace_id=claims.workspace_id,
        organization_id=claims.organization_id,
        user_id=claims.user_id,
    )


async def execute_action(
    action_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
    registry_lock: RegistryLock,
    tool_call_id: str | None = None,
) -> Any:
    """Execute a registry UDF through the shared executor queue.

    Args:
        action_name: The action to execute (e.g., "core.http_request")
        args: Arguments to pass to the action
        claims: Token claims containing role and allowed_actions
        registry_lock: Registry lock with origin→version mappings for action resolution

    Returns:
        The action result

    Raises:
        ActionNotAllowedError: If action is not in allowed_actions
        ActionExecutionError: If execution fails
    """
    # Validate action is allowed
    if action_name not in claims.allowed_actions:
        logger.warning(
            "Action not allowed",
            action_name=action_name,
            allowed_actions=claims.allowed_actions,
        )
        raise ActionNotAllowedError(
            f"Action '{action_name}' is not in allowed actions for this token"
        )

    role = build_role_from_claims(claims)
    ctx_role.set(role)

    logger.info("Executing action via executor workflow", action_name=action_name)

    run_input = build_run_input(
        action_name,
        args,
        registry_lock,
        agent_session_id=claims.session_id,
    )
    stored = await _execute_action_workflow(
        ExecuteRegistryToolWorkflowInput(role=role, run_input=run_input),
        workflow_id=build_agent_tool_workflow_id(),
        memo=ExecuteRegistryToolWorkflowMemo(
            parent_agent_workflow_id=claims.parent_agent_workflow_id,
            parent_agent_run_id=claims.parent_agent_run_id,
            parent_agent_session_id=claims.session_id,
            tool_call_id=tool_call_id,
            action_name=action_name,
        ),
        search_attributes=TypedSearchAttributes(
            search_attributes=[
                build_tool_workflow_correlation_attr(claims.session_id),
                *build_tool_workflow_alias_attrs(tool_call_id),
                build_tool_workflow_workspace_attr(claims),
                *build_tool_workflow_user_attrs(claims),
            ]
        ),
    )
    return await retrieve_stored_object(stored)


def build_run_input(
    action_name: str,
    args: dict[str, Any],
    registry_lock: RegistryLock,
    *,
    workflow_id: UUID | None = None,
    run_id: UUID | None = None,
    execution_id: UUID | None = None,
    logical_time: datetime | None = None,
    environment: str = "default",
    agent_session_id: UUID | None = None,
) -> RunActionInput:
    """Build a minimal RunActionInput for ActionRunner.

    Args:
        action_name: The action to execute
        args: Arguments for the action
        registry_lock: Registry lock with origin→version mappings for action resolution
        agent_session_id: Verified agent session provenance, when available.
    """
    task = ActionStatement(
        ref=f"mcp_{action_name.replace('.', '_')}",
        action=action_name,
        args=args,
    )

    wf_id = WorkflowUUID.from_uuid(workflow_id or uuid4())
    run_context = RunContext(
        wf_id=wf_id,
        wf_run_id=run_id or uuid4(),
        wf_exec_id=f"{wf_id.short()}/{ExecutionUUID.from_uuid(execution_id or uuid4()).short()}",
        environment=environment,
        logical_time=logical_time or datetime.now(UTC),
    )

    return RunActionInput(
        task=task,
        run_context=run_context,
        exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        agent_session_id=agent_session_id,
        registry_lock=registry_lock,
    )


async def _execute_action_workflow(
    input: ExecuteRegistryToolWorkflowInput,
    *,
    workflow_id: str,
    memo: ExecuteRegistryToolWorkflowMemo,
    search_attributes: TypedSearchAttributes,
) -> StoredObject:
    """Execute a single registry UDF via a short workflow on agent-worker."""
    client = await get_temporal_client()

    async def start() -> WorkflowHandle[ExecuteRegistryToolWorkflow, StoredObject]:
        try:
            return await client.start_workflow(
                ExecuteRegistryToolWorkflow.run,
                input,
                id=workflow_id,
                task_queue=config.TRACECAT__AGENT_QUEUE,
                priority=AGENT_TOOL_PRIORITY,
                memo=memo.model_dump(mode="json", exclude_none=True),
                search_attributes=search_attributes,
                rpc_timeout=_WORKFLOW_RPC_TIMEOUT,
            )
        except WorkflowAlreadyStartedError as error:
            return client.get_workflow_handle_for(
                ExecuteRegistryToolWorkflow.run,
                workflow_id,
                first_execution_run_id=error.run_id,
            )

    # A cancelled start RPC can still create the workflow on the server. Keep
    # the request alive so cleanup can cancel the run returned by Temporal.
    start_task = asyncio.create_task(start())
    try:
        handle = await asyncio.shield(start_task)
        stored = await handle.result()
    except (asyncio.CancelledError, RPCError):
        cleanup = asyncio.create_task(
            _cancel_started_workflow(
                start_task,
                client.get_workflow_handle_for(
                    ExecuteRegistryToolWorkflow.run, workflow_id
                ),
            )
        )
        # MCP uses AnyIO cancellation scopes; repeated asyncio cancellation
        # must also leave cleanup alive until its bounded RPCs finish.
        with anyio.CancelScope(shield=True):
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
        raise
    except WorkflowFailureError as error:
        cause = error.cause
        message = str(cause) if isinstance(cause, ApplicationError) else str(error)
        raise ActionExecutionError(message) from error
    return StoredObjectValidator.validate_python(stored)


async def _cancel_started_workflow(
    start_task: asyncio.Task[WorkflowHandle[ExecuteRegistryToolWorkflow, StoredObject]],
    fallback_handle: WorkflowHandle[ExecuteRegistryToolWorkflow, StoredObject],
) -> None:
    """Request cancellation without replacing the caller's original failure."""
    try:
        try:
            handle = await start_task
        except RPCError:
            # A lost start response leaves acceptance uncertain. The fresh,
            # per-call workflow ID still identifies any accepted execution.
            handle = fallback_handle
        await handle.cancel(rpc_timeout=_WORKFLOW_RPC_TIMEOUT)
    except Exception as error:
        if isinstance(error, RPCError) and error.status == RPCStatusCode.NOT_FOUND:
            return
        logger.warning(
            "Failed to cancel registry tool workflow",
            workflow_id=fallback_handle.id,
            error_type=type(error).__name__,
        )


def build_tool_workflow_correlation_attr(session_id: UUID) -> SearchAttributePair[str]:
    """Build the grouped correlation search attribute for registry tool workflows."""
    return TemporalSearchAttr.CORRELATION_ID.create_pair(
        build_agent_session_correlation_id(session_id)
    )


def build_tool_workflow_alias_attrs(
    tool_call_id: str | None,
) -> list[SearchAttributePair[str]]:
    """Build optional harness-specific alias search attributes for registry tools."""
    if tool_call_id is None:
        return []
    return [TemporalSearchAttr.ALIAS.create_pair(f"cc:{tool_call_id}")]


def build_tool_workflow_workspace_attr(
    claims: MCPTokenClaims,
) -> SearchAttributePair[str]:
    """Build the workspace search attribute for registry tool workflows."""
    return TemporalSearchAttr.WORKSPACE_ID.create_pair(str(claims.workspace_id))


def build_tool_workflow_user_attrs(
    claims: MCPTokenClaims,
) -> list[SearchAttributePair[str]]:
    """Build optional user search attributes for registry tool workflows."""
    if claims.user_id is None:
        return []
    return [TemporalSearchAttr.TRIGGERED_BY_USER_ID.create_pair(str(claims.user_id))]
