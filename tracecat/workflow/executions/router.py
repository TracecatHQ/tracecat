from typing import Any

import temporalio.service
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

import tracecat.agent.adapter.vercel
from tracecat.agent.schemas import AgentOutput
from tracecat.agent.types import ClaudeSDKMessageTA
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.auth.enums import SpecialUserID
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import WorkflowDefinition
from tracecat.dsl.common import (
    DSLInput,
    get_execution_type_from_search_attr,
    get_trigger_type_from_search_attr,
)
from tracecat.ee.interactions.schemas import InteractionRead
from tracecat.ee.interactions.service import InteractionService
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import UserID
from tracecat.identifiers.workflow import OptionalAnyWorkflowIDQuery, WorkflowUUID
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.settings.service import get_setting
from tracecat.workflow.executions.dependencies import UnquotedExecutionID
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.schemas import (
    WorkflowExecutionCreate,
    WorkflowExecutionCreateResponse,
    WorkflowExecutionRead,
    WorkflowExecutionReadCompact,
    WorkflowExecutionReadMinimal,
    WorkflowExecutionTerminate,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.management import WorkflowsManagementService

router = APIRouter(prefix="/workflow-executions", tags=["workflow-executions"])


async def _list_interactions(
    session: AsyncSession,
    execution_id: UnquotedExecutionID,
) -> list[InteractionRead]:
    if await get_setting("app_interactions_enabled", default=False):
        svc = InteractionService(session=session)
        interactions = await svc.list_interactions(wf_exec_id=execution_id)
        return [
            InteractionRead(
                id=interaction.id,
                wf_exec_id=interaction.wf_exec_id,
                type=interaction.type,
                status=interaction.status,
                request_payload=interaction.request_payload,
                response_payload=interaction.response_payload,
                expires_at=interaction.expires_at,
                created_at=interaction.created_at,
                updated_at=interaction.updated_at,
                actor=interaction.actor,
                action_ref=interaction.action_ref,
                action_type=interaction.action_type,
            )
            for interaction in interactions
        ]
    else:
        logger.debug("Interactions are disabled, skipping interaction states")
        return []


@router.get("")
async def list_workflow_executions(
    role: WorkspaceUserRole,
    # Filters
    workflow_id: OptionalAnyWorkflowIDQuery,
    trigger_types: set[TriggerType] | None = Query(None, alias="trigger"),
    triggered_by_user_id: UserID | SpecialUserID | None = Query(None, alias="user_id"),
    limit: int | None = Query(None),
) -> list[WorkflowExecutionReadMinimal]:
    """List all workflow executions."""
    service = await WorkflowExecutionsService.connect(role=role)
    if triggered_by_user_id == SpecialUserID.CURRENT:
        if role.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User ID is required to filter by user ID",
            )
        triggered_by_user_id = role.user_id
    limit = limit or await get_setting("app_executions_query_limit") or 100
    executions = await service.list_executions(
        workflow_id=workflow_id,
        trigger_types=trigger_types,
        triggered_by_user_id=triggered_by_user_id,
        limit=limit,
    )
    return [
        WorkflowExecutionReadMinimal.from_dataclass(execution)
        for execution in executions
    ]


@router.get("/{execution_id}")
async def get_workflow_execution(
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
    session: AsyncDBSession,
) -> WorkflowExecutionRead:
    """Get a workflow execution."""
    logger.debug("Getting workflow execution", execution_id=execution_id)
    service = await WorkflowExecutionsService.connect(role=role)
    execution = await service.get_execution(execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow execution not found",
        )
    logger.debug("Getting workflow execution events", execution_id=execution.id)
    events = await service.list_workflow_execution_events(execution.id)
    interactions = await _list_interactions(session, execution.id)
    return WorkflowExecutionRead(
        id=execution.id,
        run_id=execution.run_id,
        start_time=execution.start_time,
        execution_time=execution.execution_time,
        close_time=execution.close_time,
        status=execution.status,
        workflow_type=execution.workflow_type,
        task_queue=execution.task_queue,
        history_length=execution.history_length,
        events=events,
        interactions=interactions,
        trigger_type=get_trigger_type_from_search_attr(
            execution.typed_search_attributes, execution.id
        ),
        execution_type=get_execution_type_from_search_attr(
            execution.typed_search_attributes
        ),
    )


@router.get("/{execution_id:path}/compact")
async def get_workflow_execution_compact(
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
    session: AsyncDBSession,
) -> WorkflowExecutionReadCompact[Any, AgentOutput | Any, Any]:
    """Get a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    execution = await service.get_execution(execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow execution not found",
        )

    compact_events = await service.list_workflow_execution_events_compact(execution_id)

    for event in compact_events:
        # Project AgentOutput to UIMessages only in the compact workflow execution view
        if event.session is not None and event.action_result is not None:
            logger.trace("Transforming AgentOutput to UIMessages")
            try:
                # Successful validation asserts this is an AgentOutput
                output = AgentOutput.model_validate(event.action_result)
                if output.message_history:
                    # Re-deserialize the message field for each ChatMessage.
                    # When data round-trips through Temporal, ChatMessage.message
                    # becomes a raw dict instead of a typed ClaudeSDKMessage.
                    # We need to re-validate it so convert_chat_messages_to_ui
                    # can use isinstance() checks on the message types.
                    for chat_msg in output.message_history:
                        if chat_msg.message is not None and isinstance(
                            chat_msg.message, dict
                        ):
                            chat_msg.message = ClaudeSDKMessageTA.validate_python(
                                chat_msg.message
                            )
                    event.session.events = (
                        tracecat.agent.adapter.vercel.convert_chat_messages_to_ui(
                            output.message_history
                        )
                    )
            except Exception as e:
                logger.error("Error transforming AgentOutput to UIMessages", error=e)

    interactions = await _list_interactions(session, execution_id)
    return WorkflowExecutionReadCompact(
        id=execution.id,
        parent_wf_exec_id=execution.parent_id,
        run_id=execution.run_id,
        start_time=execution.start_time,
        execution_time=execution.execution_time,
        close_time=execution.close_time,
        status=execution.status,
        workflow_type=execution.workflow_type,
        task_queue=execution.task_queue,
        history_length=execution.history_length,
        events=compact_events,
        interactions=interactions,
        trigger_type=get_trigger_type_from_search_attr(
            execution.typed_search_attributes, execution.id
        ),
        execution_type=get_execution_type_from_search_attr(
            execution.typed_search_attributes
        ),
    )


@router.post("")
async def create_workflow_execution(
    role: WorkspaceUserRole,
    params: WorkflowExecutionCreate,
    session: AsyncDBSession,
) -> WorkflowExecutionCreateResponse:
    """Create and schedule a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    # Get the dslinput from the workflow definition
    wf_id = WorkflowUUID.new(params.workflow_id)
    try:
        result = await session.execute(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == wf_id)
            .order_by(WorkflowDefinition.version.desc())
        )
        defn = result.scalars().first()
        if not defn:
            raise NoResultFound("No workflow definition found for workflow ID")
    except NoResultFound as e:
        # No workflow associated with the webhook
        logger.opt(exception=e).error("Invalid workflow ID", error=e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invalid workflow ID"
        ) from e
    dsl_input = DSLInput(**defn.content)
    try:
        response = service.create_workflow_execution_nowait(
            dsl=dsl_input,
            wf_id=wf_id,
            payload=params.inputs,
            time_anchor=params.time_anchor,
            # For regular workflow executions, use the registry lock from the workflow definition
            registry_lock=(
                RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None
            ),
        )
        return response
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "type": "TracecatValidationError",
                "message": str(e),
                "detail": e.detail,
            },
        ) from e


@router.post("/draft")
async def create_draft_workflow_execution(
    role: WorkspaceUserRole,
    params: WorkflowExecutionCreate,
    session: AsyncDBSession,
) -> WorkflowExecutionCreateResponse:
    """Create and schedule a draft workflow execution.

    Draft executions run the current draft workflow graph (not the committed definition).
    Child workflows using aliases will resolve to the latest draft aliases, not committed aliases.
    """

    service = await WorkflowExecutionsService.connect(role=role)
    wf_id = WorkflowUUID.new(params.workflow_id)

    # Build DSL from the draft workflow, not from committed definition
    async with WorkflowsManagementService.with_session(role=role) as mgmt_service:
        workflow = await mgmt_service.get_workflow(wf_id)
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
            )
        try:
            dsl_input = await mgmt_service.build_dsl_from_workflow(workflow)
        except TracecatValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "TracecatValidationError",
                    "message": str(e),
                    "detail": e.detail,
                },
            ) from e
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "ValidationError",
                    "message": str(e),
                    "detail": e.errors(),
                },
            ) from e

    try:
        response = service.create_draft_workflow_execution_nowait(
            dsl=dsl_input,
            wf_id=wf_id,
            payload=params.inputs,
            time_anchor=params.time_anchor,
            # For draft workflow executions, pass None to dynamically resolve the registry lock
        )
        return response
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "type": "TracecatValidationError",
                "message": str(e),
                "detail": e.detail,
            },
        ) from e


@router.post(
    "/{execution_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_workflow_execution(
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
) -> None:
    """Get a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    try:
        await service.cancel_workflow_execution(execution_id)
    except temporalio.service.RPCError as e:
        if "workflow execution already completed" in e.message:
            logger.info(
                "Workflow execution already completed, ignoring cancellation request",
            )
        else:
            logger.error(e.message, error=e, execution_id=execution_id)
            raise e


@router.post(
    "/{execution_id}/terminate",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def terminate_workflow_execution(
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
    params: WorkflowExecutionTerminate,
) -> None:
    """Get a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    try:
        await service.terminate_workflow_execution(execution_id, reason=params.reason)
    except temporalio.service.RPCError as e:
        if "workflow execution already completed" in e.message:
            logger.info(
                "Workflow execution already completed, ignoring termination request",
            )
        else:
            logger.error(e.message, error=e, execution_id=execution_id)
            raise e
