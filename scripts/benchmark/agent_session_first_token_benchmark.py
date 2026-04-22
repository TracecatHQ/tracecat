#!/usr/bin/env python
"""Benchmark real agent-session init-to-first-token latency.

This script drives the Tracecat app API rather than the LLM gateway
directly:

1. Create a fresh agent session
2. Send one streaming message to that session
3. Measure from the message request start to first streamed assistant text

It is intended to measure the concurrency limit of a single agent-executor
worker from real execution startup through first token.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import orjson

DEFAULT_BASE_URL = "http://localhost:80"
DEFAULT_PROMPT = "Reply with exactly 'benchmark-ok'."
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_MODEL_PROVIDER = "openai"
DEFAULT_ENTITY_TYPE = "agent_preset"
DEFAULT_RUNS_PER_LEVEL = 3
DEFAULT_CONNECT_TIMEOUT = 20.0
DEFAULT_READ_TIMEOUT = 300.0
DEFAULT_WRITE_TIMEOUT = 30.0
DEFAULT_POOL_TIMEOUT = 10.0
DEFAULT_COOKIE_ENV_CANDIDATES = (
    "TRACECAT_BENCHMARK_COOKIE_HEADER",
    "TRACECAT_COOKIE_HEADER",
)


@dataclass(slots=True)
class BenchmarkConfig:
    base_url: str
    cookie_header: str
    workspace_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    model: str
    model_provider: str
    prompt: str
    concurrency_sweep: tuple[int, ...]
    runs_per_level: int
    connect_timeout: float
    read_timeout: float
    write_timeout: float
    pool_timeout: float
    output_json: Path | None


@dataclass(slots=True)
class RunSample:
    concurrency: int
    round_index: int
    slot_index: int
    session_id: str | None
    status: str
    http_status: int | None
    error: str | None
    create_session_ms: float | None
    message_headers_ms: float | None
    first_sse_ms: float | None
    first_assistant_text_ms: float | None
    total_stream_ms: float | None
    sse_payload_count: int


@dataclass(slots=True)
class ConcurrencySummary:
    concurrency: int
    total_runs: int
    status_counts: dict[str, int]
    http_status_counts: dict[str, int]
    create_session_ms: dict[str, float | None]
    message_headers_ms: dict[str, float | None]
    first_sse_ms: dict[str, float | None]
    first_assistant_text_ms: dict[str, float | None]
    total_stream_ms: dict[str, float | None]


def parse_args(argv: list[str]) -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="Benchmark real agent-session init-to-first-token latency.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TRACECAT_BENCHMARK_BASE_URL", DEFAULT_BASE_URL),
        help="Tracecat base URL, e.g. http://localhost:80",
    )
    parser.add_argument(
        "--cookie-env",
        default=None,
        help="Environment variable that contains the full Cookie header value.",
    )
    parser.add_argument(
        "--workspace-id",
        required=True,
        type=uuid.UUID,
        help="Workspace ID query parameter required by the agent sessions API.",
    )
    parser.add_argument(
        "--entity-type",
        default=DEFAULT_ENTITY_TYPE,
        help="Session entity type. Defaults to 'agent_preset'.",
    )
    parser.add_argument(
        "--entity-id",
        required=True,
        type=uuid.UUID,
        help="Entity ID for the new benchmark sessions.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model field sent in the Vercel chat request.",
    )
    parser.add_argument(
        "--model-provider",
        default=DEFAULT_MODEL_PROVIDER,
        help="Model provider field sent in the Vercel chat request.",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Benchmark prompt. Defaults to a short deterministic prompt.",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="Optional file containing the prompt to send.",
    )
    parser.add_argument(
        "--concurrency-sweep",
        default="1,2,3,4,5,6",
        help="Comma-separated concurrency levels to test.",
    )
    parser.add_argument(
        "--runs-per-level",
        type=int,
        default=DEFAULT_RUNS_PER_LEVEL,
        help="Number of rounds to execute per concurrency level.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT,
        help="httpx connect timeout in seconds.",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=DEFAULT_READ_TIMEOUT,
        help="httpx read timeout in seconds.",
    )
    parser.add_argument(
        "--write-timeout",
        type=float,
        default=DEFAULT_WRITE_TIMEOUT,
        help="httpx write timeout in seconds.",
    )
    parser.add_argument(
        "--pool-timeout",
        type=float,
        default=DEFAULT_POOL_TIMEOUT,
        help="httpx pool timeout in seconds.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to write the raw sample and summary output as JSON.",
    )

    args = parser.parse_args(argv)
    if args.runs_per_level <= 0:
        raise SystemExit("--runs-per-level must be greater than 0")

    return BenchmarkConfig(
        base_url=args.base_url.rstrip("/"),
        cookie_header=resolve_cookie_header(args.cookie_env),
        workspace_id=args.workspace_id,
        entity_type=args.entity_type,
        entity_id=args.entity_id,
        model=args.model,
        model_provider=args.model_provider,
        prompt=resolve_prompt(prompt=args.prompt, prompt_file=args.prompt_file),
        concurrency_sweep=parse_concurrency_sweep(args.concurrency_sweep),
        runs_per_level=args.runs_per_level,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        write_timeout=args.write_timeout,
        pool_timeout=args.pool_timeout,
        output_json=args.output_json,
    )


def resolve_cookie_header(cookie_env: str | None) -> str:
    """Resolve the full Cookie header value from env."""
    candidates = (cookie_env,) if cookie_env else DEFAULT_COOKIE_ENV_CANDIDATES
    for env_name in candidates:
        if not env_name:
            continue
        if cookie_header := os.environ.get(env_name):
            return cookie_header
    candidate_list = ", ".join(candidates)
    raise SystemExit(
        f"Missing cookie header. Set one of: {candidate_list}, or pass --cookie-env."
    )


def resolve_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    """Resolve prompt text from CLI or file input."""
    if prompt is not None and prompt_file is not None:
        raise SystemExit("Provide either --prompt or --prompt-file, not both.")
    if prompt_file is not None:
        return prompt_file.read_text(encoding="utf-8").strip()
    if prompt is not None:
        return prompt
    return DEFAULT_PROMPT


def parse_concurrency_sweep(value: str) -> tuple[int, ...]:
    """Parse a comma-separated concurrency sweep string."""
    try:
        levels = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise SystemExit(
            f"Invalid concurrency sweep {value!r}: expected comma-separated integers."
        ) from exc
    if not levels or any(level <= 0 for level in levels):
        raise SystemExit(
            f"Invalid concurrency sweep {value!r}: all levels must be positive integers."
        )
    return levels


def build_session_payload(config: BenchmarkConfig) -> dict[str, Any]:
    """Build the request body for POST /api/agent/sessions."""
    return {
        "title": "Benchmark Session",
        "entity_type": config.entity_type,
        "entity_id": str(config.entity_id),
    }


def build_message_payload(
    config: BenchmarkConfig, session_id: uuid.UUID
) -> dict[str, Any]:
    """Build a minimal Vercel chat request body."""
    return {
        "kind": "vercel",
        "message": {
            "id": str(session_id),
            "role": "user",
            "parts": [{"type": "text", "text": config.prompt}],
        },
        "model": config.model,
        "model_provider": config.model_provider,
    }


def build_headers(config: BenchmarkConfig) -> dict[str, str]:
    """Build common HTTP headers for session API requests."""
    return {
        "Cookie": config.cookie_header,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


def maybe_parse_sse_payload(line: str) -> dict[str, Any] | None:
    """Parse a Vercel JSON SSE payload from a single streamed line."""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        loaded = orjson.loads(data)
    except orjson.JSONDecodeError:
        return None
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None


def is_assistant_text_payload(payload: dict[str, Any]) -> bool:
    """Return True when the payload carries assistant text."""
    return payload.get("type") == "text-delta" and isinstance(payload.get("delta"), str)


def classify_httpx_exception(exc: Exception) -> str:
    """Map httpx exceptions into explicit benchmark status buckets."""
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, httpx.ReadError):
        return "read_error"
    if isinstance(exc, httpx.WriteError):
        return "write_error"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return "http_error"
    return exc.__class__.__name__.lower()


def percentile(values: list[float], q: float) -> float | None:
    """Compute a percentile by linear interpolation."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    position = (len(sorted_values) - 1) * q
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    fraction = position - lower_index
    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    return lower_value + (upper_value - lower_value) * fraction


def summarize_latency(values: list[float | None]) -> dict[str, float | None]:
    """Aggregate latency stats for a single metric."""
    clean_values = [value for value in values if value is not None]
    if not clean_values:
        return {
            "count": 0,
            "min": None,
            "p50": None,
            "p95": None,
            "p99": None,
            "max": None,
            "avg": None,
        }
    return {
        "count": float(len(clean_values)),
        "min": min(clean_values),
        "p50": percentile(clean_values, 0.50),
        "p95": percentile(clean_values, 0.95),
        "p99": percentile(clean_values, 0.99),
        "max": max(clean_values),
        "avg": sum(clean_values) / len(clean_values),
    }


def summarize_samples(samples: list[RunSample]) -> list[ConcurrencySummary]:
    """Group samples by concurrency level and compute aggregate summaries."""
    by_concurrency: dict[int, list[RunSample]] = {}
    for sample in samples:
        by_concurrency.setdefault(sample.concurrency, []).append(sample)

    summaries: list[ConcurrencySummary] = []
    for concurrency in sorted(by_concurrency):
        grouped = by_concurrency[concurrency]
        summaries.append(
            ConcurrencySummary(
                concurrency=concurrency,
                total_runs=len(grouped),
                status_counts=dict(Counter(sample.status for sample in grouped)),
                http_status_counts=dict(
                    Counter(
                        str(sample.http_status)
                        for sample in grouped
                        if sample.http_status is not None
                    )
                ),
                create_session_ms=summarize_latency(
                    [sample.create_session_ms for sample in grouped]
                ),
                message_headers_ms=summarize_latency(
                    [sample.message_headers_ms for sample in grouped]
                ),
                first_sse_ms=summarize_latency(
                    [sample.first_sse_ms for sample in grouped]
                ),
                first_assistant_text_ms=summarize_latency(
                    [sample.first_assistant_text_ms for sample in grouped]
                ),
                total_stream_ms=summarize_latency(
                    [sample.total_stream_ms for sample in grouped]
                ),
            )
        )
    return summaries


async def create_session(
    client: httpx.AsyncClient,
    config: BenchmarkConfig,
) -> tuple[uuid.UUID, float]:
    """Create a fresh benchmark session and return its ID and latency."""
    start_time = time.perf_counter()
    response = await client.post(
        f"{config.base_url}/api/agent/sessions",
        params={"workspace_id": str(config.workspace_id)},
        headers=build_headers(config),
        json=build_session_payload(config),
    )
    duration_ms = (time.perf_counter() - start_time) * 1000
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    return uuid.UUID(str(payload["id"])), duration_ms


async def run_session_sample(
    client: httpx.AsyncClient,
    config: BenchmarkConfig,
    concurrency: int,
    round_index: int,
    slot_index: int,
) -> RunSample:
    """Run one full session turn and measure init-to-first-token latency."""
    session_id: uuid.UUID | None = None
    create_session_ms: float | None = None
    message_headers_ms: float | None = None
    first_sse_ms: float | None = None
    first_assistant_text_ms: float | None = None
    sse_payload_count = 0

    try:
        session_id, create_session_ms = await create_session(client, config)
        start_time = time.perf_counter()
        async with client.stream(
            "POST",
            f"{config.base_url}/api/agent/sessions/{session_id}/messages",
            params={"workspace_id": str(config.workspace_id)},
            headers=build_headers(config),
            json=build_message_payload(config, session_id),
        ) as response:
            message_headers_ms = (time.perf_counter() - start_time) * 1000
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                total_stream_ms = (time.perf_counter() - start_time) * 1000
                return RunSample(
                    concurrency=concurrency,
                    round_index=round_index,
                    slot_index=slot_index,
                    session_id=str(session_id),
                    status="http_error",
                    http_status=response.status_code,
                    error=body[:500] or response.reason_phrase,
                    create_session_ms=create_session_ms,
                    message_headers_ms=message_headers_ms,
                    first_sse_ms=None,
                    first_assistant_text_ms=None,
                    total_stream_ms=total_stream_ms,
                    sse_payload_count=0,
                )

            async for line in response.aiter_lines():
                if (payload := maybe_parse_sse_payload(line)) is None:
                    continue
                sse_payload_count += 1
                if first_sse_ms is None:
                    first_sse_ms = (time.perf_counter() - start_time) * 1000
                if first_assistant_text_ms is None and is_assistant_text_payload(
                    payload
                ):
                    first_assistant_text_ms = (time.perf_counter() - start_time) * 1000

            total_stream_ms = (time.perf_counter() - start_time) * 1000
            status = "ok"
            if sse_payload_count == 0:
                status = "no_stream_events"
            elif first_assistant_text_ms is None:
                status = "no_text"
            return RunSample(
                concurrency=concurrency,
                round_index=round_index,
                slot_index=slot_index,
                session_id=str(session_id),
                status=status,
                http_status=response.status_code,
                error=None,
                create_session_ms=create_session_ms,
                message_headers_ms=message_headers_ms,
                first_sse_ms=first_sse_ms,
                first_assistant_text_ms=first_assistant_text_ms,
                total_stream_ms=total_stream_ms,
                sse_payload_count=sse_payload_count,
            )
    except Exception as exc:
        return RunSample(
            concurrency=concurrency,
            round_index=round_index,
            slot_index=slot_index,
            session_id=str(session_id) if session_id is not None else None,
            status=classify_httpx_exception(exc),
            http_status=None,
            error=repr(exc),
            create_session_ms=create_session_ms,
            message_headers_ms=message_headers_ms,
            first_sse_ms=first_sse_ms,
            first_assistant_text_ms=first_assistant_text_ms,
            total_stream_ms=None,
            sse_payload_count=sse_payload_count,
        )


async def run_concurrency_level(
    client: httpx.AsyncClient,
    config: BenchmarkConfig,
    concurrency: int,
) -> list[RunSample]:
    """Run a full set of rounds for a single concurrency level."""
    samples: list[RunSample] = []
    for round_index in range(config.runs_per_level):
        tasks = [
            run_session_sample(
                client=client,
                config=config,
                concurrency=concurrency,
                round_index=round_index,
                slot_index=slot_index,
            )
            for slot_index in range(concurrency)
        ]
        round_samples = await asyncio.gather(*tasks)
        samples.extend(round_samples)
        for sample in round_samples:
            print_sample(sample)
    return samples


def format_metric(value: float | None) -> str:
    """Format latency metrics consistently."""
    if value is None:
        return "-"
    return f"{value:.1f}"


def print_sample(sample: RunSample) -> None:
    """Print a concise single-line sample result."""
    create_ms = format_metric(sample.create_session_ms)
    headers_ms = format_metric(sample.message_headers_ms)
    first_sse_ms = format_metric(sample.first_sse_ms)
    first_text_ms = format_metric(sample.first_assistant_text_ms)
    total_ms = format_metric(sample.total_stream_ms)
    http_status = sample.http_status if sample.http_status is not None else "-"
    print(
        "sample"
        f" concurrency={sample.concurrency}"
        f" round={sample.round_index + 1}"
        f" slot={sample.slot_index + 1}"
        f" status={sample.status}"
        f" http={http_status}"
        f" create_session_ms={create_ms}"
        f" message_headers_ms={headers_ms}"
        f" first_sse_ms={first_sse_ms}"
        f" first_text_ms={first_text_ms}"
        f" total_stream_ms={total_ms}"
        f" sse_events={sample.sse_payload_count}"
    )
    if sample.error:
        print(f"  error={sample.error}")


def print_summary(summaries: list[ConcurrencySummary]) -> None:
    """Print concurrency summaries in a compact table-like format."""
    print("\nsummary")
    for summary in summaries:
        print(
            f"  concurrency={summary.concurrency}"
            f" total_runs={summary.total_runs}"
            f" statuses={summary.status_counts}"
            f" http={summary.http_status_counts}"
        )
        print(
            "    create_session_ms"
            f" p50={format_metric(summary.create_session_ms['p50'])}"
            f" p95={format_metric(summary.create_session_ms['p95'])}"
            f" p99={format_metric(summary.create_session_ms['p99'])}"
        )
        print(
            "    message_headers_ms"
            f" p50={format_metric(summary.message_headers_ms['p50'])}"
            f" p95={format_metric(summary.message_headers_ms['p95'])}"
            f" p99={format_metric(summary.message_headers_ms['p99'])}"
        )
        print(
            "    first_sse_ms"
            f" p50={format_metric(summary.first_sse_ms['p50'])}"
            f" p95={format_metric(summary.first_sse_ms['p95'])}"
            f" p99={format_metric(summary.first_sse_ms['p99'])}"
        )
        print(
            "    first_text_ms"
            f" p50={format_metric(summary.first_assistant_text_ms['p50'])}"
            f" p95={format_metric(summary.first_assistant_text_ms['p95'])}"
            f" p99={format_metric(summary.first_assistant_text_ms['p99'])}"
        )
        print(
            "    total_stream_ms"
            f" p50={format_metric(summary.total_stream_ms['p50'])}"
            f" p95={format_metric(summary.total_stream_ms['p95'])}"
            f" p99={format_metric(summary.total_stream_ms['p99'])}"
        )


def dump_results(
    output_path: Path,
    config: BenchmarkConfig,
    samples: list[RunSample],
    summaries: list[ConcurrencySummary],
) -> None:
    """Write the benchmark config, samples, and summaries to JSON."""
    output_path.write_bytes(
        orjson.dumps(
            {
                "config": {
                    **asdict(config),
                    "output_json": str(config.output_json)
                    if config.output_json is not None
                    else None,
                },
                "samples": [asdict(sample) for sample in samples],
                "summaries": [asdict(summary) for summary in summaries],
            },
            option=orjson.OPT_INDENT_2,
        )
    )


async def amain(argv: list[str]) -> int:
    """Async CLI entrypoint."""
    config = parse_args(argv)
    timeout = httpx.Timeout(
        connect=config.connect_timeout,
        read=config.read_timeout,
        write=config.write_timeout,
        pool=config.pool_timeout,
    )
    samples: list[RunSample] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for concurrency in config.concurrency_sweep:
            print(f"\nlevel concurrency={concurrency}")
            samples.extend(await run_concurrency_level(client, config, concurrency))

    summaries = summarize_samples(samples)
    print_summary(summaries)
    if config.output_json is not None:
        dump_results(config.output_json, config, samples, summaries)
        print(f"\nresults_json={config.output_json}")
    return 0


def main() -> None:
    """Sync CLI entrypoint."""
    raise SystemExit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
