#!/usr/bin/env python
"""Benchmark direct LiteLLM requests for sidecar worker tuning.

This script isolates the LiteLLM sidecar from the rest of the Tracecat API path.
It sends concurrent requests directly to the OpenAI-compatible LiteLLM endpoint
and records:

- time to response headers
- time to first SSE payload
- time to first assistant text delta
- total request duration
- status and timeout breakdowns

For the Tracecat sidecar, the default auth mode mints the same JWT bearer token
the agent runtime would use, so the benchmark still exercises the real gateway
authentication and credential-injection path.
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
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import httpx
import orjson

from tracecat.agent.tokens import mint_llm_token

DEFAULT_LITELLM_URL = "http://127.0.0.1:4000"
DEFAULT_ENDPOINT_PATH = "/v1/chat/completions"
DEFAULT_PROMPT = "Reply with exactly 'benchmark-ok'."
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_PROVIDER = "openai"
DEFAULT_RUNS_PER_LEVEL = 3
DEFAULT_CONNECT_TIMEOUT = 20.0
DEFAULT_READ_TIMEOUT = 300.0
DEFAULT_WRITE_TIMEOUT = 30.0
DEFAULT_POOL_TIMEOUT = 10.0
DEFAULT_BEARER_TOKEN_ENV_CANDIDATES = (
    "TRACECAT__AGENT_API_TOKEN",
    "TRACECAT_AGENT_API_TOKEN",
    "AGENT_API_TOKEN",
    "LITELLM_API_KEY",
)


class AuthMode(StrEnum):
    TRACECAT_JWT = "tracecat-jwt"
    BEARER_ENV = "bearer-env"
    NONE = "none"


@dataclass(slots=True)
class BenchmarkConfig:
    litellm_url: str
    endpoint_path: str
    auth_mode: AuthMode
    bearer_token_env: str | None
    workspace_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    use_workspace_credentials: bool
    model: str
    provider: str
    prompt: str
    stream: bool
    temperature: float | None
    max_tokens: int | None
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
    session_id: str
    status: str
    http_status: int | None
    error: str | None
    response_headers_ms: float | None
    first_sse_ms: float | None
    first_assistant_text_ms: float | None
    total_duration_ms: float | None
    sse_payload_count: int


@dataclass(slots=True)
class ConcurrencySummary:
    concurrency: int
    total_runs: int
    status_counts: dict[str, int]
    http_status_counts: dict[str, int]
    response_headers_ms: dict[str, float | None]
    first_sse_ms: dict[str, float | None]
    first_assistant_text_ms: dict[str, float | None]
    total_duration_ms: dict[str, float | None]


def parse_args(argv: list[str]) -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="Benchmark direct LiteLLM latency across concurrency levels.",
    )
    parser.add_argument(
        "--litellm-url",
        default=os.environ.get("TRACECAT_BENCHMARK_LITELLM_URL", DEFAULT_LITELLM_URL),
        help="LiteLLM base URL, e.g. http://127.0.0.1:4000",
    )
    parser.add_argument(
        "--endpoint-path",
        default=DEFAULT_ENDPOINT_PATH,
        help="OpenAI-compatible LiteLLM endpoint path.",
    )
    parser.add_argument(
        "--auth-mode",
        type=AuthMode,
        choices=list(AuthMode),
        default=AuthMode.TRACECAT_JWT,
        help="Authentication mode for direct LiteLLM requests.",
    )
    parser.add_argument(
        "--bearer-token-env",
        default=None,
        help="Environment variable that contains a bearer token when auth mode is bearer-env.",
    )
    parser.add_argument(
        "--workspace-id",
        type=uuid.UUID,
        default=None,
        help="Workspace ID used when auth mode is tracecat-jwt.",
    )
    parser.add_argument(
        "--organization-id",
        type=uuid.UUID,
        default=None,
        help="Organization ID used when auth mode is tracecat-jwt.",
    )
    parser.add_argument(
        "--use-org-credentials",
        action="store_true",
        help="Use organization-level credentials instead of workspace-level credentials.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model name to benchmark.",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help="Provider name to encode into the Tracecat sidecar JWT.",
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
        "--no-stream",
        action="store_true",
        help="Disable streaming and measure full responses only.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Temperature to send in the request body. Use a negative value to omit it.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=32,
        help="max_tokens to send in the request body. Use 0 to omit it.",
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

    prompt = resolve_prompt(prompt=args.prompt, prompt_file=args.prompt_file)
    require_tracecat_context = args.auth_mode is AuthMode.TRACECAT_JWT
    workspace_id = require_uuid_arg(
        args.workspace_id,
        "--workspace-id",
        required=require_tracecat_context,
    )
    organization_id = require_uuid_arg(
        args.organization_id,
        "--organization-id",
        required=require_tracecat_context,
    )
    bearer_token_env = args.bearer_token_env
    if args.auth_mode is AuthMode.BEARER_ENV and bearer_token_env is None:
        bearer_token_env = DEFAULT_BEARER_TOKEN_ENV_CANDIDATES[0]

    return BenchmarkConfig(
        litellm_url=args.litellm_url.rstrip("/"),
        endpoint_path=normalize_endpoint_path(args.endpoint_path),
        auth_mode=args.auth_mode,
        bearer_token_env=bearer_token_env,
        workspace_id=workspace_id,
        organization_id=organization_id,
        use_workspace_credentials=not args.use_org_credentials,
        model=args.model,
        provider=args.provider,
        prompt=prompt,
        stream=not args.no_stream,
        temperature=args.temperature if args.temperature >= 0 else None,
        max_tokens=args.max_tokens if args.max_tokens > 0 else None,
        concurrency_sweep=parse_concurrency_sweep(args.concurrency_sweep),
        runs_per_level=args.runs_per_level,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        write_timeout=args.write_timeout,
        pool_timeout=args.pool_timeout,
        output_json=args.output_json,
    )


def require_uuid_arg(
    value: uuid.UUID | None,
    flag_name: str,
    *,
    required: bool,
) -> uuid.UUID | None:
    """Validate required UUID CLI arguments."""
    if required and value is None:
        raise SystemExit(f"{flag_name} is required when auth mode is tracecat-jwt.")
    return value


def normalize_endpoint_path(value: str) -> str:
    """Normalize endpoint path to a leading-slash path."""
    if not value:
        raise SystemExit("--endpoint-path must not be empty.")
    return value if value.startswith("/") else f"/{value}"


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


def resolve_bearer_token(bearer_token_env: str | None) -> str:
    """Resolve bearer token from env when using bearer-env auth mode."""
    candidates = (
        (bearer_token_env,) if bearer_token_env else DEFAULT_BEARER_TOKEN_ENV_CANDIDATES
    )
    for env_name in candidates:
        if not env_name:
            continue
        if token := os.environ.get(env_name):
            return token
    candidate_list = ", ".join(candidates)
    raise SystemExit(
        f"Missing bearer token. Set one of: {candidate_list}, or pass --bearer-token-env."
    )


def build_auth_headers(
    config: BenchmarkConfig, session_id: uuid.UUID
) -> dict[str, str]:
    """Build request auth headers for a single sample."""
    match config.auth_mode:
        case AuthMode.TRACECAT_JWT:
            if config.workspace_id is None or config.organization_id is None:
                raise RuntimeError(
                    "Tracecat JWT mode requires workspace and organization IDs."
                )
            token = mint_llm_token(
                workspace_id=config.workspace_id,
                organization_id=config.organization_id,
                session_id=session_id,
                model=config.model,
                provider=config.provider,
                model_settings={},
                use_workspace_credentials=config.use_workspace_credentials,
            )
            return {"Authorization": f"Bearer {token}"}
        case AuthMode.BEARER_ENV:
            token = resolve_bearer_token(config.bearer_token_env)
            return {"Authorization": f"Bearer {token}"}
        case AuthMode.NONE:
            return {}


def build_request_payload(config: BenchmarkConfig) -> dict[str, Any]:
    """Build OpenAI-compatible request payload."""
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [{"role": "user", "content": config.prompt}],
        "stream": config.stream,
    }
    if config.temperature is not None:
        payload["temperature"] = config.temperature
    if config.max_tokens is not None:
        payload["max_tokens"] = config.max_tokens
    return payload


def maybe_parse_sse_payload(line: str) -> dict[str, Any] | None:
    """Parse a JSON SSE payload from a single streamed line."""
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


def extract_assistant_text(payload: dict[str, Any]) -> str | None:
    """Extract assistant text delta from OpenAI-compatible stream payloads."""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        content = delta.get("content")
        if isinstance(content, str) and content:
            return content
    return None


def extract_non_stream_text(payload: dict[str, Any]) -> str | None:
    """Extract assistant text from a non-streaming chat completion payload."""
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            return content
    return None


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
                response_headers_ms=summarize_latency(
                    [sample.response_headers_ms for sample in grouped]
                ),
                first_sse_ms=summarize_latency(
                    [sample.first_sse_ms for sample in grouped]
                ),
                first_assistant_text_ms=summarize_latency(
                    [sample.first_assistant_text_ms for sample in grouped]
                ),
                total_duration_ms=summarize_latency(
                    [sample.total_duration_ms for sample in grouped]
                ),
            )
        )
    return summaries


async def run_stream_sample(
    client: httpx.AsyncClient,
    config: BenchmarkConfig,
    concurrency: int,
    round_index: int,
    slot_index: int,
) -> RunSample:
    """Run a single direct LiteLLM request and record latency metrics."""
    session_id = uuid.uuid4()
    start_time = time.perf_counter()
    response_headers_ms: float | None = None
    first_sse_ms: float | None = None
    first_assistant_text_ms: float | None = None
    sse_payload_count = 0
    request_headers = {
        "Content-Type": "application/json",
        **build_auth_headers(config, session_id),
    }
    payload = build_request_payload(config)
    url = f"{config.litellm_url}{config.endpoint_path}"

    try:
        if config.stream:
            async with client.stream(
                "POST",
                url,
                headers=request_headers,
                json=payload,
            ) as response:
                response_headers_ms = (time.perf_counter() - start_time) * 1000
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    total_duration_ms = (time.perf_counter() - start_time) * 1000
                    return RunSample(
                        concurrency=concurrency,
                        round_index=round_index,
                        slot_index=slot_index,
                        session_id=str(session_id),
                        status="http_error",
                        http_status=response.status_code,
                        error=body[:500] or response.reason_phrase,
                        response_headers_ms=response_headers_ms,
                        first_sse_ms=None,
                        first_assistant_text_ms=None,
                        total_duration_ms=total_duration_ms,
                        sse_payload_count=0,
                    )

                async for line in response.aiter_lines():
                    if (payload_data := maybe_parse_sse_payload(line)) is None:
                        continue
                    sse_payload_count += 1
                    if first_sse_ms is None:
                        first_sse_ms = (time.perf_counter() - start_time) * 1000
                    if (
                        first_assistant_text_ms is None
                        and extract_assistant_text(payload_data) is not None
                    ):
                        first_assistant_text_ms = (
                            time.perf_counter() - start_time
                        ) * 1000

                total_duration_ms = (time.perf_counter() - start_time) * 1000
                status = "ok"
                error: str | None = None
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
                    error=error,
                    response_headers_ms=response_headers_ms,
                    first_sse_ms=first_sse_ms,
                    first_assistant_text_ms=first_assistant_text_ms,
                    total_duration_ms=total_duration_ms,
                    sse_payload_count=sse_payload_count,
                )

        response = await client.post(url, headers=request_headers, json=payload)
        total_duration_ms = (time.perf_counter() - start_time) * 1000
        response_headers_ms = total_duration_ms
        if response.status_code >= 400:
            return RunSample(
                concurrency=concurrency,
                round_index=round_index,
                slot_index=slot_index,
                session_id=str(session_id),
                status="http_error",
                http_status=response.status_code,
                error=response.text[:500] or response.reason_phrase,
                response_headers_ms=response_headers_ms,
                first_sse_ms=None,
                first_assistant_text_ms=None,
                total_duration_ms=total_duration_ms,
                sse_payload_count=0,
            )
        response_payload = cast(dict[str, Any], response.json())
        assistant_text = extract_non_stream_text(response_payload)
        status = "ok" if assistant_text is not None else "no_text"
        return RunSample(
            concurrency=concurrency,
            round_index=round_index,
            slot_index=slot_index,
            session_id=str(session_id),
            status=status,
            http_status=response.status_code,
            error=None,
            response_headers_ms=response_headers_ms,
            first_sse_ms=None,
            first_assistant_text_ms=total_duration_ms if assistant_text else None,
            total_duration_ms=total_duration_ms,
            sse_payload_count=0,
        )
    except Exception as exc:
        total_duration_ms = (time.perf_counter() - start_time) * 1000
        return RunSample(
            concurrency=concurrency,
            round_index=round_index,
            slot_index=slot_index,
            session_id=str(session_id),
            status=classify_httpx_exception(exc),
            http_status=None,
            error=repr(exc),
            response_headers_ms=response_headers_ms,
            first_sse_ms=first_sse_ms,
            first_assistant_text_ms=first_assistant_text_ms,
            total_duration_ms=total_duration_ms,
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
            run_stream_sample(
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


def print_sample(sample: RunSample) -> None:
    """Print a concise single-line sample result."""
    headers_ms = format_metric(sample.response_headers_ms)
    first_sse_ms = format_metric(sample.first_sse_ms)
    first_text_ms = format_metric(sample.first_assistant_text_ms)
    total_ms = format_metric(sample.total_duration_ms)
    http_status = sample.http_status if sample.http_status is not None else "-"
    print(
        "sample"
        f" concurrency={sample.concurrency}"
        f" round={sample.round_index + 1}"
        f" slot={sample.slot_index + 1}"
        f" status={sample.status}"
        f" http={http_status}"
        f" headers_ms={headers_ms}"
        f" first_sse_ms={first_sse_ms}"
        f" first_text_ms={first_text_ms}"
        f" total_ms={total_ms}"
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
            "    headers_ms"
            f" p50={format_metric(summary.response_headers_ms['p50'])}"
            f" p95={format_metric(summary.response_headers_ms['p95'])}"
            f" p99={format_metric(summary.response_headers_ms['p99'])}"
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
            "    total_ms"
            f" p50={format_metric(summary.total_duration_ms['p50'])}"
            f" p95={format_metric(summary.total_duration_ms['p95'])}"
            f" p99={format_metric(summary.total_duration_ms['p99'])}"
        )


def format_metric(value: float | None) -> str:
    """Format latency metrics consistently."""
    if value is None:
        return "-"
    return f"{value:.1f}"


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
                    "auth_mode": config.auth_mode.value,
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
