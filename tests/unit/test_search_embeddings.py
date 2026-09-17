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
from fastapi import FastAPI

from tests.embedding_helpers import (
    StubProvider,
    configuration_for,
    request_for,
    response_for,
)
from tracecat.search.embeddings import catalog as catalog_module
from tracecat.search.embeddings import client as client_module
from tracecat.search.embeddings.catalog import EmbeddingTokenCounter
from tracecat.search.embeddings.client import _retry_after
from tracecat.search.embeddings.router import router
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


async def test_response_indexes_preserve_input_identity(configuration, credential):
    stub = StubProvider(httpx.Response(200, json=response_for(configuration)))
    result = await stub.embed(configuration, credential)
    assert [item.ordinal for item in result.results] == [8, 9]
    assert [item.vector[0] for item in result.results] == [1, 2]
    assert [item.input_hash for item in result.results] == [
        item.input_hash for item in request_for(configuration).items
    ]
    assert result.prompt_tokens == result.total_tokens == 6
    assert stub.calls[0].url == "https://api.openai.com/v1/embeddings"
    assert stub.calls[0].headers["authorization"] == "Bearer synthetic-private-key"


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "duplicate",
        "range",
        "dimensions",
        "zero",
        "infinite",
        "underflow",
        "overflow",
        "model",
        "usage",
        "boolean",
        "string",
        "json",
    ],
)
async def test_invalid_provider_output(configuration, credential, problem):
    body = response_for(configuration)
    vector = body["data"][0]
    match problem:
        case "missing":
            body["data"].pop()
        case "duplicate":
            vector["index"] = 0
        case "range":
            vector["index"] = 99
        case "dimensions":
            vector["embedding"] = [1.0]
        case "zero":
            vector["embedding"] = [0.0] * configuration.spec.dimensions
        case "infinite":
            vector["embedding"][0] = 1e39
        case "underflow":
            vector["embedding"] = [1e-100] * configuration.spec.dimensions
        case "overflow":
            vector["embedding"][0] = 1e300
        case "model":
            body["model"] = "chat-model"
        case "usage":
            body["usage"]["total_tokens"] = -1
        case "boolean":
            vector["index"] = True
        case "string":
            vector["embedding"][0] = "private-source-text"

    stub = StubProvider(
        httpx.Response(
            200,
            content=b"private-source-text" if problem == "json" else None,
            json=body if problem != "json" else None,
        )
    )
    await stub.rejects(configuration, credential, EmbeddingErrorCode.RESPONSE_INVALID)


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


def test_configuration_routes_declare_workspace_path():
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    assert len(schema["paths"]) == 1
    assert set(next(iter(schema["paths"].values()))) == {"get"}
    for path, methods in schema["paths"].items():
        assert path.startswith("/workspaces/{workspace_id}/search/configuration")
        for operation in methods.values():
            workspace = next(
                p for p in operation["parameters"] if p["name"] == "workspace_id"
            )
            assert workspace["in"] == "path"
            assert workspace["required"] is True


async def test_transport_exception_does_not_escape(configuration, credential):
    async def handler(request):
        raise httpx.ConnectError(
            "synthetic-private-key private-source-text", request=request
        )

    await StubProvider(handler).rejects(
        configuration, credential, EmbeddingErrorCode.UNAVAILABLE
    )


async def test_nonfinite_vector_is_rejected(configuration, credential):
    async def handler(request):
        # JSON decoders may accept NaN even though it is not standard JSON.
        return httpx.Response(
            200,
            content=b'{"model":"text-embedding-3-small","data":[{"index":0,"embedding":[NaN]}],"usage":{"prompt_tokens":1,"total_tokens":1}}',
        )

    await StubProvider(handler).rejects(
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
