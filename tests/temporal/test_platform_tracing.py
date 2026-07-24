from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tracecat import config
from tracecat.observability.otel import (
    initialize_platform_tracing,
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
