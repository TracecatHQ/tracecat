import asyncio
import json
import os
import textwrap
import uuid
from datetime import datetime

from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry.integrations.agents.builder import agent

from tracecat.cases.service import CasesService
from tracecat.chat.enums import ChatEntity
from tracecat.chat.models import ChatResponse
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Prompt
from tracecat.secrets import secrets_manager
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError


async def run_prompts_for_case(
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
    with secrets_manager.env_sandbox(
        # {"OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "")}
        {
            "AWS_ACCESS_KEY_ID": os.environ["AWS_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": os.environ["AWS_SECRET_ACCESS_KEY"],
            "AWS_REGION": os.environ["AWS_REGION"],
        }
    ):
        coro = agent(
            instructions=prompt.content,
            model_name=os.environ["CHAT_MODEL_NAME"],
            model_provider=os.environ["CHAT_MODEL_PROVIDER"],
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
