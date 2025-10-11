from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.agent.factory import build_agent
from tracecat.agent.models import AgentConfig
from tracecat.agent.service import AgentManagementService
from tracecat.chat.models import ChatMessage
from tracecat.db.schemas import Case, Runbook
from tracecat.runbook.prompts import (
    CaseToRunbookPrompts,
    CaseToRunbookTitlePrompts,
    ExecuteRunbookPrompts,
)
from tracecat.types.auth import Role


def _clean_runbook_text(text: str) -> str:
    """Clean and normalize runbook output by removing code block wrappers."""
    text = text.strip()

    # Remove markdown code block wrapper if the entire content is wrapped
    if text.startswith("```") and text.endswith("```"):
        # Find the first newline after opening ```
        first_newline = text.find("\n")
        if first_newline != -1:
            # Remove opening ``` and language identifier
            text = text[first_newline + 1 :]
            # Remove closing ```
            if text.endswith("```"):
                text = text[:-3].rstrip()
    return text


async def generate_runbook_from_chat(
    *,
    case: Case,
    messages: list[ChatMessage],
    tools: list[str],
    session: AsyncSession,
    role: Role,
):
    """Generate a runbook from a chat."""

    prompts = CaseToRunbookPrompts(
        case=case,
        messages=messages,
        tools=tools,
    )
    instructions, user_prompt = prompts.instructions, prompts.user_prompt
    svc = AgentManagementService(session, role)
    async with svc.with_model_config() as model_config:
        agent = await build_agent(
            AgentConfig(
                model_name=model_config.name,
                model_provider=model_config.provider,
                instructions=instructions,
            )
        )
        response = await agent.run(user_prompt)
        instructions = _clean_runbook_text(response.output)
        return instructions


async def generate_runbook_title_from_chat(
    *,
    case: Case,
    messages: list[ChatMessage],
    session: AsyncSession,
    role: Role,
):
    """Generate a runbook title from a chat."""
    prompts = CaseToRunbookTitlePrompts(
        case=case,
        messages=messages,
    )
    instructions, user_prompt = prompts.instructions, prompts.user_prompt
    svc = AgentManagementService(session, role)
    async with svc.with_model_config() as model_config:
        agent = await build_agent(
            AgentConfig(
                model_name=model_config.name,
                model_provider=model_config.provider,
                instructions=instructions,
            )
        )
        response = await agent.run(user_prompt)
        title = _clean_runbook_text(response.output)
        return title


async def execute_runbook_on_case(
    *,
    runbook: Runbook,
    case: Case,
    session: AsyncSession,
    role: Role,
):
    """Execute a runbook for a case."""
    prompts = ExecuteRunbookPrompts(
        runbook=runbook,
        case=case,
    )
    instructions, user_prompt = prompts.instructions, prompts.user_prompt
    svc = AgentManagementService(session, role)
    async with svc.with_model_config() as model_config:
        agent = await build_agent(
            AgentConfig(
                model_name=model_config.name,
                model_provider=model_config.provider,
                instructions=instructions,
            )
        )
        response = await agent.run(user_prompt)
        return response.output
