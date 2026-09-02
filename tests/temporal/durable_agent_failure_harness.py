"""Real-Worker runners for the durable-agent failure contract matrix."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import httpx
import orjson
import pytest
import tracecat_ee.agent.workflows.durable as durable_workflow_module
from temporalio import activity
from temporalio.client import (
    Client,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowHistory,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker
from tracecat_ee.agent.activities import (
    BuildAgentToolDefsArgs,
    BuildAgentToolDefsResult,
    BuildToolDefsResult,
    EmitSessionDoneInputs,
    EmitSessionErrorInputs,
)
from tracecat_ee.agent.types import AgentWorkflowID
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs, DurableAgentWorkflow

from tracecat import config
from tracecat.agent.executor.activity import AgentExecutorInput, AgentExecutorResult
from tracecat.agent.sandbox.llm_proxy import (
    LLMProxyError,
    LLMRoute,
    LLMRoutingPlan,
    LLMSocketProxy,
)
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.activities import (
    CreateSessionInput,
    CreateSessionResult,
    FinalizeTurnInput,
    FinalizeTurnResult,
    LoadSessionInput,
    LoadSessionResult,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.dsl.interceptor import RuntimeErrorAttributionInterceptor
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import RuntimeErrorClassification
from tracecat.temporal.errors import raise_application_error_from_classification
from tracecat.workflow.executions.enums import TemporalSearchAttr


class FaultPoint(StrEnum):
    """Distinct failure boundaries exercised by the acceptance matrix."""

    WORKSPACE_CONTEXT_MISSING = "workspace_context.missing"
    ORGANIZATION_CONTEXT_MISSING = "organization_context.missing"
    TOOL_DEFINITIONS_ACTIVITY = "tool_definitions.activity"
    TOOL_DEFINITIONS_RESULT = "tool_definitions.result"
    SESSION_RESULT = "session.result"
    EXECUTOR_ACTIVITY = "executor.activity"
    EXECUTOR_TIMEOUT = "executor.timeout"
    EXECUTOR_RESULT = "executor.result"
    WORKFLOW_INTERNAL = "workflow.internal"


class GatewayRoute(StrEnum):
    """User-visible route topologies supported by agent LLM forwarding."""

    DIRECT_PROVIDER = "direct_provider"
    CUSTOM_GATEWAY = "custom_gateway"
    MANAGED_LITELLM = "managed_litellm"


class GatewayFailureMode(StrEnum):
    """Representative HTTP, transport, and streaming gateway failures."""

    HTTP_400 = "http_400"
    HTTP_401 = "http_401"
    HTTP_429 = "http_429"
    HTTP_503 = "http_503"
    HTTP_504 = "http_504"
    CONNECT = "connect"
    READ_TIMEOUT = "read_timeout"
    STREAM_DISCONNECT = "stream_disconnect"


@dataclass(frozen=True, slots=True)
class GatewayFailureInjection:
    """One source-level failure emitted by the real LLM socket proxy."""

    route: GatewayRoute
    mode: GatewayFailureMode


@dataclass(frozen=True, slots=True)
class FailureInjection:
    """One fault injected into an otherwise successful agent workflow."""

    point: FaultPoint
    classification: RuntimeErrorClassification | None = None
    activity_non_retryable: bool = False
    terminal_stream_error_emitted: bool | None = None
    gateway_failure: GatewayFailureInjection | None = None
    emit_session_error_fails: bool = False


@dataclass(slots=True)
class _HarnessState:
    injection: FailureInjection
    diagnostic: str
    build_calls: int = 0
    create_calls: int = 0
    executor_calls: int = 0
    workflow_internal_calls: int = 0
    emit_error_failures: int = 0
    emitted_errors: list[EmitSessionErrorInputs] = field(default_factory=list)
    emitted_done: list[EmitSessionDoneInputs] = field(default_factory=list)
    finalized_turns: list[FinalizeTurnInput] = field(default_factory=list)

    @property
    def fault_calls(self) -> int:
        match self.injection.point:
            case (
                FaultPoint.WORKSPACE_CONTEXT_MISSING
                | FaultPoint.ORGANIZATION_CONTEXT_MISSING
            ):
                return 0
            case (
                FaultPoint.TOOL_DEFINITIONS_ACTIVITY
                | FaultPoint.TOOL_DEFINITIONS_RESULT
            ):
                return self.build_calls
            case FaultPoint.SESSION_RESULT:
                return self.create_calls
            case FaultPoint.EXECUTOR_ACTIVITY | FaultPoint.EXECUTOR_TIMEOUT:
                return self.executor_calls
            case FaultPoint.EXECUTOR_RESULT:
                return self.executor_calls
            case FaultPoint.WORKFLOW_INTERNAL:
                return self.workflow_internal_calls


@dataclass(frozen=True, slots=True)
class ScenarioObservation:
    """Actual terminal state consumed by the declarative matrix assertion."""

    root: WorkflowHandle[Any, Any]
    failure: WorkflowFailureError
    history: WorkflowHistory
    fault_calls: int
    emit_error_failures: int
    emitted_errors: tuple[EmitSessionErrorInputs, ...]
    emitted_done: tuple[EmitSessionDoneInputs, ...]
    finalized_turns: tuple[FinalizeTurnInput, ...]


type WorkerFactory = Callable[..., Worker]


class _FakeWriter:
    """Minimal StreamWriter surface consumed by the LLM socket proxy."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self._closing = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    async def wait_closed(self) -> None:
        return None


class _FailingGatewayStream(httpx.AsyncByteStream):
    """Response stream that disconnects after successful HTTP headers."""

    def __init__(self, request: httpx.Request, diagnostic: str) -> None:
        self._request = request
        self._diagnostic = diagnostic

    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise httpx.ReadError(self._diagnostic, request=self._request)
        yield b""  # pragma: no cover - required to type this as an async iterator


def _gateway_status_code(mode: GatewayFailureMode) -> int | None:
    match mode:
        case GatewayFailureMode.HTTP_400:
            return 400
        case GatewayFailureMode.HTTP_401:
            return 401
        case GatewayFailureMode.HTTP_429:
            return 429
        case GatewayFailureMode.HTTP_503:
            return 503
        case GatewayFailureMode.HTTP_504:
            return 504
        case _:
            return None


def _gateway_routing_plan(route: GatewayRoute) -> tuple[LLMRoutingPlan, str | None]:
    managed_route = LLMRoute(
        base_url="http://managed-litellm.invalid",
        model_provider="openai",
        mode="managed",
    )
    if route is GatewayRoute.MANAGED_LITELLM:
        return LLMRoutingPlan(managed_route=managed_route, direct_routes={}), None

    model = route.value
    base_url = (
        "https://direct-provider.invalid"
        if route is GatewayRoute.DIRECT_PROVIDER
        else "https://custom-gateway.invalid"
    )
    direct_route = LLMRoute(
        base_url=base_url,
        model_provider=(
            "anthropic" if route is GatewayRoute.DIRECT_PROVIDER else "openai"
        ),
        mode="direct",
        authorization="Bearer synthetic-test-key",
    )
    return (
        LLMRoutingPlan(
            managed_route=managed_route,
            direct_routes={model: direct_route},
        ),
        model,
    )


async def _gateway_failure_classification(
    injection: GatewayFailureInjection,
    diagnostic: str,
) -> RuntimeErrorClassification:
    """Exercise the real proxy and return the classification it emits."""
    errors: list[LLMProxyError] = []
    routing_plan, request_model = _gateway_routing_plan(injection.route)

    async def handler(request: httpx.Request) -> httpx.Response:
        status_code = _gateway_status_code(injection.mode)
        if status_code is not None:
            return httpx.Response(
                status_code,
                request=request,
                json={"error": diagnostic},
            )
        if injection.mode is GatewayFailureMode.CONNECT:
            raise httpx.ConnectError(diagnostic, request=request)
        if injection.mode is GatewayFailureMode.READ_TIMEOUT:
            raise httpx.ReadTimeout(diagnostic, request=request)
        if injection.mode is GatewayFailureMode.STREAM_DISCONNECT:
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/event-stream"},
                stream=_FailingGatewayStream(request, diagnostic),
            )
        raise AssertionError(f"Unhandled gateway failure mode: {injection.mode}")

    proxy = LLMSocketProxy(
        socket_path=Path(f"/tmp/tracecat-gateway-probe-{uuid.uuid4()}.sock"),
        routing_plan=routing_plan,
        on_error=errors.append,
    )
    proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writer = _FakeWriter()
    request_body: dict[str, object] = {"messages": []}
    if request_model is not None:
        request_body["model"] = request_model

    try:
        await proxy._forward_request(
            {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {"content-type": "application/json"},
                "body": orjson.dumps(request_body),
            },
            cast(asyncio.StreamWriter, writer),
        )
    finally:
        if proxy._client is not None:
            await proxy._client.aclose()

    if len(errors) != 1:
        raise AssertionError(
            f"Expected one proxy error for {injection}, observed {len(errors)}"
        )
    return errors[0].classification


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    """Run each scenario against an isolated Temporal dev server."""
    search_attributes = [
        TemporalSearchAttr.ERROR_OWNER.key,
        TemporalSearchAttr.TRIGGER_TYPE.key,
        TemporalSearchAttr.EXECUTION_TYPE.key,
        TemporalSearchAttr.TRIGGERED_BY_USER_ID.key,
        TemporalSearchAttr.WORKSPACE_ID.key,
        TemporalSearchAttr.CORRELATION_ID.key,
    ]
    async with await WorkflowEnvironment.start_local(
        data_converter=get_data_converter(compression_enabled=False),
        search_attributes=search_attributes,
        dev_server_log_level="error",
    ) as environment:
        yield environment


@pytest.fixture
def worker_factory() -> Iterator[WorkerFactory]:
    """Create Workers with the same runner and interceptor as production."""
    with ThreadPoolExecutor(max_workers=4) as activity_executor:

        def create_worker(
            client: Client,
            *,
            activities: list[Callable[..., Any]],
            workflows: list[type],
            task_queue: str,
        ) -> Worker:
            return Worker(
                client=client,
                task_queue=task_queue,
                activities=activities,
                workflows=workflows,
                workflow_runner=new_sandbox_runner(),
                interceptors=[RuntimeErrorAttributionInterceptor()],
                activity_executor=activity_executor,
            )

        yield create_worker


def _workflow_args(injection: FailureInjection) -> AgentWorkflowArgs:
    role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=(
            None
            if injection.point is FaultPoint.WORKSPACE_CONTEXT_MISSING
            else uuid.uuid4()
        ),
        organization_id=(
            None
            if injection.point is FaultPoint.ORGANIZATION_CONTEXT_MISSING
            else uuid.uuid4()
        ),
    )
    agent_config = AgentConfig(
        model_name="test-model",
        model_provider="test-provider",
        actions=[],
    )
    return AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            session_id=uuid.uuid4(),
            user_prompt="synthetic prompt",
            config=agent_config,
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )


def _activities(state: _HarnessState) -> list[Callable[..., Any]]:
    @activity.defn(name="load_session_activity")
    async def load_session(input: LoadSessionInput) -> LoadSessionResult:
        del input
        return LoadSessionResult(found=False)

    @activity.defn(name="create_session_activity")
    async def create_session(input: CreateSessionInput) -> CreateSessionResult:
        state.create_calls += 1
        if state.injection.point is FaultPoint.SESSION_RESULT:
            return CreateSessionResult(
                session_id=input.session_id,
                success=False,
                error=state.diagnostic,
            )
        return CreateSessionResult(session_id=input.session_id, success=True)

    @activity.defn(name="build_agent_tool_definitions")
    async def build_agent_tool_definitions(
        args: BuildAgentToolDefsArgs,
    ) -> BuildAgentToolDefsResult:
        state.build_calls += 1
        if state.injection.point is FaultPoint.TOOL_DEFINITIONS_ACTIVITY:
            if state.injection.classification is not None:
                raise_application_error_from_classification(
                    state.injection.classification
                )
            if state.injection.activity_non_retryable:
                raise ApplicationError(state.diagnostic, non_retryable=True)
            raise RuntimeError(state.diagnostic)

        if state.injection.point is FaultPoint.TOOL_DEFINITIONS_RESULT:
            return BuildAgentToolDefsResult(scopes={})

        registry_lock = RegistryLock(origins={}, actions={})
        return BuildAgentToolDefsResult(
            scopes={
                scope.scope: BuildToolDefsResult(
                    tool_definitions={},
                    registry_lock=registry_lock,
                )
                for scope in args.scopes
            }
        )

    @activity.defn(name="run_agent_activity")
    async def run_agent(input: AgentExecutorInput) -> AgentExecutorResult:
        del input
        state.executor_calls += 1
        if state.injection.point is FaultPoint.EXECUTOR_TIMEOUT:
            await asyncio.sleep(60)
            raise AssertionError("executor timeout activity unexpectedly completed")
        if state.injection.point is FaultPoint.EXECUTOR_ACTIVITY:
            if state.injection.classification is not None:
                raise_application_error_from_classification(
                    state.injection.classification
                )
            raise RuntimeError(state.diagnostic)
        if state.injection.point is FaultPoint.EXECUTOR_RESULT:
            return AgentExecutorResult(
                success=False,
                error=state.diagnostic,
                classification=state.injection.classification,
                terminal_stream_error_emitted=(
                    state.injection.terminal_stream_error_emitted
                ),
            )
        return AgentExecutorResult(success=True, output="ok")

    @activity.defn(name="emit_session_error")
    async def emit_session_error(input: EmitSessionErrorInputs) -> None:
        state.emitted_errors.append(input)
        if state.injection.emit_session_error_fails:
            state.emit_error_failures += 1
            raise RuntimeError(state.diagnostic)

    @activity.defn(name="finalize_turn_activity")
    async def finalize_turn(input: FinalizeTurnInput) -> FinalizeTurnResult:
        state.finalized_turns.append(input)
        return FinalizeTurnResult(terminal_done_emitted=True)

    @activity.defn(name="emit_session_done")
    async def emit_session_done(input: EmitSessionDoneInputs) -> None:
        state.emitted_done.append(input)

    return [
        load_session,
        create_session,
        build_agent_tool_definitions,
        run_agent,
        emit_session_error,
        finalize_turn,
        emit_session_done,
    ]


async def run_failure_scenario(
    env: WorkflowEnvironment,
    worker_factory: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
    *,
    injection: FailureInjection,
    diagnostic: str,
) -> ScenarioObservation:
    """Execute one matrix row through the production workflow configuration."""
    if injection.gateway_failure is not None:
        injection = replace(
            injection,
            classification=await _gateway_failure_classification(
                injection.gateway_failure,
                diagnostic,
            ),
        )
    state = _HarnessState(injection=injection, diagnostic=diagnostic)
    args = _workflow_args(injection)
    task_queue = f"durable-agent-failure-{uuid.uuid4()}"

    monkeypatch.setattr(config, "TRACECAT__AGENT_EXECUTOR_QUEUE", task_queue)
    monkeypatch.setattr(durable_workflow_module, "mint_mcp_token", lambda **_: "mcp")
    monkeypatch.setattr(
        durable_workflow_module, "mint_agent_otel_token", lambda **_: "otel"
    )

    if injection.point is FaultPoint.WORKFLOW_INTERNAL:

        def fail_llm_token(**_: object) -> str:
            state.workflow_internal_calls += 1
            raise RuntimeError(diagnostic)

        monkeypatch.setattr(durable_workflow_module, "mint_llm_token", fail_llm_token)
    else:
        monkeypatch.setattr(
            durable_workflow_module, "mint_llm_token", lambda **_: "llm"
        )

    if injection.point is FaultPoint.EXECUTOR_TIMEOUT:
        monkeypatch.setattr(
            durable_workflow_module,
            "clamp_agent_timeout_seconds",
            lambda _: 0.05,
        )
        monkeypatch.setattr(
            durable_workflow_module,
            "AGENT_TIMEOUT_CLEANUP_BUFFER_SECONDS",
            0,
        )

    async with worker_factory(
        env.client,
        activities=_activities(state),
        workflows=[DurableAgentWorkflow],
        task_queue=task_queue,
    ):
        handle = await env.client.start_workflow(
            DurableAgentWorkflow.run,
            args,
            id=AgentWorkflowID(args.agent_args.session_id),
            task_queue=task_queue,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
        )
        with pytest.raises(WorkflowFailureError) as exc_info:
            await handle.result()

    return ScenarioObservation(
        root=handle,
        failure=exc_info.value,
        history=await handle.fetch_history(),
        fault_calls=state.fault_calls,
        emit_error_failures=state.emit_error_failures,
        emitted_errors=tuple(state.emitted_errors),
        emitted_done=tuple(state.emitted_done),
        finalized_turns=tuple(state.finalized_turns),
    )


async def replay_scenario_history(
    env: WorkflowEnvironment,
    history: WorkflowHistory,
) -> None:
    """Verify that the current production worker can replay a failed history."""
    result = await Replayer(
        workflows=[DurableAgentWorkflow],
        workflow_runner=new_sandbox_runner(),
        data_converter=env.client.data_converter,
        interceptors=[RuntimeErrorAttributionInterceptor()],
    ).replay_workflow(history, raise_on_replay_failure=False)
    assert result.replay_failure is None
