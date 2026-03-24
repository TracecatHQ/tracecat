from __future__ import annotations

import uuid

import pytest

from scripts.benchmark.litellm_sidecar_benchmark import (
    AuthMode,
    BenchmarkConfig,
    RunSample,
    build_auth_headers,
    build_request_payload,
    classify_httpx_exception,
    extract_assistant_text,
    extract_non_stream_text,
    maybe_parse_sse_payload,
    normalize_endpoint_path,
    parse_concurrency_sweep,
    percentile,
    resolve_bearer_token,
    summarize_latency,
    summarize_samples,
)


def make_config(*, auth_mode: AuthMode = AuthMode.NONE) -> BenchmarkConfig:
    return BenchmarkConfig(
        litellm_url="http://127.0.0.1:4000",
        endpoint_path="/v1/chat/completions",
        auth_mode=auth_mode,
        bearer_token_env="BENCHMARK_TOKEN",
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        use_workspace_credentials=True,
        model="gpt-4o-mini",
        provider="openai",
        prompt="Reply with benchmark-ok.",
        stream=True,
        temperature=0.0,
        max_tokens=32,
        concurrency_sweep=(1, 2),
        runs_per_level=1,
        connect_timeout=20.0,
        read_timeout=300.0,
        write_timeout=30.0,
        pool_timeout=10.0,
        output_json=None,
    )


def test_parse_concurrency_sweep_parses_csv() -> None:
    assert parse_concurrency_sweep("1, 2,3") == (1, 2, 3)


def test_normalize_endpoint_path_adds_leading_slash() -> None:
    assert normalize_endpoint_path("v1/chat/completions") == "/v1/chat/completions"


def test_maybe_parse_sse_payload_extracts_data_line() -> None:
    payload = maybe_parse_sse_payload('data: {"choices":[{"delta":{"content":"hi"}}]}')

    assert payload == {"choices": [{"delta": {"content": "hi"}}]}


def test_extract_assistant_text_reads_openai_delta() -> None:
    payload = {"choices": [{"delta": {"content": "benchmark-ok"}}]}

    assert extract_assistant_text(payload) == "benchmark-ok"


def test_extract_non_stream_text_reads_chat_completion_message() -> None:
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "benchmark-ok"}}]
    }

    assert extract_non_stream_text(payload) == "benchmark-ok"


def test_resolve_bearer_token_uses_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCHMARK_TOKEN", "token-123")

    assert resolve_bearer_token("BENCHMARK_TOKEN") == "token-123"


def test_build_auth_headers_in_none_mode_returns_empty_headers() -> None:
    headers = build_auth_headers(make_config(auth_mode=AuthMode.NONE), uuid.uuid4())

    assert headers == {}


def test_build_request_payload_uses_stream_and_prompt() -> None:
    payload = build_request_payload(make_config())

    assert payload["stream"] is True
    assert payload["messages"] == [
        {"role": "user", "content": "Reply with benchmark-ok."}
    ]


def test_classify_httpx_exception_prefers_specific_timeout() -> None:
    import httpx

    exc = httpx.ConnectTimeout("boom")

    assert classify_httpx_exception(exc) == "connect_timeout"


def test_percentile_interpolates_values() -> None:
    assert percentile([10.0, 20.0, 30.0, 40.0], 0.5) == 25.0


def test_summarize_latency_handles_empty_values() -> None:
    assert summarize_latency([]) == {
        "count": 0,
        "min": None,
        "p50": None,
        "p95": None,
        "p99": None,
        "max": None,
        "avg": None,
    }


def test_summarize_samples_groups_by_concurrency() -> None:
    samples = [
        RunSample(
            concurrency=2,
            round_index=0,
            slot_index=0,
            session_id="a",
            status="ok",
            http_status=200,
            error=None,
            response_headers_ms=20.0,
            first_sse_ms=30.0,
            first_assistant_text_ms=45.0,
            total_duration_ms=100.0,
            sse_payload_count=3,
        ),
        RunSample(
            concurrency=2,
            round_index=0,
            slot_index=1,
            session_id="b",
            status="no_text",
            http_status=200,
            error=None,
            response_headers_ms=25.0,
            first_sse_ms=35.0,
            first_assistant_text_ms=None,
            total_duration_ms=80.0,
            sse_payload_count=2,
        ),
    ]

    [summary] = summarize_samples(samples)

    assert summary.concurrency == 2
    assert summary.total_runs == 2
    assert summary.status_counts == {"ok": 1, "no_text": 1}
    assert summary.http_status_counts == {"200": 2}
    assert summary.first_sse_ms["p50"] == 32.5
    assert summary.first_assistant_text_ms["count"] == 1.0
