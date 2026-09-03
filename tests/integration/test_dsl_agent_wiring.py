from __future__ import annotations

import uuid
from collections.abc import Callable, Generator, Iterator, Sequence
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from tracecat_ee.agent.activities import (
    BuildAgentToolDefsArgs,
    BuildAgentToolDefsResult,
    BuildToolDefsResult,
)
from tracecat_ee.agent.schemas import AgentActionArgs
from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow
from tracecat_registry.integrations.agents.slack import SlackbotContext

from tests.shared import to_data
from tracecat import config
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
)
from tracecat.agent.session.activities import (
    CreateSessionInput,
    CreateSessionResult,
    LoadSessionInput,
    LoadSessionMessagesInput,
    LoadSessionMessagesResult,
    LoadSessionResult,
)
from tracecat.agent.worker import (
    get_activities as get_agent_worker_activities,
)
from tracecat.agent.worker import (
    new_sandbox_runner as new_agent_sandbox_runner,
)
from tracecat.auth.types import Role
from tracecat.dsl.action import (
    BuildAgentArgsActivityInput,
    FinalizeSlackbotActivityInput,
    PreparedSlackbot,
)
from tracecat.dsl.common import RETRY_POLICIES, DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.worker import get_activities as get_dsl_worker_activities
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock


def _activity_name(activity_def: object) -> str:
    return getattr(activity_def, "__temporal_activity_definition").name


@pytest.fixture(scope="session")
def minio_server() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.fixture(autouse=True)
def disable_result_externalization(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(config, "TRACECAT__RESULT_EXTERNALIZATION_ENABLED", False)
    monkeypatch.setattr(
        config,
        "TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES",
        1024 * 1024,
    )
    yield


def _replace_activity(
    activities: Sequence[Callable[..., Any]],
    replacement: Callable[..., Any],
) -> list[Callable[..., Any]]:
    target_name = _activity_name(replacement)
    replaced = False
    updated: list[Callable[..., Any]] = []
    for existing in activities:
        if _activity_name(existing) == target_name:
            updated.append(replacement)
            replaced = True
        else:
            updated.append(existing)
    if not replaced:
        raise AssertionError(f"Activity {target_name!r} was not registered")
    return updated


def create_mock_create_session_activity(
    captured_inputs: list[CreateSessionInput] | None = None,
) -> Callable[..., Any]:
    @activity.defn(name="create_session_activity")
    async def mock_create_session_activity(
        input: CreateSessionInput,
    ) -> CreateSessionResult:
        if captured_inputs is not None:
            captured_inputs.append(input)
        return CreateSessionResult(session_id=input.session_id, success=True)

    return mock_create_session_activity


def create_mock_load_session_activity() -> Callable[..., Any]:
    @activity.defn(name="load_session_activity")
    async def mock_load_session_activity(_: LoadSessionInput) -> LoadSessionResult:
        return LoadSessionResult(
            found=False,
            sdk_session_id=None,
            sdk_session_data=None,
            is_fork=False,
        )

    return mock_load_session_activity


def create_mock_load_session_messages_activity() -> Callable[..., Any]:
    @activity.defn(name="load_session_messages_activity")
    async def mock_load_session_messages_activity(
        _: LoadSessionMessagesInput,
    ) -> LoadSessionMessagesResult:
        return LoadSessionMessagesResult(messages=[])

    return mock_load_session_messages_activity


def create_mock_build_tool_definitions_activity() -> Callable[..., Any]:
    @activity.defn(name="build_agent_tool_definitions")
    async def mock_build_tool_definitions(
        args: BuildAgentToolDefsArgs,
    ) -> BuildAgentToolDefsResult:
        tool_result = BuildToolDefsResult(
            tool_definitions={},
            registry_lock=RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={},
            ),
            user_mcp_claims=None,
            allowed_internal_tools=None,
        )
        return BuildAgentToolDefsResult(
            scopes={scope.scope: tool_result for scope in args.scopes}
        )

    return mock_build_tool_definitions


def create_mock_slackbot_agent_args_activity() -> Callable[..., Any]:
    @activity.defn(name="prepare_slackbot_activity")
    async def mock_prepare_slackbot_activity(
        input: BuildAgentArgsActivityInput,
    ) -> PreparedSlackbot:
        assert input.action == "ai.slackbot"
        return PreparedSlackbot(
            args=AgentActionArgs(
                user_prompt="Prepared Slack prompt",
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=["tools.slack.post_message"],
            ),
            context=SlackbotContext(channel_id="C01234567", ts="1700000000.000001"),
        )

    return mock_prepare_slackbot_activity


def create_mock_finalize_slackbot_activity(
    captured_inputs: list[FinalizeSlackbotActivityInput],
) -> Callable[..., Any]:
    @activity.defn(name="finalize_slackbot_activity")
    async def mock_finalize_slackbot_activity(
        input: FinalizeSlackbotActivityInput,
    ) -> None:
        captured_inputs.append(input)

    return mock_finalize_slackbot_activity


def create_mock_run_agent_activity(
    *,
    output: str,
    captured_inputs: list[AgentExecutorInput] | None = None,
) -> Callable[..., Any]:
    @activity.defn(name="run_agent_activity")
    async def mock_run_agent_activity(
        input: AgentExecutorInput,
    ) -> AgentExecutorResult:
        if captured_inputs is not None:
            captured_inputs.append(input)
        activity.heartbeat("Mock agent running")
        return AgentExecutorResult(success=True, output=output)

    return mock_run_agent_activity


@pytest.fixture
def agent_worker_factory(
    threadpool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., Worker], None, None]:
    def create_agent_worker(
        client: Client,
        *,
        task_queue: str,
        activities: Sequence[Callable[..., Any]],
    ) -> Worker:
        monkeypatch.setattr(config, "TRACECAT__AGENT_EXECUTOR_QUEUE", task_queue)
        monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", task_queue)
        return Worker(
            client=client,
            task_queue=task_queue,
            activities=activities,
            workflows=[DurableAgentWorkflow],
            workflow_runner=new_agent_sandbox_runner(),
            activity_executor=threadpool,
        )

    yield create_agent_worker


class TestDSLAgentWiring:
    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_dsl_workflow_executes_ai_agent_on_agent_worker(
        self,
        test_role: Role,
        temporal_client: Client,
        test_worker_factory: Callable[..., Worker],
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        captured_inputs: list[AgentExecutorInput] = []
        agent_activities = list(get_agent_worker_activities())
        for replacement in (
            create_mock_create_session_activity(),
            create_mock_load_session_activity(),
            create_mock_load_session_messages_activity(),
            create_mock_build_tool_definitions_activity(),
        ):
            agent_activities = _replace_activity(agent_activities, replacement)
        agent_activities.append(
            create_mock_run_agent_activity(
                output="dsl-agent-wired",
                captured_inputs=captured_inputs,
            )
        )

        dsl = DSLInput(
            title="DSL agent wiring",
            description="Verify ai.agent spawns a child agent workflow",
            entrypoint=DSLEntrypoint(ref="agent"),
            actions=[
                ActionStatement(
                    ref="agent",
                    action="ai.agent",
                    args={
                        "user_prompt": "Investigate this alert",
                        "model_name": "gpt-4o-mini",
                        "model_provider": "openai",
                    },
                )
            ],
            returns="${{ ACTIONS.agent.result }}",
        )
        wf_id = WorkflowUUID.new_uuid4()

        wf_exec_id = generate_exec_id(wf_id)
        async with test_worker_factory(
            temporal_client,
            activities=list(get_dsl_worker_activities()),
        ):
            async with agent_worker_factory(
                temporal_client,
                task_queue=config.TRACECAT__AGENT_QUEUE,
                activities=agent_activities,
            ):
                result = await temporal_client.execute_workflow(
                    DSLWorkflow.run,
                    DSLRunArgs(
                        dsl=dsl,
                        role=test_role,
                        wf_id=wf_id,
                        registry_lock=RegistryLock(origins={}, actions={}),
                    ),
                    id=wf_exec_id,
                    task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

        data = await to_data(result)
        assert data["output"] == "dsl-agent-wired"
        assert len(captured_inputs) == 1
        executor_input = captured_inputs[0]
        assert executor_input.curr_run_id is not None
        assert executor_input.curr_run_id != executor_input.session_id

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_slackbot_interface_executes_and_finalizes_on_agent_worker(
        self,
        test_role: Role,
        temporal_client: Client,
        test_worker_factory: Callable[..., Worker],
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        finalized: list[FinalizeSlackbotActivityInput] = []
        dsl_activities = _replace_activity(
            list(get_dsl_worker_activities()),
            create_mock_slackbot_agent_args_activity(),
        )
        dsl_activities = _replace_activity(
            dsl_activities,
            create_mock_finalize_slackbot_activity(finalized),
        )
        agent_activities = list(get_agent_worker_activities())
        for replacement in (
            create_mock_create_session_activity(),
            create_mock_load_session_activity(),
            create_mock_load_session_messages_activity(),
            create_mock_build_tool_definitions_activity(),
        ):
            agent_activities = _replace_activity(agent_activities, replacement)
        agent_activities.append(
            create_mock_run_agent_activity(output="slackbot-interface-wired")
        )

        dsl = DSLInput(
            title="Slackbot interface wiring",
            description="Verify Slackbot spawns and finalizes a child agent workflow",
            entrypoint=DSLEntrypoint(ref="agent"),
            actions=[
                ActionStatement(
                    ref="agent",
                    action="ai.slackbot",
                    args={
                        "event": None,
                        "prompt": "Investigate this alert",
                        "instructions": "Reply in the channel",
                        "channel_id": "C01234567",
                        "model": {
                            "model_name": "gpt-4o-mini",
                            "model_provider": "openai",
                        },
                    },
                )
            ],
            returns="${{ ACTIONS.agent.result }}",
        )
        wf_id = WorkflowUUID.new_uuid4()

        async with test_worker_factory(
            temporal_client,
            activities=dsl_activities,
        ):
            async with agent_worker_factory(
                temporal_client,
                task_queue=config.TRACECAT__AGENT_QUEUE,
                activities=agent_activities,
            ):
                result = await temporal_client.execute_workflow(
                    DSLWorkflow.run,
                    DSLRunArgs(dsl=dsl, role=test_role, wf_id=wf_id),
                    id=generate_exec_id(wf_id),
                    task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

        data = await to_data(result)
        assert data["output"] == "slackbot-interface-wired"
        assert len(finalized) == 1
        assert finalized[0].succeeded is True
        assert finalized[0].context.channel_id == "C01234567"

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_dsl_workflow_forks_existing_agent_session(
        self,
        test_role: Role,
        temporal_client: Client,
        test_worker_factory: Callable[..., Worker],
        agent_worker_factory: Callable[..., Worker],
    ) -> None:
        parent_session_id = uuid.uuid4()
        captured_inputs: list[CreateSessionInput] = []

        agent_activities = list(get_agent_worker_activities())
        for replacement in (
            create_mock_create_session_activity(captured_inputs),
            create_mock_load_session_activity(),
            create_mock_load_session_messages_activity(),
            create_mock_build_tool_definitions_activity(),
        ):
            agent_activities = _replace_activity(agent_activities, replacement)
        agent_activities.append(
            create_mock_run_agent_activity(output="dsl-agent-existing-session")
        )

        dsl = DSLInput(
            title="DSL agent existing session wiring",
            description="Verify ai.agent forks a provided session ID",
            entrypoint=DSLEntrypoint(ref="agent"),
            actions=[
                ActionStatement(
                    ref="agent",
                    action="ai.agent",
                    args={
                        "user_prompt": "Continue this investigation",
                        "model_name": "gpt-4o-mini",
                        "model_provider": "openai",
                        "session_id": str(parent_session_id),
                    },
                )
            ],
            returns="${{ ACTIONS.agent.result }}",
        )
        wf_id = WorkflowUUID.new_uuid4()

        async with test_worker_factory(
            temporal_client,
            activities=list(get_dsl_worker_activities()),
        ):
            async with agent_worker_factory(
                temporal_client,
                task_queue=config.TRACECAT__AGENT_QUEUE,
                activities=agent_activities,
            ):
                result = await temporal_client.execute_workflow(
                    DSLWorkflow.run,
                    DSLRunArgs(
                        dsl=dsl,
                        role=test_role,
                        wf_id=wf_id,
                        registry_lock=RegistryLock(origins={}, actions={}),
                    ),
                    id=generate_exec_id(wf_id),
                    task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    execution_timeout=timedelta(seconds=60),
                )

        data = await to_data(result)
        assert data["output"] == "dsl-agent-existing-session"
        assert len(captured_inputs) == 1
        create_input = captured_inputs[0]
        assert create_input.session_id != parent_session_id
        assert create_input.parent_session_id == parent_session_id
        assert create_input.require_existing is False
