"""Agent turn and LLM proxy metrics shared by every agent backend.

Every series is labelled by ``backend`` (``{backend_id}/{harness}``), so one
dashboard compares backends. Labels stay low-cardinality: never session, turn,
run, or workspace identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, Meter

from tracecat.agent.common.stream_types import StreamEventType
from tracecat.agent.llm_routing import KNOWN_MODEL_PROVIDERS
from tracecat.agent.session.types import AgentSessionEntity

UNKNOWN_LABEL: Final = "unknown"
OTHER_PROVIDER_LABEL: Final = "other"

FIRST_TOKEN_EVENT_TYPES: Final = frozenset(
    {
        StreamEventType.TEXT_DELTA,
        StreamEventType.THINKING_DELTA,
        StreamEventType.TOOL_CALL_START,
    }
)
"""Stream events that count as a turn's first model output."""

type LLMRoute = Literal["managed", "direct"]

# The SDK's default buckets stop at 10 s; agent turns routinely run for minutes.
_FIRST_TOKEN_BUCKETS_MS: Final = (
    50,
    100,
    250,
    500,
    750,
    1_000,
    1_500,
    2_000,
    3_000,
    5_000,
    7_500,
    10_000,
    15_000,
    20_000,
    30_000,
    60_000,
    120_000,
)
_DURATION_BUCKETS_MS: Final = (
    500,
    1_000,
    2_000,
    3_000,
    5_000,
    7_500,
    10_000,
    15_000,
    20_000,
    30_000,
    45_000,
    60_000,
    90_000,
    120_000,
    180_000,
    300_000,
    600_000,
    1_200_000,
    1_800_000,
    3_600_000,
)


class TurnOutcome(StrEnum):
    """How a turn, or a turn segment ending at an approval pause, settled."""

    COMPLETED = "completed"
    MODEL_ERROR = "model_error"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    UNSETTLED = "unsettled"


class TokenType(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    CACHE_READ = "cache_read"
    CACHE_WRITE = "cache_write"


@dataclass(frozen=True, slots=True)
class TurnTokens:
    """Model tokens one turn used. ``input`` excludes cache reads and writes."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass(frozen=True, slots=True)
class _Instruments:
    llm_ttft_ms: Histogram
    turn_first_token_ms: Histogram
    turn_duration_ms: Histogram
    turn_outcomes: Counter
    turn_tokens: Counter

    @classmethod
    def create(cls, meter: Meter) -> _Instruments:
        return cls(
            llm_ttft_ms=meter.create_histogram(
                "agent.llm_proxy.ttft_ms",
                unit="ms",
                description=(
                    "Time from the LLM proxy receiving a streamed request to "
                    "its first upstream chunk."
                ),
                explicit_bucket_boundaries_advisory=_FIRST_TOKEN_BUCKETS_MS,
            ),
            turn_first_token_ms=meter.create_histogram(
                "agent.turn.first_token_ms",
                unit="ms",
                description="Time from sending a turn to its first model output.",
                explicit_bucket_boundaries_advisory=_FIRST_TOKEN_BUCKETS_MS,
            ),
            turn_duration_ms=meter.create_histogram(
                "agent.turn.duration_ms",
                unit="ms",
                description="Time from sending a turn to its outcome.",
                explicit_bucket_boundaries_advisory=_DURATION_BUCKETS_MS,
            ),
            turn_outcomes=meter.create_counter(
                "agent.turn.outcomes",
                description="Agent turns by outcome.",
            ),
            turn_tokens=meter.create_counter(
                "agent.turn.tokens",
                unit="{token}",
                description="Model tokens used by agent turns, by token type.",
            ),
        )


_instruments = _Instruments.create(metrics.get_meter("tracecat.agent"))


def backend_label(backend_id: str, harness: str) -> str:
    """Build the ``backend`` label from a backend identifier and its harness."""
    return f"{backend_id}/{harness}"


def _provider_label(model_provider: str | None) -> str:
    if not model_provider:
        return UNKNOWN_LABEL
    if model_provider in KNOWN_MODEL_PROVIDERS:
        return model_provider
    return OTHER_PROVIDER_LABEL


def _elapsed_ms(start: datetime, end: datetime) -> float:
    # Send and settle times can come from different hosts; clamp clock skew.
    return max((end - start).total_seconds() * 1000, 0.0)


def record_llm_ttft(*, backend: str, route: LLMRoute, ttft_ms: float) -> None:
    """Record time to first chunk for one streamed upstream LLM request."""
    _instruments.llm_ttft_ms.record(ttft_ms, {"backend": backend, "route": route})


def record_turn(
    *,
    backend: str,
    surface: AgentSessionEntity | None,
    model_provider: str | None,
    outcome: TurnOutcome,
    sent_at: datetime,
    first_token_at: datetime | None,
    settled_at: datetime,
    tokens: TurnTokens | None,
) -> None:
    """Record one turn's latency, outcome, and token use.

    Args:
        backend: Label from ``backend_label``.
        surface: Entity the session belongs to; ``None`` is labelled unknown.
        model_provider: Configured provider slug. Unknown slugs are labelled
            ``other`` so custom provider names never become label values.
        outcome: How the turn settled.
        sent_at: When the user sent the turn, or resumed it after approvals.
        first_token_at: When the first model output streamed, if any did.
        settled_at: When the turn reached ``outcome``.
        tokens: Token use reported by the runtime, if any.
    """
    labels = {
        "backend": backend,
        "surface": surface.value if surface is not None else UNKNOWN_LABEL,
        "provider": _provider_label(model_provider),
    }
    outcome_labels = {**labels, "outcome": outcome.value}
    _instruments.turn_outcomes.add(1, outcome_labels)
    _instruments.turn_duration_ms.record(
        _elapsed_ms(sent_at, settled_at), outcome_labels
    )
    if first_token_at is not None:
        _instruments.turn_first_token_ms.record(
            _elapsed_ms(sent_at, first_token_at), labels
        )
    if tokens is None:
        return
    counts = {
        TokenType.INPUT: tokens.input,
        TokenType.OUTPUT: tokens.output,
        TokenType.CACHE_READ: tokens.cache_read,
        TokenType.CACHE_WRITE: tokens.cache_write,
    }
    for token_type, count in counts.items():
        if count > 0:
            _instruments.turn_tokens.add(
                count, {**labels, "token.type": token_type.value}
            )
