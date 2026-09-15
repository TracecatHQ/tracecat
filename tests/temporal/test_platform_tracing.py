from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import baggage, context
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from temporalio import activity, workflow
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tracecat import config
from tracecat.observability.otel import (
    initialize_platform_tracing,
    instrument_fastapi_app,
    platform_span,
    set_current_span_attributes,
    shutdown_platform_tracing,
    temporal_tracing_interceptor,
)

pytestmark = [pytest.mark.temporal]


@activity.defn
async def traced_executor_activity() -> str:
    info = activity.info()
    set_current_span_attributes(
        {
            "tracecat.workflow.execution.id": info.workflow_id,
            "tracecat.action.name": "core.test.trace",
            "temporal.activity.attempt": info.attempt,
            "temporal.task_queue": info.task_queue,
        }
    )
    return "ok"


@workflow.defn(sandboxed=False)
class TracedWorkflow:
    @workflow.run
    async def run(self) -> str:
        return await workflow.execute_activity(
            traced_executor_activity,
            start_to_close_timeout=timedelta(seconds=10),
        )


_SYNTHETIC_BAGGAGE_KEY = "synthetic-platform-baggage"


@activity.defn
async def baggage_probe_activity() -> bool:
    return baggage.get_baggage(_SYNTHETIC_BAGGAGE_KEY) is not None


@workflow.defn(sandboxed=False)
class BaggageProbeWorkflow:
    @workflow.run
    async def run(self) -> list[bool]:
        workflow_has_baggage = baggage.get_baggage(_SYNTHETIC_BAGGAGE_KEY) is not None
        activity_has_baggage = await workflow.execute_activity(
            baggage_probe_activity,
            start_to_close_timeout=timedelta(seconds=10),
        )
        return [workflow_has_baggage, activity_has_baggage]


_SYNTHETIC_FAILURE_DETAIL = "synthetic-customer-secret-must-not-export"


@activity.defn
async def failing_traced_activity() -> None:
    raise ApplicationError(_SYNTHETIC_FAILURE_DETAIL, non_retryable=True)


@workflow.defn(sandboxed=False)
class FailingTracedWorkflow:
    @workflow.run
    async def run(self) -> None:
        await workflow.execute_activity(
            failing_traced_activity,
            start_to_close_timeout=timedelta(seconds=10),
        )


@dataclass(frozen=True, slots=True)
class GoldenTraceOrigin:
    workflow_id: str
    workflow_execution_id: str
    action_ref: str
    trigger_type: str
    agent_session_id: str
    agent_run_id: str


@activity.defn
async def golden_agent_activity(origin: GoldenTraceOrigin) -> str:
    set_current_span_attributes(
        {
            "tracecat.workflow.id": origin.workflow_id,
            "tracecat.workflow.execution.id": origin.workflow_execution_id,
            "tracecat.action.ref": origin.action_ref,
            "tracecat.trigger.type": origin.trigger_type,
            "tracecat.agent.session.id": origin.agent_session_id,
            "tracecat.agent.run.id": origin.agent_run_id,
        }
    )
    with platform_span("tracecat.agent.prepare"):
        pass
    with platform_span("tracecat.agent.runtime"):
        pass
    return "agent-complete"


@workflow.defn(sandboxed=False)
class GoldenAgentWorkflow:
    @workflow.run
    async def run(self, origin: GoldenTraceOrigin) -> str:
        return await workflow.execute_activity(
            golden_agent_activity,
            origin,
            start_to_close_timeout=timedelta(seconds=10),
        )


@workflow.defn(sandboxed=False)
class GoldenDSLWorkflow:
    @workflow.run
    async def run(self, origin: GoldenTraceOrigin) -> str:
        return await workflow.execute_child_workflow(
            GoldenAgentWorkflow.run,
            origin,
            id=f"golden-agent/{origin.agent_run_id}",
            task_queue="platform-tracing-golden-test",
        )


@pytest.fixture
async def traced_env(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[WorkflowEnvironment, InMemorySpanExporter], None]:
    shutdown_platform_tracing()
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    exporter = InMemorySpanExporter()
    initialize_platform_tracing("tracecat-api", exporter=exporter)
    interceptor = temporal_tracing_interceptor()
    assert interceptor is not None

    async with await WorkflowEnvironment.start_time_skipping(
        interceptors=[interceptor]
    ) as env:
        yield env, exporter

    shutdown_platform_tracing()


@pytest.mark.anyio
async def test_async_api_dispatch_keeps_one_temporal_activity_trace(
    traced_env: tuple[WorkflowEnvironment, InMemorySpanExporter],
) -> None:
    env, exporter = traced_env
    workflow_id = "synthetic-platform-trace"

    async with Worker(
        env.client,
        task_queue="platform-tracing-test",
        workflows=[TracedWorkflow],
        activities=[traced_executor_activity],
        max_cached_workflows=0,
    ):
        runtime = initialize_platform_tracing(
            "tracecat-api",
            exporter=exporter,
        )
        assert runtime is not None
        with runtime.tracer("test.api").start_as_current_span(
            "POST /workflow-executions"
        ):
            dispatch = asyncio.create_task(
                env.client.execute_workflow(
                    TracedWorkflow.run,
                    id=workflow_id,
                    task_queue="platform-tracing-test",
                )
            )

        assert await dispatch == "ok"

    spans = exporter.get_finished_spans()
    assert all(span.context is not None for span in spans)
    trace_ids = {span.context.trace_id for span in spans if span.context is not None}
    assert len(trace_ids) == 1
    span_names = {span.name for span in spans}
    assert "POST /workflow-executions" in span_names
    assert any("StartWorkflow" in name for name in span_names)
    assert any("RunWorkflow" in name for name in span_names)
    assert any("RunActivity" in name for name in span_names)

    activity_spans = [span for span in spans if "RunActivity" in span.name]
    assert len(activity_spans) == 1
    activity_span = activity_spans[0]
    assert activity_span.attributes is not None
    assert activity_span.attributes["tracecat.workflow.execution.id"] == workflow_id
    assert activity_span.attributes["tracecat.action.name"] == "core.test.trace"

    workflow_spans = [span for span in spans if "RunWorkflow" in span.name]
    assert len(workflow_spans) == 1


@pytest.mark.anyio
async def test_http_dsl_agent_golden_trace_has_complete_hierarchy(
    traced_env: tuple[WorkflowEnvironment, InMemorySpanExporter],
) -> None:
    env, exporter = traced_env
    origin = GoldenTraceOrigin(
        workflow_id="00000000-0000-4000-8000-000000000001",
        workflow_execution_id="wf_synthetic/exec_synthetic",
        action_ref="investigate",
        trigger_type="webhook",
        agent_session_id="00000000-0000-4000-8000-000000000002",
        agent_run_id="00000000-0000-4000-8000-000000000003",
    )
    app = FastAPI()

    async def webhook(workflow_id: str, secret: str) -> dict[str, str]:
        del workflow_id, secret
        set_current_span_attributes(
            {
                "tracecat.workflow.id": origin.workflow_id,
                "tracecat.workflow.execution.id": origin.workflow_execution_id,
                "tracecat.trigger.type": origin.trigger_type,
            }
        )
        result = await env.client.execute_workflow(
            GoldenDSLWorkflow.run,
            origin,
            id=origin.workflow_execution_id,
            task_queue="platform-tracing-golden-test",
        )
        return {"result": result}

    app.add_api_route(
        "/webhooks/{workflow_id}/{secret}",
        webhook,
        methods=["POST"],
    )
    instrument_fastapi_app(app, service_name="tracecat-api")

    async with Worker(
        env.client,
        task_queue="platform-tracing-golden-test",
        workflows=[GoldenDSLWorkflow, GoldenAgentWorkflow],
        activities=[golden_agent_activity],
        max_cached_workflows=0,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/webhooks/synthetic-workflow/synthetic-webhook-secret"
            )

    assert response.status_code == 200
    assert response.json() == {"result": "agent-complete"}
    spans = exporter.get_finished_spans()
    contexts = [span.context for span in spans if span.context is not None]
    assert len(contexts) == len(spans)
    assert len({context.trace_id for context in contexts}) == 1

    spans_by_id = {
        context.span_id: span for context, span in zip(contexts, spans, strict=True)
    }
    roots = [span for span in spans if span.parent is None or not span.parent.is_valid]
    orphans = [
        span
        for span in spans
        if span.parent is not None
        and span.parent.is_valid
        and span.parent.span_id not in spans_by_id
    ]
    assert len(roots) == 1
    assert orphans == []
    assert "webhooks" in roots[0].name

    span_names = {span.name for span in spans}
    assert "tracecat.agent.prepare" in span_names
    assert "tracecat.agent.runtime" in span_names
    assert any("StartChildWorkflow" in name for name in span_names)
    assert any("RunActivity" in name for name in span_names)

    agent_activity = next(span for span in spans if "RunActivity" in span.name)
    assert agent_activity.attributes is not None
    assert (
        agent_activity.attributes["tracecat.workflow.execution.id"]
        == origin.workflow_execution_id
    )
    assert agent_activity.attributes["tracecat.action.ref"] == origin.action_ref
    assert agent_activity.attributes["tracecat.agent.session.id"] == (
        origin.agent_session_id
    )
    assert agent_activity.attributes["tracecat.agent.run.id"] == origin.agent_run_id
    assert "synthetic-webhook-secret" not in str(spans)


@pytest.mark.anyio
async def test_unparented_workflow_still_emits_one_complete_trace(
    traced_env: tuple[WorkflowEnvironment, InMemorySpanExporter],
) -> None:
    env, exporter = traced_env

    async with Worker(
        env.client,
        task_queue="platform-tracing-unparented-test",
        workflows=[TracedWorkflow],
        activities=[traced_executor_activity],
        max_cached_workflows=0,
    ):
        assert (
            await env.client.execute_workflow(
                TracedWorkflow.run,
                id="synthetic-unparented-workflow",
                task_queue="platform-tracing-unparented-test",
            )
            == "ok"
        )

    spans = exporter.get_finished_spans()
    trace_ids = {span.context.trace_id for span in spans if span.context is not None}
    assert len(trace_ids) == 1
    assert any("RunWorkflow" in span.name for span in spans)
    assert any("RunActivity" in span.name for span in spans)


@pytest.mark.anyio
async def test_headerless_workflow_start_emits_one_trace_per_run(
    traced_env: tuple[WorkflowEnvironment, InMemorySpanExporter],
) -> None:
    """A run started without a trace header still forms one complete trace.

    Schedules and other Temporal-originated starts carry no carrier. Each run
    must resolve to a single trace whose only unexported span is the synthetic
    per-run parent, and separate runs must not share a trace.
    """
    env, exporter = traced_env
    headerless_config = env.client.config()
    headerless_config["interceptors"] = []
    headerless_client = Client(**headerless_config)
    task_queue = "platform-tracing-headerless-test"

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[TracedWorkflow],
        activities=[traced_executor_activity],
        max_cached_workflows=0,
    ):
        for workflow_id in ("synthetic-headerless-1", "synthetic-headerless-2"):
            assert (
                await headerless_client.execute_workflow(
                    TracedWorkflow.run,
                    id=workflow_id,
                    task_queue=task_queue,
                )
                == "ok"
            )

    spans = exporter.get_finished_spans()
    spans_by_run: dict[str, list[ReadableSpan]] = {}
    for span in spans:
        assert span.attributes is not None
        run_id = span.attributes["temporalRunID"]
        assert isinstance(run_id, str)
        spans_by_run.setdefault(run_id, []).append(span)
    assert len(spans_by_run) == 2

    run_trace_ids: list[int] = []
    for run_spans in spans_by_run.values():
        names = {span.name.split(":")[0] for span in run_spans}
        assert {
            "RunWorkflow",
            "StartActivity",
            "RunActivity",
            "CompleteWorkflow",
        } <= names
        contexts = [span.context for span in run_spans if span.context is not None]
        assert len(contexts) == len(run_spans)
        trace_ids = {context.trace_id for context in contexts}
        assert len(trace_ids) == 1
        run_trace_ids.append(trace_ids.pop())
        exported_ids = {context.span_id for context in contexts}
        unexported_parents = {
            span.parent.span_id
            for span in run_spans
            if span.parent is not None and span.parent.span_id not in exported_ids
        }
        assert len(unexported_parents) == 1
    assert len(set(run_trace_ids)) == 2


@pytest.mark.anyio
async def test_temporal_tracing_does_not_propagate_baggage(
    traced_env: tuple[WorkflowEnvironment, InMemorySpanExporter],
) -> None:
    env, _ = traced_env

    async with Worker(
        env.client,
        task_queue="platform-tracing-baggage-test",
        workflows=[BaggageProbeWorkflow],
        activities=[baggage_probe_activity],
        max_cached_workflows=0,
    ):
        baggage_context = baggage.set_baggage(
            _SYNTHETIC_BAGGAGE_KEY,
            "synthetic-do-not-propagate",
        )
        token = context.attach(baggage_context)
        try:
            baggage_visibility = await env.client.execute_workflow(
                BaggageProbeWorkflow.run,
                id="synthetic-platform-baggage-test",
                task_queue="platform-tracing-baggage-test",
            )
        finally:
            context.detach(token)

    assert baggage_visibility == [False, False]


@pytest.mark.anyio
async def test_temporal_tracing_does_not_export_failure_details(
    traced_env: tuple[WorkflowEnvironment, InMemorySpanExporter],
) -> None:
    env, exporter = traced_env

    async with Worker(
        env.client,
        task_queue="platform-tracing-failure-test",
        workflows=[FailingTracedWorkflow],
        activities=[failing_traced_activity],
        max_cached_workflows=0,
    ):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                FailingTracedWorkflow.run,
                id="synthetic-platform-failure-test",
                task_queue="platform-tracing-failure-test",
            )

    failed_spans = [
        span
        for span in exporter.get_finished_spans()
        if "RunActivity" in span.name or "CompleteWorkflow" in span.name
    ]
    assert len(failed_spans) == 2
    for span in failed_spans:
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description is None
        assert span.attributes is not None
        assert span.attributes["error.type"] == "temporal.failure"
        assert span.events == ()
        assert _SYNTHETIC_FAILURE_DETAIL not in str(span)
