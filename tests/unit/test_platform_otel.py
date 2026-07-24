from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from temporalio.contrib.opentelemetry import TracingInterceptor

from tracecat import config
from tracecat.logger._logger import _add_trace_context
from tracecat.observability.otel import (
    TRACE_ID_HEADER,
    TRACE_SAMPLED_HEADER,
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


def test_active_trace_context_is_added_to_log_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
    runtime = initialize_platform_tracing(
        "tracecat-api", exporter=InMemorySpanExporter()
    )
    assert runtime is not None
    record: Any = {"extra": {}}

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
    assert "do-not-export" not in str(span.attributes)
    assert "authorization" not in str(span.attributes).lower()
    assert "cookie" not in str(span.attributes).lower()


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
