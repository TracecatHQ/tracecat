from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ExponentialHistogramDataPoint,
    HistogramDataPoint,
    InMemoryMetricReader,
    NumberDataPoint,
)

from tracecat.agent import metrics as agent_metrics
from tracecat.agent.error_policy import (
    agent_executor_timed_out,
    agent_executor_unclassified,
    agent_llm_rate_limited,
)
from tracecat.agent.executor.activity import (
    AgentExecutorResult,
    _turn_outcome,
    _turn_tokens,
)
from tracecat.agent.metrics import (
    MAX_TURN_TOKENS,
    TurnOutcome,
    TurnTokens,
    backend_label,
    record_llm_ttft,
    record_turn,
)
from tracecat.agent.session.types import AgentSessionEntity

SENT_AT = datetime(2026, 1, 1, tzinfo=UTC)
BACKEND = backend_label("oss", "claude_code")


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    monkeypatch.setattr(
        agent_metrics, "_instruments", agent_metrics._Instruments.create(meter)
    )
    return reader


def _points(
    reader: InMemoryMetricReader, name: str
) -> list[NumberDataPoint | HistogramDataPoint | ExponentialHistogramDataPoint]:
    data = reader.get_metrics_data()
    assert data is not None
    return [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def test_backend_label_joins_backend_and_harness() -> None:
    assert BACKEND == "oss/claude_code"


def test_record_turn_emits_latency_outcome_and_tokens(
    reader: InMemoryMetricReader,
) -> None:
    record_turn(
        backend=BACKEND,
        surface=AgentSessionEntity.CASE,
        model_provider="anthropic",
        outcome=TurnOutcome.COMPLETED,
        sent_at=SENT_AT,
        first_token_at=SENT_AT + timedelta(milliseconds=800),
        settled_at=SENT_AT + timedelta(seconds=3),
        tokens=TurnTokens(
            input=10, output=20, cache_read=300, cache_write=MAX_TURN_TOKENS + 1
        ),
    )

    labels = {"backend": BACKEND, "surface": "case", "provider": "anthropic"}
    outcome_labels = {**labels, "outcome": "completed"}

    [outcome] = _points(reader, "agent.turn.outcomes")
    assert isinstance(outcome, NumberDataPoint)
    assert outcome.value == 1
    assert dict(outcome.attributes or {}) == outcome_labels

    [duration] = _points(reader, "agent.turn.duration_ms")
    assert isinstance(duration, HistogramDataPoint)
    assert duration.sum == pytest.approx(3000)
    assert dict(duration.attributes or {}) == outcome_labels

    [first_token] = _points(reader, "agent.turn.first_token_ms")
    assert isinstance(first_token, HistogramDataPoint)
    assert first_token.sum == pytest.approx(800)
    assert dict(first_token.attributes or {}) == labels

    tokens = {
        dict(point.attributes or {})["token.type"]: point.value
        for point in _points(reader, "agent.turn.tokens")
        if isinstance(point, NumberDataPoint)
    }
    # Zero and out-of-range counts add nothing.
    assert tokens == {"input": 10, "output": 20, "cache_read": 300}


def test_record_turn_bounds_labels_and_clamps_skew(
    reader: InMemoryMetricReader,
) -> None:
    """Custom provider names and missing surfaces never become label values."""
    record_turn(
        backend=BACKEND,
        surface=None,
        model_provider="my-company-gateway",
        outcome=TurnOutcome.MODEL_ERROR,
        sent_at=SENT_AT,
        first_token_at=None,
        settled_at=SENT_AT - timedelta(milliseconds=5),
        tokens=None,
    )

    [duration] = _points(reader, "agent.turn.duration_ms")
    assert isinstance(duration, HistogramDataPoint)
    assert duration.sum == 0
    assert dict(duration.attributes or {}) == {
        "backend": BACKEND,
        "surface": "unknown",
        "provider": "other",
        "outcome": "model_error",
    }
    assert _points(reader, "agent.turn.first_token_ms") == []
    assert _points(reader, "agent.turn.tokens") == []


def test_record_llm_ttft_labels_backend_and_route(
    reader: InMemoryMetricReader,
) -> None:
    record_llm_ttft(backend=BACKEND, route="direct", ttft_ms=42.0)

    [point] = _points(reader, "agent.llm_proxy.ttft_ms")
    assert isinstance(point, HistogramDataPoint)
    assert point.sum == pytest.approx(42.0)
    assert dict(point.attributes or {}) == {"backend": BACKEND, "route": "direct"}


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (AgentExecutorResult(success=True), TurnOutcome.COMPLETED),
        (
            AgentExecutorResult(success=True, approval_requested=True),
            TurnOutcome.BLOCKED,
        ),
        (AgentExecutorResult(success=False, cancelled=True), TurnOutcome.CANCELLED),
        (
            AgentExecutorResult(success=True, approval_requested=True, cancelled=True),
            TurnOutcome.CANCELLED,
        ),
        (
            AgentExecutorResult(
                success=False,
                classification=agent_llm_rate_limited(route_is_direct=False),
            ),
            TurnOutcome.MODEL_ERROR,
        ),
        (
            AgentExecutorResult(
                success=False, classification=agent_executor_timed_out()
            ),
            TurnOutcome.UNSETTLED,
        ),
        (
            AgentExecutorResult(
                success=False, classification=agent_executor_unclassified()
            ),
            TurnOutcome.FAILED,
        ),
        (AgentExecutorResult(success=False), TurnOutcome.FAILED),
    ],
)
def test_claude_code_turn_outcome(
    result: AgentExecutorResult, expected: TurnOutcome
) -> None:
    assert _turn_outcome(result) == expected


def test_claude_code_turn_tokens_read_result_usage() -> None:
    usage = {
        "input_tokens": 12,
        "output_tokens": 34,
        "cache_read_input_tokens": 560,
        "cache_creation_input_tokens": 78,
        "server_tool_use": {"web_search_requests": 1},
    }

    assert _turn_tokens(usage) == TurnTokens(
        input=12, output=34, cache_read=560, cache_write=78
    )
    assert _turn_tokens({"input_tokens": "12"}) == TurnTokens()
    assert _turn_tokens(None) is None
