"""Platform-owned OpenTelemetry tracing.

This module intentionally does not read or reuse tenant-configurable agent OTel
settings. Platform services export to the operator-controlled OTLP endpoint.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Final, Protocol

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Link, Span, SpanKind, Status, StatusCode, TraceFlags
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import Attributes
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from temporalio.api.common.v1 import Payload
from temporalio.contrib.opentelemetry import (
    TracingInterceptor,
    TracingWorkflowInboundInterceptor,
)
from temporalio.exceptions import ApplicationError, ApplicationErrorCategory
from temporalio.worker import WorkflowInboundInterceptor, WorkflowInterceptorClassInput

from tracecat import __version__, config

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

TRACE_ID_HEADER: Final = "X-Trace-ID"
TRACE_SAMPLED_HEADER: Final = "X-Trace-Sampled"

_EXCLUDED_FASTAPI_URLS: Final = ",".join(
    (
        r"^https?://[^/]+/?$",
        r".*/health(?:/.*)?$",
        r".*/metrics$",
        r".*/ready$",
        r".*/readiness$",
    )
)

# OpenTelemetry treats an empty capture list as permission to fall back to
# OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_* environment variables.
# A non-empty never-match pattern keeps platform tracing's no-header contract
# intact even when those variables are set globally by an operator.
_NEVER_CAPTURE_HTTP_HEADER: Final = r"(?!)"
_REDACTED_HTTP_PATH: Final = "/[REDACTED]"
_TRACE_CONTEXT_PROPAGATOR: Final = TraceContextTextMapPropagator()
_TEMPORAL_FAILURE_TYPE: Final = "temporal.failure"

type _TraceCarrier = dict[str, list[str] | str]


class _TemporalInputWithHeaders(Protocol):
    headers: Mapping[str, Payload]


class _CompletedWorkflowSpanInput(Protocol):
    @property
    def context(self) -> Mapping[str, list[str] | str]: ...

    @property
    def name(self) -> str: ...

    @property
    def attributes(self) -> Attributes: ...

    @property
    def time_ns(self) -> int: ...

    @property
    def link_context(self) -> Mapping[str, list[str] | str] | None: ...

    @property
    def exception(self) -> Exception | None: ...

    @property
    def kind(self) -> SpanKind: ...

    @property
    def parent_missing(self) -> bool: ...


@dataclass(frozen=True)
class PlatformTracing:
    """Process-level platform tracing runtime."""

    service_name: str
    tracer_provider: TracerProvider

    def tracer(self, instrumentation_name: str) -> trace.Tracer:
        """Return a tracer backed by this process's provider."""
        return self.tracer_provider.get_tracer(
            instrumentation_name,
            schema_url=None,
        )


_runtime: PlatformTracing | None = None
_runtime_lock = Lock()


def initialize_platform_tracing(
    service_name: str,
    *,
    exporter: SpanExporter | None = None,
) -> PlatformTracing | None:
    """Initialize disabled-by-default platform tracing for one process.

    Passing an exporter is intended for tests and uses synchronous exporting so
    assertions do not depend on background batch timing.
    """
    global _runtime

    if not config.TRACECAT__PLATFORM_OTEL_ENABLED:
        return None

    with _runtime_lock:
        if _runtime is not None:
            if _runtime.service_name != service_name:
                logger.warning(
                    "Platform tracing already initialized for %s; ignoring %s",
                    _runtime.service_name,
                    service_name,
                )
            return _runtime

        provider: TracerProvider | None = None
        try:
            provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": service_name,
                        "service.namespace": "tracecat",
                        "service.version": __version__,
                        "deployment.environment.name": config.TRACECAT__APP_ENV,
                    }
                )
            )
            if exporter is None:
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            else:
                provider.add_span_processor(SimpleSpanProcessor(exporter))

            # Baggage is deliberately excluded from the platform propagation
            # contract so arbitrary caller-provided values do not cross service
            # and Temporal boundaries.
            set_global_textmap(_TRACE_CONTEXT_PROPAGATOR)
            _runtime = PlatformTracing(
                service_name=service_name,
                tracer_provider=provider,
            )
        except Exception:
            logger.exception(
                "Platform tracing initialization failed; continuing without tracing"
            )
            if provider is not None:
                provider.shutdown()
            return None

        logger.info("Platform tracing initialized for %s", service_name)
        return _runtime


def get_platform_tracing() -> PlatformTracing | None:
    """Return the initialized process tracing runtime, if any."""
    return _runtime


def shutdown_platform_tracing(*, timeout_millis: int = 5_000) -> None:
    """Flush and shut down tracing without affecting process shutdown."""
    global _runtime

    with _runtime_lock:
        runtime, _runtime = _runtime, None

    if runtime is None:
        return

    try:
        runtime.tracer_provider.force_flush(timeout_millis=timeout_millis)
    except Exception:
        logger.exception("Failed to flush platform traces during shutdown")
    try:
        runtime.tracer_provider.shutdown()
    except Exception:
        logger.exception("Failed to shut down platform tracing")


def temporal_tracing_interceptor() -> TracingInterceptor | None:
    """Create the Temporal interceptor backed by the platform provider."""
    runtime = get_platform_tracing()
    if runtime is None:
        return None
    return _TraceContextOnlyTracingInterceptor(
        tracer=runtime.tracer("tracecat.temporal"),
        always_create_workflow_spans=False,
    )


class _TraceContextOnlyWorkflowTracingInterceptor(TracingWorkflowInboundInterceptor):
    """Extract and inject trace context without persisting OTel baggage."""

    def __init__(self, next_interceptor: WorkflowInboundInterceptor) -> None:
        super().__init__(next_interceptor)
        self.text_map_propagator = _TRACE_CONTEXT_PROPAGATOR


class _TraceContextOnlyTracingInterceptor(TracingInterceptor):
    """Temporal tracing with a trace-only carrier and sanitized failures."""

    def __init__(
        self,
        *,
        tracer: trace.Tracer,
        always_create_workflow_spans: bool,
    ) -> None:
        super().__init__(
            tracer=tracer,
            always_create_workflow_spans=always_create_workflow_spans,
        )
        self.text_map_propagator = _TRACE_CONTEXT_PROPAGATOR

    @contextmanager
    def _start_as_current_span(
        self,
        name: str,
        *,
        attributes: Attributes,
        input: _TemporalInputWithHeaders | None = None,
        kind: SpanKind,
        context: otel_context.Context | None = None,
    ) -> Iterator[None]:
        """Create a Temporal span without exporting exception content."""
        token = otel_context.attach(context) if context else None
        try:
            with self.tracer.start_as_current_span(
                name,
                attributes=attributes,
                kind=kind,
                context=context,
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                if input:
                    input.headers = self._context_to_headers(input.headers)
                try:
                    yield None
                except Exception as exc:
                    if (
                        not isinstance(exc, ApplicationError)
                        or exc.category != ApplicationErrorCategory.BENIGN
                    ):
                        span.set_status(Status(status_code=StatusCode.ERROR))
                        span.set_attribute("error.type", _TEMPORAL_FAILURE_TYPE)
                    raise
        finally:
            if token and context is otel_context.get_current():
                otel_context.detach(token)

    def _completed_workflow_span(
        self, params: _CompletedWorkflowSpanInput
    ) -> _TraceCarrier | None:
        """Export workflow failures without messages or stack traces."""
        if params.parent_missing and not self._always_create_workflow_spans:
            return None

        span_context = self.text_map_propagator.extract(params.context)
        links: Sequence[Link] = []
        if params.link_context:
            link_span = trace.get_current_span(
                self.text_map_propagator.extract(params.link_context)
            )
            if link_span is not trace.INVALID_SPAN:
                links = [Link(link_span.get_span_context())]

        span = self.tracer.start_span(
            params.name,
            span_context,
            attributes=params.attributes,
            links=links,
            start_time=params.time_ns,
            kind=params.kind,
        )
        span_context = trace.set_span_in_context(span, span_context)
        if params.exception:
            span.set_status(Status(status_code=StatusCode.ERROR))
            span.set_attribute("error.type", _TEMPORAL_FAILURE_TYPE)
        span.end(end_time=params.time_ns)

        carrier: _TraceCarrier = {}
        self.text_map_propagator.inject(carrier, span_context)
        return carrier

    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[TracingWorkflowInboundInterceptor]:
        super().workflow_interceptor_class(input)
        return _TraceContextOnlyWorkflowTracingInterceptor


def set_current_span_attributes(attributes: dict[str, str | int | bool | None]) -> None:
    """Attach safe platform attributes to the current recording span."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)


def _sanitize_server_span(span: Span, scope: Scope) -> None:
    """Replace raw URLs with a credential-safe route template."""
    if not span.is_recording():
        return
    route_path = getattr(scope.get("route"), "path", None)
    path = route_path if isinstance(route_path, str) else _REDACTED_HTTP_PATH
    span.set_attribute("http.target", path)
    span.set_attribute("http.url", path)
    span.set_attribute("url.full", path)
    span.set_attribute("url.path", path)
    if scope.get("query_string"):
        span.set_attribute("url.query", "[REDACTED]")


class TraceResponseHeadersMiddleware:
    """Expose the active server trace identifier on HTTP responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_trace_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                span = trace.get_current_span()
                # FastAPI resolves the route before starting the response, so
                # this is the first safe point to replace the request hook's
                # redacted placeholder with the parameterized route template.
                _sanitize_server_span(span, scope)
                span_context = span.get_span_context()
                if span_context.is_valid:
                    headers = MutableHeaders(scope=message)
                    headers[TRACE_ID_HEADER] = f"{span_context.trace_id:032x}"
                    sampled = bool(span_context.trace_flags & TraceFlags.SAMPLED)
                    headers[TRACE_SAMPLED_HEADER] = str(sampled).lower()
            await send(message)

        await self.app(scope, receive, send_with_trace_headers)


def instrument_fastapi_app(
    app: FastAPI, *, service_name: str
) -> PlatformTracing | None:
    """Initialize and instrument a FastAPI application when tracing is enabled."""
    runtime = initialize_platform_tracing(service_name)
    if runtime is None:
        return None

    app.add_middleware(TraceResponseHeadersMiddleware)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=runtime.tracer_provider,
        excluded_urls=_EXCLUDED_FASTAPI_URLS,
        server_request_hook=_sanitize_server_span,
        http_capture_headers_server_request=[_NEVER_CAPTURE_HTTP_HEADER],
        http_capture_headers_server_response=[_NEVER_CAPTURE_HTTP_HEADER],
        exclude_spans=["receive", "send"],
    )
    return runtime
