from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind
from temporalio.contrib.opentelemetry import TracingInterceptor

from tracecat import config
from tracecat.logger._logger import _add_trace_context
from tracecat.observability import otel as platform_otel
from tracecat.observability.otel import (
    TRACE_ID_HEADER,
    TRACE_SAMPLED_HEADER,
    current_trace_id,
    get_platform_tracing,
    initialize_platform_tracing,
    instrument_fastapi_app,
    shutdown_platform_tracing,
    temporal_tracing_interceptor,
)


class RaisingSpanExporter(SpanExporter):
    """Exporter used to prove export failures stay off the request path."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        del spans
        raise RuntimeError("synthetic export failure")


@pytest.fixture(autouse=True)
def reset_platform_tracing(monkeypatch: pytest.MonkeyPatch):
    shutdown_platform_tracing()
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", False)
    yield
    shutdown_platform_tracing()


def test_platform_tracing_is_disabled_by_default() -> None:
    assert initialize_platform_tracing("tracecat-api") is None
    assert get_platform_tracing() is None
    assert temporal_tracing_interceptor() is None


def test_platform_relay_reads_only_credential_free_endpoint_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-gateway:4318")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://otel-gateway:4318/v1/traces",
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=secret")

    assert platform_otel.platform_otel_collector_env() == {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-gateway:4318",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": ("http://otel-gateway:4318/v1/traces"),
    }


def test_platform_tracing_initialization_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    exporter = InMemorySpanExporter()

    first = initialize_platform_tracing("tracecat-api", exporter=exporter)
    second = initialize_platform_tracing("tracecat-api", exporter=exporter)

    assert first is not None
    assert second is first
    assert get_platform_tracing() is first
    assert isinstance(temporal_tracing_interceptor(), TracingInterceptor)


def test_current_trace_id_matches_active_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    runtime = initialize_platform_tracing(
        "tracecat-api", exporter=InMemorySpanExporter()
    )
    assert runtime is not None

    with runtime.tracer("test.trace-link").start_as_current_span("request") as span:
        trace_id = current_trace_id()

    assert trace_id is not None
    assert len(trace_id) == 32
    assert span.get_span_context().trace_id == int(trace_id, 16)


def test_active_trace_context_is_added_to_log_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    runtime = initialize_platform_tracing(
        "tracecat-api", exporter=InMemorySpanExporter()
    )
    assert runtime is not None
    record: Any = {
        "extra": {
            "trace_id": "spoofed-trace-id",
            "span_id": "spoofed-span-id",
            "trace_sampled": False,
        }
    }

    with runtime.tracer("test.logging").start_as_current_span("request"):
        _add_trace_context(record)

    assert len(record["extra"]["trace_id"]) == 32
    assert len(record["extra"]["span_id"]) == 16
    assert record["extra"]["trace_sampled"] is True


def test_fastapi_headers_are_omitted_when_platform_tracing_is_disabled() -> None:
    app = FastAPI()
    app.add_api_route("/items", lambda: {"status": "ok"})

    assert instrument_fastapi_app(app, service_name="tracecat-api") is None

    with TestClient(app) as client:
        response = client.get("/items")

    assert response.status_code == 200
    assert TRACE_ID_HEADER not in response.headers
    assert TRACE_SAMPLED_HEADER not in response.headers


def test_fastapi_request_emits_sanitized_span_and_trace_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    exporter = InMemorySpanExporter()
    initialize_platform_tracing("tracecat-api", exporter=exporter)
    app = FastAPI()
    app.add_api_route("/items", lambda: {"status": "ok"})

    instrument_fastapi_app(app, service_name="tracecat-api")

    with TestClient(app) as client:
        response = client.get(
            "/items?token=do-not-export",
            headers={
                "Authorization": "Bearer synthetic-do-not-export",
                "Cookie": "session=synthetic-do-not-export",
                "User-Agent": "synthetic-user-agent-secret",
            },
        )

    assert response.status_code == 200
    assert len(response.headers[TRACE_ID_HEADER]) == 32
    assert response.headers[TRACE_SAMPLED_HEADER] == "true"
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.context is not None
    assert span.attributes is not None
    assert f"{span.context.trace_id:032x}" == response.headers[TRACE_ID_HEADER]
    assert span.resource.attributes["service.name"] == "tracecat-api"
    assert span.attributes["url.full"] == "/items"
    assert span.attributes["url.query"] == "[REDACTED]"
    assert span.attributes["http.user_agent"] == "[REDACTED]"
    assert span.attributes["user_agent.original"] == "[REDACTED]"
    assert "do-not-export" not in str(span.attributes)
    assert "synthetic-user-agent-secret" not in str(span.attributes)
    assert "authorization" not in str(span.attributes).lower()
    assert "cookie" not in str(span.attributes).lower()


def test_fastapi_span_uses_route_template_instead_of_path_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
    exporter = InMemorySpanExporter()
    initialize_platform_tracing("tracecat-api", exporter=exporter)
    app = FastAPI()
    app.add_api_route(
        "/webhooks/{workflow_id}/{secret}",
        lambda workflow_id, secret: {"status": "ok"},
        methods=["POST"],
    )
    instrument_fastapi_app(app, service_name="tracecat-api")

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/synthetic-workflow-id/synthetic-webhook-secret"
        )

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes is not None
    span_attributes = spans[0].attributes
    assert span_attributes["http.target"] == "/webhooks/{workflow_id}/{secret}"
    assert span_attributes["http.url"] == "/webhooks/{workflow_id}/{secret}"
    assert span_attributes["url.full"] == "/webhooks/{workflow_id}/{secret}"
    assert span_attributes["url.path"] == "/webhooks/{workflow_id}/{secret}"
    assert "synthetic-workflow-id" not in str(span_attributes)
    assert "synthetic-webhook-secret" not in str(span_attributes)


def test_fastapi_header_capture_cannot_be_enabled_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST", ".*")
    monkeypatch.setenv(
        "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE", ".*"
    )
    exporter = InMemorySpanExporter()
    initialize_platform_tracing("tracecat-api", exporter=exporter)
    app = FastAPI()

    def get_items(response: Response) -> dict[str, str]:
        response.set_cookie("session", "synthetic-response-secret")
        return {"status": "ok"}

    app.add_api_route("/items", get_items, methods=["GET"])
    instrument_fastapi_app(app, service_name="tracecat-api")

    with TestClient(app) as client:
        response = client.get(
            "/items",
            headers={
                "Authorization": "Bearer synthetic-request-secret",
                "Cookie": "session=synthetic-request-secret",
            },
        )

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes is not None
    span_attributes = str(spans[0].attributes).lower()
    assert "authorization" not in span_attributes
    assert "cookie" not in span_attributes
    assert "synthetic-request-secret" not in span_attributes
    assert "synthetic-response-secret" not in span_attributes


@pytest.mark.parametrize("path", ["/", "/health", "/ready", "/readiness", "/metrics"])
def test_fastapi_probe_routes_are_excluded(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    exporter = InMemorySpanExporter()
    initialize_platform_tracing("tracecat-api", exporter=exporter)
    app = FastAPI()
    app.add_api_route(
        "/{probe_path:path}",
        lambda probe_path: {"path": probe_path},
    )

    instrument_fastapi_app(app, service_name="tracecat-api")

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert TRACE_ID_HEADER not in response.headers
    assert TRACE_SAMPLED_HEADER not in response.headers
    assert exporter.get_finished_spans() == ()


def test_exporter_exception_does_not_change_fastapi_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    initialize_platform_tracing("tracecat-api", exporter=RaisingSpanExporter())
    app = FastAPI()
    app.add_api_route("/items", lambda: {"status": "ok"})

    instrument_fastapi_app(app, service_name="tracecat-api")

    with TestClient(app) as client:
        response = client.get("/items")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers[TRACE_ID_HEADER]) == 32


@dataclass(frozen=True, slots=True)
class _HeaderlessSpanParams:
    """Workflow command span input as the SDK hands it to the worker side."""

    name: str
    attributes: dict[str, str]
    kind: SpanKind = SpanKind.INTERNAL
    context: dict[str, str] = field(default_factory=dict)
    time_ns: int = 1
    link_context: None = None
    exception: Exception | None = None
    parent_missing: bool = True


def _headerless_interceptor() -> platform_otel._TraceContextOnlyTracingInterceptor:
    interceptor = temporal_tracing_interceptor()
    assert isinstance(interceptor, platform_otel._TraceContextOnlyTracingInterceptor)
    return interceptor


def _complete_headerless_span(
    interceptor: platform_otel._TraceContextOnlyTracingInterceptor,
    name: str,
    run_id: str,
) -> str:
    carrier = interceptor._completed_workflow_span(
        _HeaderlessSpanParams(
            name=name,
            attributes={"temporalWorkflowID": "synthetic-wf", "temporalRunID": run_id},
        )
    )
    assert carrier is not None
    traceparent = carrier["traceparent"]
    assert isinstance(traceparent, str)
    return traceparent


def test_headerless_workflow_run_spans_share_one_deterministic_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Temporal-started run has no trace header, yet forms a single trace.

    Every command span of the run hangs off the same never-exported parent that
    is derived from the run ID, so the trace stays intact across replays, and a
    different run gets a different trace.
    """
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    exporter = InMemorySpanExporter()
    initialize_platform_tracing("tracecat-worker", exporter=exporter)
    interceptor = _headerless_interceptor()

    for name in (
        "RunWorkflow:Traced",
        "StartActivity:probe",
        "CompleteWorkflow:Traced",
    ):
        _complete_headerless_span(interceptor, name, "run-1")
    _complete_headerless_span(interceptor, "RunWorkflow:Traced", "run-2")

    spans_by_run: dict[str, list[Any]] = {}
    for span in exporter.get_finished_spans():
        assert span.attributes is not None
        run_id = span.attributes["temporalRunID"]
        assert isinstance(run_id, str)
        spans_by_run.setdefault(run_id, []).append(span)
    run_1 = spans_by_run["run-1"]
    assert len(run_1) == 3
    trace_ids = {span.context.trace_id for span in run_1}
    assert len(trace_ids) == 1
    parent_ids = {span.parent.span_id for span in run_1 if span.parent is not None}
    assert len(parent_ids) == 1
    assert parent_ids != {0}
    # The synthetic parent is never exported.
    assert parent_ids.isdisjoint({span.context.span_id for span in run_1})
    assert spans_by_run["run-2"][0].context.trace_id not in trace_ids

    # Replaying the run reproduces the same trace.
    replayed = _complete_headerless_span(interceptor, "RunWorkflow:Traced", "run-1")
    assert format(next(iter(trace_ids)), "032x") in replayed


def test_headerless_workflow_run_follows_the_configured_sampler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sampler decides once per run, so an unsampled run exports nothing."""
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    exporter = InMemorySpanExporter()
    initialize_platform_tracing("tracecat-worker", exporter=exporter)
    interceptor = _headerless_interceptor()

    traceparent = _complete_headerless_span(interceptor, "RunWorkflow:Traced", "run-1")
    _complete_headerless_span(interceptor, "StartActivity:probe", "run-1")

    assert traceparent.endswith("-00")
    assert exporter.get_finished_spans() == ()
