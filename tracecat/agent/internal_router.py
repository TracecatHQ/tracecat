"""Routes kept for registry locks predating pydantic-ai removal; remove after the announced sunset."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import orjson
from fastapi import APIRouter, HTTPException, status
from temporalio.client import WorkflowFailureError
from temporalio.common import Priority
from tracecat_ee.agent.types import AgentWorkflowID
from tracecat_ee.agent.workflows.durable import (
    AgentWorkflowArgs,
    DurableAgentWorkflow,
)

from tracecat import config
from tracecat.agent.schemas import (
    AgentOutput,
    InternalRankItemsRequest,
    InternalRunAgentRequest,
    RunAgentArgs,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.logger import logger
from tracecat.tiers.entitlements import Entitlement, check_entitlement

router = APIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    include_in_schema=False,
)

_MAX_RANK_ITEMS = 100
_RANK_OUTPUT_KEY = "ranked_ids"
_RANK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        _RANK_OUTPUT_KEY: {
            "type": "array",
            "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        }
    },
    "required": [_RANK_OUTPUT_KEY],
    "additionalProperties": False,
}


def _warn_retired_route(path: str, role: Role) -> None:
    logger.warning(
        "Retired internal agent route called",
        path=path,
        workspace_id=role.workspace_id,
    )


def build_agent_workflow_args(
    params: InternalRunAgentRequest,
    *,
    role: Role,
    session_id: uuid.UUID,
) -> AgentWorkflowArgs:
    """Map a retired SDK agent call onto the durable workflow. Presets resolve there."""
    config_ = (
        AgentConfig(**params.config.model_dump()) if params.config is not None else None
    )
    return AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            user_prompt=params.user_prompt,
            session_id=session_id,
            config=config_,
            preset_slug=params.preset_slug,
            preset_version=params.preset_version,
            max_requests=params.max_requests,
            max_tool_calls=params.max_tool_calls,
        ),
        title="Agent run",
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=session_id,
    )


def _build_rank_workflow_args(
    params: InternalRankItemsRequest,
    *,
    role: Role,
    session_id: uuid.UUID,
) -> AgentWorkflowArgs:
    instructions = (
        "Order the provided items from most to least relevant to this criteria:\n"
        f"{params.criteria_prompt}\n\n"
        "Return every provided id exactly once in the ranked_ids field and do not "
        "invent ids."
    )
    user_prompt = (
        'Rank these items and return only a JSON object with a "ranked_ids" field:\n'
        f"{orjson.dumps(params.items).decode()}"
    )
    return AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            user_prompt=user_prompt,
            session_id=session_id,
            config=AgentConfig(
                model_name=params.model_name,
                model_provider=params.model_provider,
                catalog_id=params.catalog_id,
                base_url=params.base_url,
                instructions=instructions,
                output_type=_RANK_OUTPUT_SCHEMA,
                model_settings=params.model_settings,
                retries=params.retries,
                actions=None,
                tool_approvals=None,
            ),
            max_requests=params.max_requests,
            max_tool_calls=0,
        ),
        title="Agent ranking run",
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=session_id,
    )


async def _execute_agent_workflow(
    workflow_args: AgentWorkflowArgs,
    *,
    session_id: uuid.UUID,
) -> AgentOutput:
    client = await get_temporal_client()
    result = await client.execute_workflow(
        DurableAgentWorkflow.run,
        workflow_args,
        id=AgentWorkflowID(session_id),
        task_queue=config.TRACECAT__AGENT_QUEUE,
        retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        priority=Priority(priority_key=1),
        # The sandbox HTTP call must not outlive the workflow.
        execution_timeout=timedelta(seconds=config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT),
    )
    return AgentOutput.model_validate(result)


def _pop_matching_id(
    returned_id: object,
    remaining_ids: list[str | int],
) -> str | int | None:
    if isinstance(returned_id, bool) or not isinstance(returned_id, str | int):
        return None

    normalized: str | int = returned_id
    if isinstance(returned_id, str):
        normalized = returned_id.strip()
        if normalized.startswith("`") and normalized.endswith("`"):
            normalized = normalized[1:-1]

    for index, item_id in enumerate(remaining_ids):
        if type(item_id) is type(normalized) and item_id == normalized:
            return remaining_ids.pop(index)

    if isinstance(normalized, str):
        try:
            numeric_id = int(normalized)
        except ValueError:
            return None
        for index, item_id in enumerate(remaining_ids):
            if type(item_id) is int and item_id == numeric_id:
                return remaining_ids.pop(index)
    return None


def _ranked_id_permutation(
    output: object,
    input_ids: list[str | int],
) -> list[str | int]:
    """Keep known ids in model order, then append the rest in input order."""
    if not isinstance(output, dict) or not isinstance(
        returned_ids := output.get(_RANK_OUTPUT_KEY), list
    ):
        raise ValueError(
            'Ranking agent must return a JSON object with a "ranked_ids" list'
        )

    remaining_ids = list(input_ids)
    ranked_ids: list[str | int] = []
    for returned_id in returned_ids:
        if (matched_id := _pop_matching_id(returned_id, remaining_ids)) is not None:
            ranked_ids.append(matched_id)
    return [*ranked_ids, *remaining_ids]


def _apply_rank_limits(
    ranked_ids: list[str | int],
    *,
    min_items: int | None,
    max_items: int | None,
) -> list[str | int]:
    if min_items is not None and min_items < 0:
        raise ValueError("min_items must be non-negative")
    if max_items is not None and max_items < 0:
        raise ValueError("max_items must be non-negative")
    if min_items is not None and max_items is not None and min_items > max_items:
        raise ValueError("min_items cannot exceed max_items")

    limit = len(ranked_ids) if max_items is None else max_items
    if min_items is not None:
        limit = max(limit, min_items)
    return ranked_ids[:limit]


async def _rank_items(
    params: InternalRankItemsRequest,
    *,
    role: Role,
) -> list[str | int]:
    """One structured-output run replaces the old pairwise tournament."""
    if not params.items:
        return []
    if len(params.items) > _MAX_RANK_ITEMS:
        raise ValueError(
            f"Expected at most {_MAX_RANK_ITEMS} items, got {len(params.items)} items."
        )

    session_id = uuid.uuid4()
    result = await _execute_agent_workflow(
        _build_rank_workflow_args(params, role=role, session_id=session_id),
        session_id=session_id,
    )
    ranked_ids = _ranked_id_permutation(
        result.output,
        [item["id"] for item in params.items],
    )
    return _apply_rank_limits(
        ranked_ids,
        min_items=params.min_items,
        max_items=params.max_items,
    )


def _http_error(exc: ValueError | WorkflowFailureError) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    cause = exc.cause or exc
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"error_type": type(cause).__name__, "message": str(cause)},
    )


@router.post("/run", status_code=status.HTTP_200_OK)
@require_scope("agent:execute")
async def run_agent_endpoint(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalRunAgentRequest,
) -> dict[str, Any]:
    """Run a retired SDK agent call on the durable runtime."""
    ctx_role.set(role)
    _warn_retired_route("/internal/agent/run", role)
    try:
        if params.config is not None and params.config.tool_approvals:
            await check_entitlement(session, role, Entitlement.AGENT_ADDONS)
        session_id = uuid.uuid4()
        result = await _execute_agent_workflow(
            build_agent_workflow_args(params, role=role, session_id=session_id),
            session_id=session_id,
        )
    except (ValueError, WorkflowFailureError) as exc:
        logger.exception("Retired internal agent run failed")
        raise _http_error(exc) from exc
    return result.model_dump(mode="json")


@router.post("/rank", status_code=status.HTTP_200_OK)
@require_scope("agent:execute")
async def rank_items_endpoint(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalRankItemsRequest,
) -> list[str | int]:
    """Rank items through one structured durable agent run."""
    del session
    ctx_role.set(role)
    _warn_retired_route("/internal/agent/rank", role)
    try:
        return await _rank_items(params, role=role)
    except (ValueError, WorkflowFailureError) as exc:
        logger.exception("Retired internal agent rank failed")
        raise _http_error(exc) from exc


@router.post("/rank-pairwise", status_code=status.HTTP_200_OK)
@require_scope("agent:execute")
async def rank_items_pairwise_endpoint(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalRankItemsRequest,
) -> list[str | int]:
    """Serve pairwise calls through the shared structured ranking run."""
    del session
    ctx_role.set(role)
    _warn_retired_route("/internal/agent/rank-pairwise", role)
    try:
        return await _rank_items(params, role=role)
    except (ValueError, WorkflowFailureError) as exc:
        logger.exception("Retired internal agent rank failed")
        raise _http_error(exc) from exc
