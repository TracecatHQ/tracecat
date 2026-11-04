"""Workflow agent actions run on their session's backend.

The workflow builds the built-in durable workflow's arguments either way. The
prepare activity returns no turn when the session is on the built-in backend,
so that path is unchanged. Otherwise it creates the workflow session on the
selected backend, reserves the turn with the action's own configuration, and
hands back the backend's workflow input to start as a child by type name.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import Priority, RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.schemas import (
    AgentWorkflowArgs,
    ChildAgentTurn,
    WorkflowApprovalSubmission,
    WorkflowCancelRequest,
)
from tracecat.agent.backends.types import (
    SessionForkContext,
    SessionWorkflowContext,
    TurnResult,
)
from tracecat.agent.preset.activities import CustomModelProviderConfigResult
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
from tracecat.agent.types import (
    AgentConfig,
    apply_preset_overrides,
    clamp_agent_timeout_seconds,
)
from tracecat.agent.workflow_turns.activities import (
    _holder_ended,
    prepare_workflow_agent_turn_activity,
    read_workflow_agent_turn_activity,
    select_workflow_agent_backend_activity,
)
from tracecat.agent.workflow_turns.schemas import (
    PrepareWorkflowAgentTurnInput,
    ReadWorkflowAgentTurnInput,
    SelectWorkflowAgentBackendInput,
)
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession
from tracecat.exceptions import EntitlementRequired, TracecatConflictError

ACTIVITIES = "tracecat.agent.workflow_turns.activities"


@workflow.defn(name="InstalledTurnWorkflow")
class InstalledTurnWorkflow:
    @workflow.run
    async def run(self, args: dict[str, Any], /) -> dict[str, Any]:
        return args

    @workflow.update
    def set_approvals(self, submission: WorkflowApprovalSubmission) -> bool:
        return True

    @workflow.update
    def request_cancel(self, request: WorkflowCancelRequest) -> None:
        pass


class InstalledBackend(AgentBackend[dict[str, Any], dict[str, Any]]):
    workflow = InstalledTurnWorkflow
    name = "Installed"
    default_harness = "installed"
    supported_harnesses = frozenset({"installed"})
    caller_owned_turns = True
    task_queue = "installed-queue"
    retry_policy = RetryPolicy(maximum_attempts=2)
    priority = Priority(priority_key=4)

    async def prepare_fork(self, context: SessionForkContext) -> None:
        pass

    async def build_workflow_args(
        self, context: SessionWorkflowContext
    ) -> dict[str, Any]:
        return {
            "session_id": str(context.session_id),
            "prompt": context.prompt,
            "instructions": context.config.instructions,
            "output_type": context.config.output_type,
            "limits": [
                context.timeout_seconds,
                context.max_requests,
                context.max_tool_calls,
            ],
        }

    def parse_turn_result(self, result: object) -> TurnResult:
        match result:
            case {"output": output}:
                return TurnResult(output=output)
            case {"error": str(error)}:
                return TurnResult(output=None, error=error)
        raise AssertionError(f"Unexpected result: {result!r}")


def _role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )


def _args(
    *,
    session_id: uuid.UUID | None = None,
    continue_existing_session: bool = False,
    config: AgentConfig | None = None,
    preset_slug: str | None = None,
) -> AgentWorkflowArgs:
    return AgentWorkflowArgs(
        role=_role(),
        agent_args=RunAgentArgs(
            user_prompt="Triage this alert",
            session_id=session_id or uuid.uuid4(),
            config=config
            if config is not None or preset_slug is not None
            else AgentConfig(
                model_name="test-model",
                model_provider="openai",
                instructions="Be brief",
                output_type={"type": "object"},
            ),
            preset_slug=preset_slug,
            max_requests=7,
            max_tool_calls=3,
            timeout_seconds=600,
        ),
        title="Alert triage",
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
        continue_existing_session=continue_existing_session,
    )


def _service(
    *, existing: SimpleNamespace | None = None, backend_id: str = "external"
) -> SimpleNamespace:
    role = _role()
    created = SimpleNamespace(id=None, title="Alert triage", backend_id=backend_id)

    async def get_or_create_session(args: AgentSessionCreate):
        if existing is not None:
            return existing, False
        created.id = args.id
        return created, True

    async def prepare_new_turn(
        session_id: uuid.UUID,
        prompt: str,
        *,
        config: AgentConfig,
        caller_run_id: uuid.UUID,
        holder_ended: object,
    ) -> SimpleNamespace:
        del holder_ended
        return SimpleNamespace(
            prompt=prompt,
            session_id=session_id,
            run_id=caller_run_id,
            active_stream_id=uuid.uuid4(),
            config=config,
            title="Alert triage",
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
            tools=None,
            agent_preset_id=None,
            agent_preset_version_id=None,
            backend_id=existing.backend_id if existing else backend_id,
            harness_type="installed",
        )

    return SimpleNamespace(
        session=AsyncMock(),
        role=role,
        organization_id=role.organization_id,
        execution_role=role,
        get_session=AsyncMock(return_value=existing),
        get_or_create_session=AsyncMock(side_effect=get_or_create_session),
        auto_title_session_on_first_prompt=AsyncMock(),
        prepare_new_turn=AsyncMock(side_effect=prepare_new_turn),
    )


@contextlib.contextmanager
def _activity_env(service: SimpleNamespace, *, selected: str) -> Iterator[AsyncMock]:
    @contextlib.asynccontextmanager
    async def with_session(*, role: Role) -> AsyncIterator[SimpleNamespace]:
        del role
        yield service

    with (
        patch(f"{ACTIVITIES}.AgentSessionService.with_session", with_session),
        patch(
            f"{ACTIVITIES}.select_backend_id", AsyncMock(return_value=selected)
        ) as select,
        patch(f"{ACTIVITIES}.get_agent_backend", return_value=InstalledBackend()),
    ):
        yield select


async def _select(args: AgentWorkflowArgs) -> str | None:
    selected = await select_workflow_agent_backend_activity(
        SelectWorkflowAgentBackendInput(role=args.role, args=args)
    )
    return selected.backend_id


async def _prepare(
    args: AgentWorkflowArgs, *, backend_id: str = "external"
) -> ChildAgentTurn:
    return await prepare_workflow_agent_turn_activity(
        PrepareWorkflowAgentTurnInput(
            role=args.role, run_id=uuid.uuid4(), backend_id=backend_id, args=args
        )
    )


@pytest.mark.anyio
async def test_builtin_backend_keeps_the_durable_workflow_path() -> None:
    service = _service()
    with _activity_env(service, selected="oss") as select:
        assert await _select(_args()) is None
    select.assert_awaited_once()
    assert select.await_args is not None
    assert select.await_args.args[2] is AgentSessionEntity.WORKFLOW
    service.get_or_create_session.assert_not_awaited()
    service.prepare_new_turn.assert_not_awaited()


@pytest.mark.anyio
async def test_selected_backend_runs_the_action_with_its_own_config() -> None:
    service = _service()
    args = _args()
    with _activity_env(service, selected="external"):
        assert await _select(args) == "external"
        # Selection writes nothing; the turn's parent prepares it.
        service.get_or_create_session.assert_not_awaited()
        turn = await _prepare(args)

    created = service.get_or_create_session.await_args.args[0]
    assert created.id == args.agent_args.session_id
    assert created.backend_id == "external"
    assert created.entity_type is AgentSessionEntity.WORKFLOW
    assert created.entity_id == args.entity_id
    service.auto_title_session_on_first_prompt.assert_awaited_once()
    # The turn runs on the action's configuration, not the session's.
    assert service.prepare_new_turn.await_args.kwargs["config"] is (
        args.agent_args.config
    )

    # The action's own run ID reserves the session.
    assert turn.run_id == service.prepare_new_turn.await_args.kwargs["caller_run_id"]
    assert turn.backend_id == "external"
    assert turn.session_id == args.agent_args.session_id
    assert turn.workflow_type == "InstalledTurnWorkflow"
    assert turn.workflow_id == f"agent/{turn.run_id}"
    assert turn.task_queue == "installed-queue"
    assert turn.retry_policy == RetryPolicy(maximum_attempts=2)
    assert turn.priority == Priority(priority_key=4)
    assert turn.args == {
        "session_id": str(args.agent_args.session_id),
        "prompt": "Triage this alert",
        "instructions": "Be brief",
        "output_type": {"type": "object"},
        # Deployment bounds apply, as for the built-in workflow.
        "limits": [clamp_agent_timeout_seconds(600), 7, 3],
    }


@pytest.mark.anyio
async def test_existing_session_keeps_its_own_backend() -> None:
    """Continuing a session never re-selects: its backend is part of it."""
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        title="Earlier",
        backend_id="external",
        entity_type=AgentSessionEntity.WORKFLOW.value,
        forked_from_session_id=None,
        created_by=None,
    )
    service = _service(existing=existing)
    args = _args(session_id=existing.id, continue_existing_session=True)
    with _activity_env(service, selected="oss") as select:
        turn = await _prepare(args)
    select.assert_not_awaited()
    service.auto_title_session_on_first_prompt.assert_not_awaited()
    assert turn.session_id == existing.id


@pytest.mark.anyio
@pytest.mark.parametrize("existing_backend", ["oss", None])
async def test_existing_builtin_session_keeps_the_durable_workflow_path(
    existing_backend: str | None,
) -> None:
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        backend_id=existing_backend,
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        created_by=None,
    )
    service = _service(existing=existing)
    args = _args(session_id=existing.id, continue_existing_session=True)
    with _activity_env(service, selected="external"):
        assert await _select(args) is None
    service.prepare_new_turn.assert_not_awaited()


@pytest.mark.anyio
async def test_missing_session_to_continue_keeps_the_durable_workflow_path() -> None:
    """The built-in workflow reports the missing session, as before."""
    service = _service()
    with _activity_env(service, selected="external") as select:
        assert await _select(_args(continue_existing_session=True)) is None
    select.assert_not_awaited()
    service.get_or_create_session.assert_not_awaited()


@pytest.mark.anyio
async def test_preset_action_layers_its_overrides_on_the_preset() -> None:
    service = _service()
    preset_config = AgentConfig(
        model_name="preset-model",
        model_provider="anthropic",
        instructions="Preset instructions",
        actions=["core.http_request"],
        tool_tunnels={"core.http_request": "tunnel-a", "core.script": "tunnel-b"},
    )

    @contextlib.asynccontextmanager
    async def with_preset_config(**kwargs: Any) -> AsyncIterator[AgentConfig]:
        assert kwargs["slug"] == "triage"
        assert kwargs["prune_tool_tunnels"] is False
        yield preset_config

    agents = Mock(with_preset_config=with_preset_config)
    args = _args(
        preset_slug="triage",
        config=AgentConfig(
            model_name="preset-override",
            model_provider="preset-override",
            instructions="Action instructions",
            output_type="str",
        ),
    )
    with (
        _activity_env(service, selected="external"),
        patch(f"{ACTIVITIES}.AgentManagementService", return_value=agents),
    ):
        await _prepare(args)

    config = service.prepare_new_turn.await_args.kwargs["config"]
    assert config.model_name == "preset-model"
    assert config.instructions == "Preset instructions\nAction instructions"
    assert config.output_type == "str"
    assert config.tool_tunnels == {"core.http_request": "tunnel-a"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "entity_type",
    [
        AgentSessionEntity.WORKSPACE_CHAT,
        AgentSessionEntity.CASE,
        AgentSessionEntity.EXTERNAL_CHANNEL,
        # Approval forks have no tools; an action's config would give them some.
        AgentSessionEntity.APPROVAL,
    ],
)
async def test_action_cannot_continue_another_surfaces_session_on_a_backend(
    entity_type: AgentSessionEntity,
) -> None:
    """Otherwise a workflow action would skip the workflow entitlement, or the
    other surface's own rules, by naming that surface's session."""
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        backend_id="external",
        entity_type=entity_type.value,
        forked_from_session_id=None,
        created_by=None,
    )
    service = _service(existing=existing)
    args = _args(session_id=existing.id, continue_existing_session=True)
    with (
        _activity_env(service, selected="external"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await _prepare(args)
    assert exc_info.value.non_retryable
    service.prepare_new_turn.assert_not_awaited()


@pytest.mark.anyio
async def test_action_cannot_continue_a_reviewer_fork_on_a_backend() -> None:
    """A reviewer fork of a workflow session has no tools; an action's config
    would give it some."""
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        backend_id="external",
        entity_type=AgentSessionEntity.WORKFLOW.value,
        forked_from_session_id=uuid.uuid4(),
        created_by=None,
    )
    service = _service(existing=existing)
    args = _args(session_id=existing.id, continue_existing_session=True)
    with (
        _activity_env(service, selected="external"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await _prepare(args)
    assert exc_info.value.non_retryable
    service.prepare_new_turn.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("actor", "creator", "refused"),
    [
        # A session a user started stays theirs, as in the API,
        ("user", "teammate", True),
        ("user", "self", False),
        # including against runs no user is attributed to, such as webhooks.
        ("background", "teammate", True),
        # Background runs leave no user creator; later runs may continue them.
        ("user", None, False),
        ("background", None, False),
    ],
)
async def test_action_continues_only_sessions_its_user_may_write(
    actor: str, creator: str | None, refused: bool
) -> None:
    user_id = uuid.uuid4() if actor == "user" else None
    role = _role().model_copy(update={"user_id": user_id})
    created_by = {"teammate": uuid.uuid4(), "self": user_id, None: None}[creator]
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        title="Earlier",
        backend_id="external",
        entity_type=AgentSessionEntity.WORKFLOW.value,
        forked_from_session_id=None,
        created_by=created_by,
    )
    service = _service(existing=existing)
    service.role = role
    args = _args(session_id=existing.id, continue_existing_session=True)
    with _activity_env(service, selected="external"):
        if refused:
            with pytest.raises(ApplicationError):
                await _select(args)
            service.prepare_new_turn.assert_not_awaited()
        else:
            assert (await _prepare(args)).session_id == existing.id


@pytest.mark.anyio
async def test_preparation_refuses_a_backend_other_than_the_selected_one() -> None:
    """The turn runs only where the action selected it."""
    service = _service()
    with (
        _activity_env(service, selected="oss"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await _prepare(_args(), backend_id="external")
    assert exc_info.value.non_retryable
    service.prepare_new_turn.assert_not_awaited()


@pytest.mark.anyio
async def test_busy_session_fails_the_action_without_retry() -> None:
    """Like the built-in workflow refusing a second run of the same session."""
    service = _service()
    service.prepare_new_turn.side_effect = TracecatConflictError(
        "This chat already has an active turn"
    )
    with (
        _activity_env(service, selected="external"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await _prepare(_args())
    assert exc_info.value.non_retryable
    assert exc_info.value.type == "TracecatConflictError"


@pytest.mark.anyio
async def test_gateway_provider_uses_the_organizations_endpoint() -> None:
    """An action's base URL never reaches a gateway provider, as on the
    built-in path, so a workflow cannot redirect the organization's key."""
    service = _service()
    catalog_id = uuid.uuid4()
    args = _args(
        config=AgentConfig(
            model_name="action-model",
            model_provider="custom-model-provider",
            catalog_id=catalog_id,
            base_url="https://elsewhere.invalid",
        )
    )
    resolve = AsyncMock(
        return_value=CustomModelProviderConfigResult(
            base_url="https://gateway.internal.invalid",
            passthrough=True,
            model_name="org-model",
        )
    )
    with (
        _activity_env(service, selected="external"),
        patch(f"{ACTIVITIES}.resolve_custom_model_provider_config_activity", resolve),
    ):
        await _prepare(args)
    resolve.assert_awaited_once_with(
        args.role, catalog_id, False, "custom-model-provider"
    )
    config = service.prepare_new_turn.await_args.kwargs["config"]
    assert config.base_url == "https://gateway.internal.invalid"
    assert config.passthrough is True
    assert config.model_name == "org-model"


@pytest.mark.anyio
async def test_refused_entitlement_fails_the_action_without_retry() -> None:
    service = _service()
    service.prepare_new_turn.side_effect = EntitlementRequired(
        "agent_runtime_workflows"
    )
    with (
        _activity_env(service, selected="external"),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await _prepare(_args())
    assert exc_info.value.non_retryable
    assert exc_info.value.type == "EntitlementRequired"


@pytest.mark.anyio
async def test_read_returns_the_backends_output() -> None:
    with patch(f"{ACTIVITIES}.find_agent_backend", return_value=InstalledBackend()):
        read = await read_workflow_agent_turn_activity(
            ReadWorkflowAgentTurnInput(
                backend_id="external", result={"output": {"verdict": "benign"}}
            )
        )
        failed = await read_workflow_agent_turn_activity(
            ReadWorkflowAgentTurnInput(backend_id="external", result={"error": "boom"})
        )
    assert (read.output, read.error) == ({"verdict": "benign"}, None)
    assert (failed.output, failed.error) == (None, "boom")


@pytest.mark.anyio
async def test_read_fails_without_retry_when_the_backend_is_gone() -> None:
    with (
        patch(f"{ACTIVITIES}.find_agent_backend", return_value=None),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await read_workflow_agent_turn_activity(
            ReadWorkflowAgentTurnInput(backend_id="external", result={})
        )
    assert exc_info.value.non_retryable


def test_preset_overrides_match_the_builtin_workflow() -> None:
    preset = AgentConfig(
        model_name="m",
        model_provider="p",
        instructions=None,
        actions=["a", "b"],
        tool_tunnels={"a": "t1", "b": "t2"},
    )
    merged = apply_preset_overrides(
        preset,
        AgentConfig(
            model_name="x", model_provider="x", actions=["b"], instructions="Extra"
        ),
    )
    assert merged.actions == ["b"]
    assert merged.instructions == "Extra"
    assert merged.tool_tunnels == {"b": "t2"}
    # The preset's own configuration is left as resolved.
    assert preset.actions == ["a", "b"]
    assert apply_preset_overrides(preset, None).tool_tunnels == {"a": "t1", "b": "t2"}


def _held_session() -> AgentSession:
    return AgentSession(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        title="Held",
        entity_type=AgentSessionEntity.WORKFLOW.value,
        entity_id=uuid.uuid4(),
        backend_id="external",
        curr_run_id=uuid.uuid4(),
    )


@contextlib.contextmanager
def _holder_env(
    *,
    lifecycle: TurnLifecycle,
    parent: WorkflowExecutionStatus | RPCError,
    claimed: bool,
) -> Iterator[AsyncMock]:
    backend = InstalledBackend()
    handle = Mock(
        describe=AsyncMock(side_effect=parent)
        if isinstance(parent, RPCError)
        else AsyncMock(return_value=SimpleNamespace(status=parent))
    )
    client = Mock(get_workflow_handle=Mock(return_value=handle))
    with (
        patch(f"{ACTIVITIES}.find_agent_backend", return_value=backend),
        patch.object(backend, "get_turn_lifecycle", AsyncMock(return_value=lifecycle)),
        patch(f"{ACTIVITIES}.get_temporal_client", AsyncMock(return_value=client)),
        patch(
            f"{ACTIVITIES}.claim_unstarted_workflow_id",
            AsyncMock(return_value=claimed),
        ) as claim,
    ):
        yield claim


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("lifecycle", "parent", "outlived", "claimed", "ended"),
    [
        # The backend's turn is still running.
        (TurnLifecycle.RUNNING, WorkflowExecutionStatus.COMPLETED, True, True, False),
        # Its parent is still reading the result.
        (TurnLifecycle.COMPLETED, WorkflowExecutionStatus.RUNNING, True, True, False),
        # Both closed; with the parent gone its child cannot run either.
        (
            TurnLifecycle.COMPLETED,
            WorkflowExecutionStatus.COMPLETED,
            False,
            False,
            True,
        ),
        (TurnLifecycle.FAILED, WorkflowExecutionStatus.TERMINATED, False, False, True),
        # Never started: only a reservation past the start grace is given up,
        (TurnLifecycle.FAILED, "not_found", False, True, False),
        # and only once its parent's ID is claimed, so a late start of that
        # parent is rejected instead of running beside the new turn.
        (TurnLifecycle.FAILED, "not_found", True, True, True),
        (TurnLifecycle.FAILED, "not_found", True, False, False),
        # An unknown lookup failure is never taken as an end.
        (TurnLifecycle.FAILED, "unavailable", True, True, False),
    ],
)
async def test_holder_ends_only_when_its_turn_can_never_finish(
    lifecycle: TurnLifecycle,
    parent: WorkflowExecutionStatus | str,
    outlived: bool,
    claimed: bool,
    ended: bool,
) -> None:
    db = AsyncMock()
    db.scalar.return_value = uuid.uuid4() if outlived else None
    match parent:
        case "not_found":
            status: WorkflowExecutionStatus | RPCError = RPCError(
                "missing", RPCStatusCode.NOT_FOUND, b""
            )
        case "unavailable":
            status = RPCError("down", RPCStatusCode.UNAVAILABLE, b"")
        case WorkflowExecutionStatus():
            status = parent
        case _:
            raise AssertionError(parent)
    held = _held_session()
    with _holder_env(lifecycle=lifecycle, parent=status, claimed=claimed) as claim:
        assert await _holder_ended(db)(held) is ended
    if parent == "not_found" and outlived:
        claim.assert_awaited_once()
        assert claim.await_args is not None
        assert claim.await_args.args[1] == f"workflow-agent-turn/{held.curr_run_id}"
    else:
        claim.assert_not_awaited()
