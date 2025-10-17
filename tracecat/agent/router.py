import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter
from temporalio.client import WorkflowExecution, WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError

from tracecat.agent.approvals.models import ApprovalRead
from tracecat.agent.approvals.service import (
    ApprovalMap,
    ApprovalService,
    EnrichedSession,
    SessionInfo,
)
from tracecat.agent.models import (
    ModelConfig,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.agent.service import AgentManagementService
from tracecat.agent.stream.common import get_stream_headers
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.stream.events import StreamFormat
from tracecat.agent.types import AgentWorkflowID, StreamKey
from tracecat.agent.workflows.durable import (
    DurableAgentWorkflow,
    WorkflowApprovalSubmission,
)
from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.auth.models import UserReadMinimal
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import AgentActionMemo
from tracecat.identifiers.workflow import exec_id_to_parts
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])

OrganizationAdminUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
]

OrganizationUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
]


@router.get("/models")
async def list_models(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> dict[str, ModelConfig]:
    """List all available AI models."""
    service = AgentManagementService(session, role=role)
    return await service.list_models()


@router.get("/providers")
async def list_providers(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> list[str]:
    """List all available AI model providers."""
    service = AgentManagementService(session, role=role)
    return await service.list_providers()


@router.get("/providers/status")
async def get_providers_status(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> dict[str, bool]:
    """Get credential status for all providers."""
    service = AgentManagementService(session, role=role)
    return await service.get_providers_status()


@router.get("/providers/configs")
async def list_provider_credential_configs(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> list[ProviderCredentialConfig]:
    """List credential field configurations for all providers."""
    service = AgentManagementService(session, role=role)
    return await service.list_provider_credential_configs()


@router.get("/providers/{provider}/config")
async def get_provider_credential_config(
    *,
    provider: str,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> ProviderCredentialConfig:
    """Get credential field configuration for a specific provider."""
    service = AgentManagementService(session, role=role)
    try:
        return await service.get_provider_credential_config(provider)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider} not found",
        ) from e


@router.post("/credentials", status_code=status.HTTP_201_CREATED)
async def create_provider_credentials(
    *,
    params: ModelCredentialCreate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Create or update credentials for an AI provider."""
    service = AgentManagementService(session, role=role)
    try:
        await service.create_provider_credentials(params)
        return {"message": f"Credentials for {params.provider} created successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create credentials: {str(e)}",
        ) from e


@router.put("/credentials/{provider}")
async def update_provider_credentials(
    *,
    provider: str,
    params: ModelCredentialUpdate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Update existing credentials for an AI provider."""
    service = AgentManagementService(session, role=role)
    try:
        await service.update_provider_credentials(provider, params)
        return {"message": f"Credentials for {provider} updated successfully"}
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credentials for provider {provider} not found",
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update credentials: {str(e)}",
        ) from e


@router.delete("/credentials/{provider}")
async def delete_provider_credentials(
    *,
    provider: str,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Delete credentials for an AI provider."""
    service = AgentManagementService(session, role=role)
    await service.delete_provider_credentials(provider)
    return {"message": f"Credentials for {provider} deleted successfully"}


@router.get("/default-model")
async def get_default_model(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> str | None:
    """Get the organization's default AI model."""
    service = AgentManagementService(session, role=role)
    return await service.get_default_model()


@router.put("/default-model")
async def set_default_model(
    *,
    model_name: str,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Set the organization's default AI model."""
    service = AgentManagementService(session, role=role)
    try:
        await service.set_default_model(model_name)
        return {"message": f"Default model set to {model_name}"}
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_name} not found",
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to set default model: {str(e)}",
        ) from e


class WorkflowSummary(BaseModel):
    id: uuid.UUID
    title: str
    alias: str | None = None


class AgentSessionRead(BaseModel):
    id: uuid.UUID
    created_at: datetime
    parent_id: str | None = None
    parent_run_id: str | None
    root_id: str | None = None
    root_run_id: str | None = None
    status: WorkflowExecutionStatus | None = None
    approvals: list[ApprovalRead] = Field(default_factory=list)
    parent_workflow: WorkflowSummary | None = None
    root_workflow: WorkflowSummary | None = None
    action_ref: str | None = None
    action_title: str | None = None


ApprovalsTA: TypeAdapter[list[EnrichedSession]] = TypeAdapter(list[EnrichedSession])


class AgentApprovalSubmission(BaseModel):
    approvals: ApprovalMap


@router.get("/sessions")
async def list_agent_sessions(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[AgentSessionRead]:
    """List all agent sessions."""
    # TODO: Limit to workspace
    # Get all running DurableAgentWorkflows
    client = await get_temporal_client()
    sessions: list[SessionInfo] = []
    executions_by_id: dict[uuid.UUID, WorkflowExecution] = {}

    async for execution in client.list_workflows(
        query="WorkflowType = 'DurableAgentWorkflow'"
    ):
        memo = await execution.memo()
        execution_time = execution.start_time
        typed_memo = AgentActionMemo.model_validate(memo)
        session_id = AgentWorkflowID.extract_id(execution.id)
        if execution.parent_id:
            p_wf_id, _ = exec_id_to_parts(execution.parent_id)
        else:
            p_wf_id = None
        if execution.root_id:
            r_wf_id, _ = exec_id_to_parts(execution.root_id)
        else:
            r_wf_id = None
        sessions.append(
            SessionInfo(
                session_id=session_id,
                parent_workflow_id=p_wf_id,
                root_workflow_id=r_wf_id,
                start_time=execution_time,
                action_ref=typed_memo.action_ref,
                action_title=typed_memo.action_title,
            )
        )
        # Store execution object for later use
        executions_by_id[session_id] = execution

    svc = ApprovalService(session, role=role)
    enriched_sessions = await svc.list_sessions_enriched(sessions)

    result: list[AgentSessionRead] = []
    for enriched_session in enriched_sessions:
        execution = executions_by_id[enriched_session.id]

        # Transform approval enrichments to API response format
        approval_reads = [
            ApprovalRead(
                approved_by=UserReadMinimal.model_validate(
                    enriched.approved_by, from_attributes=True
                )
                if enriched.approved_by
                else None,
                **enriched.approval.model_dump(exclude={"approved_by"}),
            )
            for enriched in enriched_session.approvals
        ]

        # Create workflow summaries if workflows exist
        parent_summary = (
            WorkflowSummary(
                id=enriched_session.parent_workflow.id,
                title=enriched_session.parent_workflow.title,
                alias=enriched_session.parent_workflow.alias,
            )
            if enriched_session.parent_workflow
            else None
        )

        root_summary = (
            WorkflowSummary(
                id=enriched_session.root_workflow.id,
                title=enriched_session.root_workflow.title,
                alias=enriched_session.root_workflow.alias,
            )
            if enriched_session.root_workflow
            else None
        )

        result.append(
            AgentSessionRead(
                id=enriched_session.id,
                created_at=enriched_session.start_time,
                parent_id=str(enriched_session.parent_workflow.id)
                if enriched_session.parent_workflow
                else None,
                parent_run_id=execution.parent_id,
                root_id=str(enriched_session.root_workflow.id)
                if enriched_session.root_workflow
                else None,
                root_run_id=execution.root_id,
                status=execution.status,
                approvals=approval_reads,
                parent_workflow=parent_summary,
                root_workflow=root_summary,
                action_ref=enriched_session.action_ref,
                action_title=enriched_session.action_title,
            )
        )
    return result


@router.get("/sessions/{session_id}")
async def stream_agent_session(
    *,
    role: WorkspaceUserRole,
    session_id: uuid.UUID,
    request: Request,
    format: StreamFormat = Query(
        default="vercel", description="Streaming format (e.g. 'vercel')"
    ),
    last_event_id: str = Header(default="0-0"),
) -> StreamingResponse:
    """Stream agent session events via Server-Sent Events (SSE).

    This endpoint provides real-time streaming of AI agent execution steps
    using Server-Sent Events. It supports automatic reconnection via the
    Last-Event-ID header.
    """
    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access required",
        )

    stream_key = StreamKey(workspace_id, session_id)
    logger.info(
        "Starting agent session",
        stream_key=stream_key,
        last_id=last_event_id,
        session_id=session_id,
        format=format,
    )

    stream = await AgentStream.new(session_id, workspace_id)
    headers = get_stream_headers(format)
    return StreamingResponse(
        stream.sse(request.is_disconnected, last_id=last_event_id, format=format),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/sessions/{session_id}/approvals", status_code=status.HTTP_204_NO_CONTENT)
async def submit_agent_approvals(
    *,
    role: WorkspaceUserRole,
    session_id: uuid.UUID,
    payload: AgentApprovalSubmission,
) -> None:
    """Submit approval decisions back to the running agent workflow."""
    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access required",
        )

    workflow_id = AgentWorkflowID(session_id)
    client = await get_temporal_client()
    handle = client.get_workflow_handle_for(DurableAgentWorkflow.run, workflow_id)

    try:
        submission = WorkflowApprovalSubmission(
            approvals=payload.approvals,
            approved_by=role.user_id,
        )
        await handle.execute_update(DurableAgentWorkflow.set_approvals, submission)
    except ApplicationError as exc:
        logger.warning(
            "Failed to submit approvals",
            session_id=session_id,
            workflow_id=workflow_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RPCError as exc:
        logger.error(
            "Temporal RPC error while submitting approvals",
            session_id=session_id,
            workflow_id=workflow_id,
            error=str(exc),
        )
        if "workflow not found" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found"
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to reach workflow service",
        ) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected error while submitting approvals",
            session_id=session_id,
            workflow_id=workflow_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit approvals",
        ) from exc
