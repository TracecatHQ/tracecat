"""Run workflow agent actions on the session's agent backend.

A workflow agent action builds the built-in durable workflow's arguments
either way. The action first selects its backend. On another backend than the
built-in one, the turn's parent workflow prepares the turn there, starts the
backend's turn workflow as its own child by type name, and reads its result.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.backends.base import claim_unstarted_workflow_id
from tracecat.agent.backends.registry import DEFAULT_AGENT_BACKEND, find_agent_backend
from tracecat.agent.backends.schemas import AgentWorkflowArgs, ChildAgentTurn
from tracecat.agent.backends.selection import select_backend_id
from tracecat.agent.backends.turns import build_child_agent_turn
from tracecat.agent.backends.types import SessionTurnContext
from tracecat.agent.error_policy import (
    agent_provider_credentials_missing,
    invalid_agent_configuration,
)
from tracecat.agent.gateway_providers import is_gateway_provider
from tracecat.agent.parsers import resolve_agent_output
from tracecat.agent.preset.activities import (
    resolve_custom_model_provider_config_activity,
)
from tracecat.agent.service import AgentManagementService
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import (
    AgentConfig,
    apply_preset_overrides,
    clamp_agent_timeout_seconds,
)
from tracecat.agent.workflow_turns.schemas import (
    ReadWorkflowAgentTurnInput,
    ReadWorkflowAgentTurnResult,
    SelectWorkflowAgentBackendResult,
    WorkflowAgentTurnInput,
    workflow_agent_turn_workflow_id,
)
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import AgentSession
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import (
    AgentProviderCredentialsNotFoundError,
    EntitlementRequired,
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.temporal.errors import raise_application_error_from_classification


async def _turn_backend(
    service: AgentSessionService, args: AgentWorkflowArgs
) -> tuple[str, AgentSession | None]:
    """Pick the turn's backend, with the session if it exists; it keeps its own.

    On another backend, a workflow action continues only its own workflow's
    sessions, never a reviewer fork, and a user's session only under that
    user's runs, so no other surface, workflow or run writes into them.
    """
    if session := await service.get_session(args.agent_args.session_id):
        backend_id = session.backend_id or DEFAULT_AGENT_BACKEND
        started_by_teammate = (
            session.created_by is not None
            and session.created_by != service.role.user_id
        )
        if backend_id != DEFAULT_AGENT_BACKEND and (
            session.entity_type != AgentSessionEntity.WORKFLOW.value
            or session.entity_id != args.entity_id
            or session.forked_from_session_id is not None
            or started_by_teammate
        ):
            raise_application_error_from_classification(invalid_agent_configuration())
        return backend_id, session
    if args.continue_existing_session:
        # The built-in workflow reports the missing session, as before.
        return DEFAULT_AGENT_BACKEND, None
    backend_id = await select_backend_id(
        service.session, service.organization_id, AgentSessionEntity.WORKFLOW
    )
    return backend_id, None


async def _turn_config(
    service: AgentSessionService, args: AgentWorkflowArgs
) -> AgentConfig:
    """Resolve the configuration the built-in workflow would run the turn with."""
    agent_args = args.agent_args
    if agent_args.preset_slug is None:
        if agent_args.config is None:
            raise_application_error_from_classification(invalid_agent_configuration())
        return agent_args.config
    # Resolve by the version the action pinned, like the built-in workflow.
    pinned = args.agent_preset_version_id is not None
    agents = AgentManagementService(service.session, service.role)
    async with agents.with_preset_config(
        slug=None if pinned else agent_args.preset_slug,
        preset_version_id=args.agent_preset_version_id,
        preset_version=None if pinned else agent_args.preset_version,
        # Overrides may change the actions; tunnels are pruned after them.
        prune_tool_tunnels=False,
    ) as preset_config:
        return apply_preset_overrides(preset_config, agent_args.config)


async def _with_gateway_provider_config(role: Role, config: AgentConfig) -> AgentConfig:
    """Point a gateway provider at the organization's configured endpoint.

    Like the built-in workflow, this replaces any base URL the action supplied
    for a gateway provider. Other providers keep the action's base URL, as on
    the built-in workflow.
    """
    if not is_gateway_provider(config.model_provider):
        return config
    resolved = await resolve_custom_model_provider_config_activity(
        role, config.catalog_id, False, config.model_provider
    )
    return replace(
        config,
        base_url=resolved.base_url,
        passthrough=resolved.passthrough,
        model_name=resolved.model_name or config.model_name,
    )


# A reserved turn whose workflows never appear is still on its way to starting
# them until this long after the reservation; only then is it given up.
_UNSTARTED_TURN_GRACE = timedelta(minutes=10)
_RUNNING = frozenset(
    {WorkflowExecutionStatus.RUNNING, WorkflowExecutionStatus.CONTINUED_AS_NEW}
)


class _Execution(StrEnum):
    """What a workflow lookup proves about one execution."""

    RUNNING = "running"
    CLOSED = "closed"
    MISSING = "missing"
    # The lookup failed; nothing is proven.
    UNKNOWN = "unknown"


async def _execution(handle: WorkflowHandle[Any, Any]) -> _Execution:
    try:
        description = await handle.describe(rpc_timeout=timedelta(seconds=5))
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return _Execution.MISSING
        return _Execution.UNKNOWN
    except Exception:
        return _Execution.UNKNOWN
    if description.status in _RUNNING:
        return _Execution.RUNNING
    return _Execution.CLOSED


def _holder_ended(
    turn: SessionTurnContext,
) -> Callable[[AgentSession], Awaitable[bool]]:
    """Decide whether the run holding a workflow session has ended for good.

    Only then may another workflow agent action take the session over. The run
    may be a workflow agent action's, whose parent starts the backend's turn,
    or a direct turn the API started on the backend's own workflow ID, so both
    workflows are checked. The run has ended when neither runs and every ID
    that never started has been claimed, so no late start can run; when
    neither started, only once the reservation has outlived the start grace
    period. Any failed lookup proves nothing, and the backend may still keep
    a terminal turn's reservation for reconciliation.

    Args:
        turn: The admitting turn, whose database session holds the row lock;
            also handed to the backend's reconciliation check.
    """

    async def ended(session: AgentSession) -> bool:
        run_id = session.curr_run_id
        backend = find_agent_backend(session.backend_id)
        if run_id is None or backend is None:
            return False
        client = await get_temporal_client()
        native = await _execution(await backend.handle(run_id, client=client))
        if native in (_Execution.RUNNING, _Execution.UNKNOWN):
            return False
        parent_id = workflow_agent_turn_workflow_id(run_id)
        parent = await _execution(client.get_workflow_handle(parent_id))
        if parent in (_Execution.RUNNING, _Execution.UNKNOWN):
            return False
        if native is _Execution.MISSING and parent is _Execution.MISSING:
            # Neither started yet; either may still be on its way. Every
            # reservation write bumps updated_at, so this is its age.
            outlived = await turn.db.scalar(
                select(AgentSession.id).where(
                    AgentSession.id == session.id,
                    AgentSession.updated_at <= func.now() - _UNSTARTED_TURN_GRACE,
                )
            )
            if outlived is None:
                return False
        # Claim every ID that never started, each on its own, so a late start
        # is rejected and never runs beside the new turn. An earlier attempt
        # may have claimed one already; its tombstone then shows as closed.
        missing = [
            workflow_id
            for workflow_id, execution in (
                (parent_id, parent),
                (backend.workflow_id(run_id), native),
            )
            if execution is _Execution.MISSING
        ]
        for workflow_id in missing:
            if not await claim_unstarted_workflow_id(client, workflow_id):
                return False
        return not await backend.retains_terminal_ownership(
            replace(turn, session=session), run_id
        )

    return ended


@activity.defn
async def select_workflow_agent_backend_activity(
    args: AgentWorkflowArgs,
) -> SelectWorkflowAgentBackendResult:
    """Pick the backend a workflow agent action runs on.

    Returns no backend for the built-in one, whose workflow the action starts
    as before. Writes nothing.
    """
    ctx_role.set(args.role)
    async with AgentSessionService.with_session(role=args.role) as service:
        backend_id, _ = await _turn_backend(service, args)
    if backend_id == DEFAULT_AGENT_BACKEND:
        return SelectWorkflowAgentBackendResult()
    return SelectWorkflowAgentBackendResult(backend_id=backend_id)


@activity.defn
async def prepare_workflow_agent_turn_activity(
    input: WorkflowAgentTurnInput,
) -> ChildAgentTurn:
    """Prepare a workflow agent action's turn on the backend selected for it.

    Creates the workflow session on that backend if needed, reserves the turn
    under the action's run ID, and builds the backend's workflow input. A retry
    adopts the same reservation and returns the same turn.
    """
    args = input.args
    agent_args = args.agent_args
    ctx_role.set(args.role)
    async with AgentSessionService.with_session(role=args.role) as service:
        # Check again under this attempt; the session's backend must not have
        # changed since the action selected it.
        backend_id, agent_session = await _turn_backend(service, args)
        if backend_id != input.backend_id:
            raise ApplicationError(
                "The agent session's backend changed", non_retryable=True
            )
        try:
            config = await _with_gateway_provider_config(
                args.role, await _turn_config(service, args)
            )
            if agent_session is None:
                agent_session, created = await service.get_or_create_session(
                    AgentSessionCreate(
                        id=agent_args.session_id,
                        title=args.title,
                        created_by=args.role.user_id,
                        entity_type=args.entity_type,
                        entity_id=args.entity_id,
                        tools=args.tools,
                        agent_preset_id=args.agent_preset_id,
                        agent_preset_version_id=args.agent_preset_version_id,
                        backend_id=backend_id,
                    )
                )
                if created:
                    await service.auto_title_session_on_first_prompt(
                        agent_session,
                        agent_args.user_prompt,
                        expected_title=agent_session.title,
                    )
            admitting = SessionTurnContext(
                db=service.session,
                session=agent_session,
                role=service.role,
                config=config,
                prompt=agent_args.user_prompt,
                run_id=input.run_id,
                # The stream is assigned on reservation; only the run counts here.
                stream_id=uuid.uuid4(),
            )
            prepared = await service.prepare_new_turn(
                agent_session.id,
                agent_args.user_prompt,
                config=config,
                caller_run_id=input.run_id,
                holder_ended=_holder_ended(admitting),
            )
        except EntitlementRequired as e:
            # Retrying cannot help until the entitlement is restored.
            raise ApplicationError(
                str(e), e.detail, type=e.__class__.__name__, non_retryable=True
            ) from e
        except TracecatConflictError as e:
            # Another action holds the session, like the built-in workflow
            # refusing a second run of the same session.
            raise ApplicationError(
                str(e), type=e.__class__.__name__, non_retryable=True
            ) from e
        except AgentProviderCredentialsNotFoundError as e:
            # The organization must add credentials; retrying cannot help.
            raise_application_error_from_classification(
                agent_provider_credentials_missing(e)
            )
        except (
            TracecatAuthorizationError,
            TracecatNotFoundError,
            TracecatValidationError,
        ) as e:
            raise_application_error_from_classification(invalid_agent_configuration(e))
        return await build_child_agent_turn(
            prepared,
            role=service.execution_role,
            config=prepared.config,
            tools=prepared.tools,
            timeout_seconds=clamp_agent_timeout_seconds(agent_args.timeout_seconds),
            max_requests=agent_args.max_requests,
            max_tool_calls=agent_args.max_tool_calls,
        )


@activity.defn
async def read_workflow_agent_turn_activity(
    input: ReadWorkflowAgentTurnInput,
) -> ReadWorkflowAgentTurnResult:
    """Read a finished turn's output through the backend that ran it."""
    # Read results of a backend that was disabled mid-turn too.
    backend = find_agent_backend(input.backend_id)
    if backend is None:
        raise ApplicationError("Agent backend is unavailable", non_retryable=True)
    result = backend.parse_turn_result(input.result)
    # The backend returns its own output; workflows read it under the rule
    # the built-in backend applies, so both give the same value.
    return ReadWorkflowAgentTurnResult(
        output=resolve_agent_output(result.output), error=result.error
    )
