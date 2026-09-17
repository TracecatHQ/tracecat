"""Deterministic checks for provider validation, bounded work and safe failures."""

import asyncio
import gzip
import socket
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from tests.embedding_helpers import (
    StubProvider,
    configuration_for,
    request_for,
    wire_response,
)
from tracecat.search.embeddings import catalog as catalog_module
from tracecat.search.embeddings import client as client_module
from tracecat.search.embeddings.catalog import EmbeddingTokenCounter
from tracecat.search.embeddings.client import _retry_after
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    ResolvedCredential,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def configuration():
    return configuration_for("openai")


@pytest.fixture
def credential():
    return ResolvedCredential({"OPENAI_API_KEY": "synthetic-private-key"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("index", 0),  # Duplicate index: the fixture returns index 1 first.
        ("index", 99),
        ("index", True),
        ("embedding", [1.0]),
        ("embedding", [0.0] * 1536),
        ("embedding", [1e-100] * 1536),  # Becomes zero in PostgreSQL float32.
        ("embedding", [1e39] * 1536),  # Overflows PostgreSQL float32.
        ("embedding", ["private-source-text"] * 1536),
    ],
)
async def test_invalid_vector_or_index(configuration, credential, field, value):
    body = wire_response(configuration)
    body["data"][0][field] = value
    await StubProvider(httpx.Response(200, json=body)).rejects(
        configuration, credential, EmbeddingErrorCode.RESPONSE_INVALID
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("data", []),
        ("model", "chat-model"),
        ("usage", {"prompt_tokens": 1, "total_tokens": -1}),
        ("usage", {"prompt_tokens": 2, "total_tokens": 1}),
    ],
)
async def test_invalid_response_envelope(configuration, credential, field, value):
    body = wire_response(configuration) | {field: value}
    await StubProvider(httpx.Response(200, json=body)).rejects(
        configuration, credential, EmbeddingErrorCode.RESPONSE_INVALID
    )


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "CREDENTIAL_INVALID", False),
        (403, "CREDENTIAL_INVALID", False),
        (400, "CONFIGURATION_INVALID", False),
        (404, "CONFIGURATION_INVALID", False),
        (429, "RATE_LIMITED", True),
        (503, "UNAVAILABLE", True),
        (302, "CONFIGURATION_INVALID", False),
    ],
)
async def test_status_errors_discard_provider_body(
    configuration, credential, status, code, retryable
):
    stub = StubProvider(
        httpx.Response(
            status,
            text="synthetic-private-key private-source-text",
            headers={"Retry-After": "45", "Location": "https://example.com"},
        )
    )
    error = await stub.rejects(configuration, credential, code)
    assert len(stub.calls) == 1
    assert error.retryable == retryable and error.retry_after == 45


async def test_total_timeout(configuration, credential):
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    await StubProvider(handler).rejects(
        configuration, credential, EmbeddingErrorCode.TIMEOUT, timeout=0.02
    )


@pytest.mark.parametrize(
    "problem",
    [
        "empty",
        "blank",
        "count",
        "tokens",
        "batch_tokens",
        "characters",
        "hash",
        "ordinal",
        "version",
        "dimensions",
    ],
)
async def test_bad_inputs_never_reach_provider(configuration, credential, problem):
    request = request_for(configuration)
    match problem:
        case "empty":
            request = replace(request, items=())
        case "blank":
            request = request_for(configuration, ("  ",))
        case "count":
            request = request_for(configuration, ("hello",) * 33)
        case "tokens":
            request = request_for(configuration, (" hello" * 8192,))
        case "batch_tokens":
            request = request_for(configuration, (" hello" * 8001,) * 2)
        case "characters":
            request = request_for(configuration, ("x" * 131073,))
        case "hash":
            request = replace(
                request, items=(replace(request.items[0], input_hash="wrong"),)
            )
        case "ordinal":
            request = replace(request, items=(request.items[0], request.items[0]))
        case "version":
            request = replace(request, config_version=3)
        case "dimensions":
            request = replace(request, dimensions=4)

    async def handler(request):
        pytest.fail("Invalid inputs reached provider")

    with pytest.raises(EmbeddingError):
        await StubProvider(handler).embed(configuration, credential, request)


def test_retry_after_sanitization():
    for value in (
        None,
        "NaN",
        "inf",
        "-1",
        "private-source-text",
        "9" * 129,
        "999999999",
    ):
        assert _retry_after(value) is None
    assert _retry_after("0") == 0
    assert _retry_after("1.5") == 1.5
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=60))
    seconds = _retry_after(date)
    assert seconds is not None and 58 <= seconds <= 60


async def test_transport_exception_does_not_escape(configuration, credential):
    async def handler(request):
        raise httpx.ConnectError(
            "synthetic-private-key private-source-text", request=request
        )

    await StubProvider(handler).rejects(
        configuration, credential, EmbeddingErrorCode.UNAVAILABLE
    )


@pytest.mark.parametrize("body", [b"private-source-text", b"[NaN]", b"[Infinity]"])
async def test_invalid_json_or_nonfinite_vector(configuration, credential, body):
    if body.startswith(b"["):
        body = (
            b'{"model":"text-embedding-3-small","data":[{"index":0,"embedding":'
            + body
            + b'}],"usage":{"prompt_tokens":1,"total_tokens":1}}'
        )
    configuration = replace(
        configuration, spec=replace(configuration.spec, dimensions=1)
    )
    await StubProvider(httpx.Response(200, content=body)).rejects(
        configuration,
        credential,
        EmbeddingErrorCode.RESPONSE_INVALID,
        request_for(configuration, ("hello",)),
    )


async def test_token_counting_does_not_block_deadline(
    configuration, credential, monkeypatch
):
    class SlowCounter:
        def count_tokens(self, text):
            time.sleep(0.2)
            return 1

    monkeypatch.setattr(client_module, "token_counter", lambda spec: SlowCounter())

    async def handler(request):
        pytest.fail("Timed-out token preparation reached the provider")

    started = time.monotonic()
    with pytest.raises(EmbeddingError) as caught:
        await StubProvider(handler).embed(
            configuration,
            credential,
            request_for(configuration, ("hello",)),
            timeout=0.02,
        )
    assert time.monotonic() - started < 0.15
    assert caught.value.code == EmbeddingErrorCode.TIMEOUT


async def test_invalid_unicode_is_sanitized(configuration, credential):
    request = request_for(configuration)
    item = replace(request.items[0], text="synthetic-private-text\ud800")
    request = replace(request, items=(item,))

    async def handler(request):
        pytest.fail("Invalid Unicode reached the provider")

    await StubProvider(handler).rejects(
        configuration, credential, EmbeddingErrorCode.INPUT_INVALID, request
    )


def test_cold_tokenizer_works_offline(monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Tokenizer initialization attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "")
    catalog_module._load_encoding.cache_clear()
    try:
        counter = EmbeddingTokenCounter()
        # Golden counts from the pinned upstream cl100k_base ordinary encoding.
        for text, expected in (
            ("hello", 1),
            ("<|endoftext|>", 7),
            ("日本語 🙂", 5),
            ("We're testing 1234567.\n\n", 8),
            ("     \r\n  punctuation!?", 4),
        ):
            assert counter.count_tokens(text) == expected
    finally:
        catalog_module._load_encoding.cache_clear()


@pytest.mark.parametrize("corrupt", [False, True])
async def test_missing_or_corrupt_vocabulary_fails_safely(
    configuration, credential, monkeypatch, tmp_path, corrupt
):
    monkeypatch.setattr(catalog_module, "__file__", str(tmp_path / "catalog.py"))
    if corrupt:
        data = tmp_path / "data"
        data.mkdir()
        (data / "cl100k_base.tiktoken.gz").write_bytes(gzip.compress(b"corrupt"))
    catalog_module._load_encoding.cache_clear()

    async def handler(request):
        pytest.fail("Missing tokenizer data reached the provider")

    try:
        await StubProvider(handler).rejects(
            configuration, credential, EmbeddingErrorCode.UNAVAILABLE
        )
    finally:
        catalog_module._load_encoding.cache_clear()
