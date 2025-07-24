import asyncio
import json
import textwrap
import uuid
from datetime import datetime

from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry.integrations.agents.builder import agent

from tracecat.agent.service import AgentManagementService
from tracecat.cases.service import CasesService
from tracecat.chat.enums import ChatEntity
from tracecat.chat.models import ChatResponse
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Prompt
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError


async def execute_runbook_for_case(
    *,
    prompt: Prompt,
    case_id: uuid.UUID,
    session: AsyncSession,
    role: Role,
) -> ChatResponse:
    """Run prompt for a case.

    This creates a new chat for the case and runs the prompt on it.
    """
    chat_svc = ChatService(session, role)
    case_svc = CasesService(session, role)
    case = await case_svc.get_case(case_id)
    if not case:
        raise TracecatNotFoundError(f"Case {case_id} not found")

    chat = await chat_svc.create_chat(
        entity_type=ChatEntity.CASE,
        entity_id=case.id,
        title=f"Prompt {prompt.title}: {datetime.now().isoformat()}",
    )

    # Create task with proper environment
    # NOTE: In production, this should use workspace-specific credentials
    agent_svc = AgentManagementService(session, role)
    async with agent_svc.with_model_config() as model_config:
        coro = agent(
            instructions=prompt.content,
            model_name=model_config.name,
            model_provider=model_config.provider,
            actions=prompt.tools,
            workflow_run_id=str(chat.id),
            user_prompt=textwrap.dedent(f"""
            You are working with the following case:
            <CaseId>
            {case.id}
            </CaseId>

            This case has the following alert payload:
            <Alert type="json">
            {json.dumps(case.payload)}
            </Alert>
            """),
        )
        _ = asyncio.create_task(coro)

    stream_url = f"/api/chat/{chat.id}/stream"

    return ChatResponse(
        stream_url=stream_url,
        chat_id=chat.id,
    )
