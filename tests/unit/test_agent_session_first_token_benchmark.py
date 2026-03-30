from __future__ import annotations

import uuid

import pytest

from scripts.benchmark.agent_session_first_token_benchmark import (
    BenchmarkConfig,
    RunSample,
    build_headers,
    build_message_payload,
    build_session_payload,
    classify_httpx_exception,
    is_assistant_text_payload,
    maybe_parse_sse_payload,
    parse_concurrency_sweep,
    percentile,
    resolve_cookie_header,
    summarize_latency,
    summarize_samples,
)


def make_config() -> BenchmarkConfig:
    return BenchmarkConfig(
        base_url="http://localhost:80",
        cookie_header="tracecat_auth=test; tracecat-org-id=org",
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        entity_type="agent_preset",
        entity_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        model="gpt-5.4",
        model_provider="openai",
        prompt="Reply with benchmark-ok.",
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


def test_maybe_parse_sse_payload_extracts_data_line() -> None:
    payload = maybe_parse_sse_payload('data: {"type":"text-delta","delta":"hi"}')

    assert payload == {"type": "text-delta", "delta": "hi"}


def test_is_assistant_text_payload_matches_text_delta() -> None:
    assert is_assistant_text_payload({"type": "text-delta", "delta": "ok"})
    assert not is_assistant_text_payload({"type": "text-start", "id": "1"})


def test_resolve_cookie_header_uses_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCHMARK_COOKIE", "cookie=value")

    assert resolve_cookie_header("BENCHMARK_COOKIE") == "cookie=value"


def test_build_headers_sets_cookie_and_sse_accept() -> None:
    headers = build_headers(make_config())

    assert headers["Cookie"] == "tracecat_auth=test; tracecat-org-id=org"
    assert headers["Accept"] == "text/event-stream"


def test_build_session_payload_uses_entity_fields() -> None:
    payload = build_session_payload(make_config())

    assert payload["entity_type"] == "agent_preset"
    assert payload["entity_id"] == "00000000-0000-0000-0000-000000000002"


def test_build_message_payload_uses_vercel_shape() -> None:
    payload = build_message_payload(
        make_config(), uuid.UUID("00000000-0000-0000-0000-000000000003")
    )

    assert payload["kind"] == "vercel"
    assert payload["message"]["role"] == "user"
    assert payload["message"]["parts"] == [
        {"type": "text", "text": "Reply with benchmark-ok."}
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
            create_session_ms=20.0,
            message_headers_ms=30.0,
            first_sse_ms=40.0,
            first_assistant_text_ms=45.0,
            total_stream_ms=100.0,
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
            create_session_ms=22.0,
            message_headers_ms=35.0,
            first_sse_ms=50.0,
            first_assistant_text_ms=None,
            total_stream_ms=80.0,
            sse_payload_count=2,
        ),
    ]

    [summary] = summarize_samples(samples)

    assert summary.concurrency == 2
    assert summary.total_runs == 2
    assert summary.status_counts == {"ok": 1, "no_text": 1}
    assert summary.http_status_counts == {"200": 2}
    assert summary.first_sse_ms["p50"] == 45.0
    assert summary.first_assistant_text_ms["count"] == 1.0
