"""Run workflow agent actions on the session's agent backend.

A workflow agent action builds the built-in durable workflow's arguments
either way. When its session belongs to another backend, these activities
prepare the turn on that backend and read its result, and the workflow starts
the backend's turn workflow as its own child by type name.
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.backends.registry import (
    DEFAULT_AGENT_BACKEND,
    find_agent_backend,
    get_agent_backend,
    get_workflow_type,
)
from tracecat.agent.backends.schemas import AgentWorkflowArgs, ChildAgentTurn
from tracecat.agent.backends.selection import select_backend_id
from tracecat.agent.backends.types import SessionWorkflowContext
from tracecat.agent.error_policy import (
    agent_provider_credentials_missing,
    invalid_agent_configuration,
)
from tracecat.agent.gateway_providers import is_gateway_provider
from tracecat.agent.preset.activities import (
    resolve_custom_model_provider_config_activity,
)
from tracecat.agent.service import AgentManagementService
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
from tracecat.agent.types import (
    AgentConfig,
    apply_preset_overrides,
    clamp_agent_timeout_seconds,
)
from tracecat.agent.workflow_turns.schemas import (
    PrepareWorkflowAgentTurnInput,
    PrepareWorkflowAgentTurnResult,
    ReadWorkflowAgentTurnInput,
    ReadWorkflowAgentTurnResult,
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


async def _turn_backend_id(
    service: AgentSessionService, args: AgentWorkflowArgs
) -> str:
    """Pick the turn's backend. A session that exists keeps its own.

    A workflow action continues only workflow sessions on another backend.
    Chat, channel and approval sessions keep their own surface's rules, such
    as the entitlements it needs, and reviewer forks keep having no tools.
    """
    if session := await service.get_session(args.agent_args.session_id):
        backend_id = session.backend_id or DEFAULT_AGENT_BACKEND
        if backend_id != DEFAULT_AGENT_BACKEND and (
            session.entity_type != AgentSessionEntity.WORKFLOW.value
            or session.forked_from_session_id is not None
        ):
            raise_application_error_from_classification(invalid_agent_configuration())
        return backend_id
    if args.continue_existing_session:
        # The built-in workflow reports the missing session, as before.
        return DEFAULT_AGENT_BACKEND
    return await select_backend_id(
        service.session, service.organization_id, AgentSessionEntity.WORKFLOW
    )


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


def _holder_ended(db: AsyncSession) -> Callable[[AgentSession], Awaitable[bool]]:
    """Decide whether the run holding a workflow session has ended for good.

    Only then may another workflow agent action take the session over. A run
    has ended when its backend turn is not running and its parent workflow has
    closed, or, when neither ever started, once the reservation has outlived
    the start grace period.
    """

    async def ended(session: AgentSession) -> bool:
        run_id = session.curr_run_id
        backend = find_agent_backend(session.backend_id)
        if run_id is None or backend is None:
            return False
        if await backend.get_turn_lifecycle(run_id) is TurnLifecycle.RUNNING:
            return False
        client = await get_temporal_client()
        parent = client.get_workflow_handle(workflow_agent_turn_workflow_id(run_id))
        try:
            description = await parent.describe()
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                return False
            # Every reservation write bumps updated_at, so this is its age.
            outlived = await db.scalar(
                select(AgentSession.id).where(
                    AgentSession.id == session.id,
                    AgentSession.updated_at <= func.now() - _UNSTARTED_TURN_GRACE,
                )
            )
            return outlived is not None
        return description.status not in _RUNNING

    return ended


@activity.defn
async def prepare_workflow_agent_turn_activity(
    input: PrepareWorkflowAgentTurnInput,
) -> PrepareWorkflowAgentTurnResult:
    """Prepare a workflow agent action's turn on its session's backend.

    Returns no turn when the action runs on the built-in backend. Otherwise
    creates the workflow session on the selected backend if needed, reserves
    the turn, and builds the backend's workflow input. A retry after the
    reservation returns the same turn.
    """
    ctx_role.set(input.role)
    args = input.args
    agent_args = args.agent_args
    async with AgentSessionService.with_session(role=input.role) as service:
        backend_id = await _turn_backend_id(service, args)
        if backend_id == DEFAULT_AGENT_BACKEND:
            return PrepareWorkflowAgentTurnResult()
        try:
            config = await _with_gateway_provider_config(
                input.role, await _turn_config(service, args)
            )
            agent_session, created = await service.get_or_create_session(
                AgentSessionCreate(
                    id=agent_args.session_id,
                    title=args.title,
                    created_by=input.role.user_id,
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
            prepared = await service.prepare_new_turn(
                agent_session.id,
                agent_args.user_prompt,
                config=config,
                caller_run_id=input.run_id,
                holder_ended=_holder_ended(service.session),
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
        try:
            backend = get_agent_backend(
                prepared.backend_id, harness_type=prepared.harness_type
            )
            workflow_args = await backend.build_workflow_args(
                SessionWorkflowContext(
                    session_id=prepared.session_id,
                    backend_id=prepared.backend_id,
                    harness_type=prepared.harness_type,
                    title=prepared.title,
                    entity_type=prepared.entity_type,
                    entity_id=prepared.entity_id,
                    tools=tuple(prepared.tools) if prepared.tools is not None else None,
                    agent_preset_id=prepared.agent_preset_id,
                    agent_preset_version_id=prepared.agent_preset_version_id,
                    role=service.execution_role,
                    config=prepared.config,
                    prompt=prepared.prompt,
                    run_id=prepared.run_id,
                    stream_id=prepared.active_stream_id,
                    timeout_seconds=clamp_agent_timeout_seconds(
                        agent_args.timeout_seconds
                    ),
                    max_requests=agent_args.max_requests,
                    max_tool_calls=agent_args.max_tool_calls,
                )
            )
        except Exception:
            # No workflow will start for this reservation, so free the session
            # now. A retry reserves it again.
            await service.release_prepared_turn(prepared.session_id, prepared.run_id)
            raise
    return PrepareWorkflowAgentTurnResult(
        turn=ChildAgentTurn(
            backend_id=prepared.backend_id,
            session_id=prepared.session_id,
            run_id=prepared.run_id,
            workflow_type=get_workflow_type(backend),
            workflow_id=backend.workflow_id(prepared.run_id),
            task_queue=backend.task_queue,
            priority=backend.priority,
            retry_policy=backend.retry_policy,
            args=workflow_args,
        )
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
    return ReadWorkflowAgentTurnResult(output=result.output, error=result.error)
