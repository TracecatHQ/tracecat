"""Deterministic checks for provider validation, bounded work and safe failures."""

import asyncio
import gzip
import hashlib
import socket
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from tracecat.search.embeddings import catalog as catalog_module
from tracecat.search.embeddings import client as client_module
from tracecat.search.embeddings.catalog import EmbeddingTokenCounter, get_model
from tracecat.search.embeddings.client import EmbeddingClient, _retry_after
from tracecat.search.embeddings.router import router
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
    ResolvedCredential,
)
from tracecat.search.types import EmbeddingInput, EmbeddingRequest, SearchScope


@pytest.fixture
def configuration():
    return PinnedConfiguration(
        2, get_model("openai", "text-embedding-3-small"), uuid.uuid4(), "default"
    )


@pytest.fixture
def credential():
    return ResolvedCredential(SecretStr("synthetic-private-key"), b"fingerprint")


def request_for(configuration, texts=("summary: alpha", "summary: beta")):
    return EmbeddingRequest(
        SearchScope(uuid.uuid4(), uuid.uuid4()),
        configuration.version,
        configuration.spec.dimensions,
        tuple(
            EmbeddingInput(i + 8, hashlib.sha256(text.encode()).hexdigest(), text)
            for i, text in enumerate(texts)
        ),
    )


def response_for(configuration, count=2):
    return {
        "model": configuration.spec.model,
        "data": [
            {"index": i, "embedding": [float(i + 1)] * configuration.spec.dimensions}
            for i in reversed(range(count))
        ],
        "usage": {"prompt_tokens": 6, "total_tokens": 6},
    }


@pytest.mark.anyio
async def test_response_indexes_preserve_input_identity(configuration, credential):
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for(configuration))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await EmbeddingClient(http).embed(
            configuration, credential, request_for(configuration)
        )
    assert [item.ordinal for item in result.results] == [8, 9]
    assert [item.vector[0] for item in result.results] == [1, 2]
    assert [item.input_hash for item in result.results] == [
        item.input_hash for item in request_for(configuration).items
    ]
    assert result.prompt_tokens == result.total_tokens == 6
    assert seen[0].url == "https://api.openai.com/v1/embeddings"
    assert seen[0].headers["authorization"] == "Bearer synthetic-private-key"


@pytest.mark.anyio
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

    async def handler(request):
        return httpx.Response(
            200,
            content=b"private-source-text" if problem == "json" else None,
            json=body if problem != "json" else None,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError) as caught:
            await EmbeddingClient(http).embed(
                configuration, credential, request_for(configuration)
            )
    assert caught.value.code == EmbeddingErrorCode.RESPONSE_INVALID
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert "private" not in str(caught.value)


@pytest.mark.anyio
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
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            text="synthetic-private-key private-source-text",
            headers={"Retry-After": "45", "Location": "https://example.com"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError) as caught:
            await EmbeddingClient(http).embed(
                configuration, credential, request_for(configuration)
            )
    assert calls == 1
    assert caught.value.code == code
    assert caught.value.retryable == retryable
    assert caught.value.retry_after == 45
    assert caught.value.__context__ is None


@pytest.mark.anyio
async def test_total_timeout(configuration, credential):
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError) as caught:
            await EmbeddingClient(http, timeout=0.02).embed(
                configuration, credential, request_for(configuration)
            )
    assert caught.value.code == EmbeddingErrorCode.TIMEOUT
    assert caught.value.__context__ is None


@pytest.mark.anyio
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

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError):
            await EmbeddingClient(http).embed(configuration, credential, request)


def test_catalog_and_counter():
    counter = EmbeddingTokenCounter()
    assert counter.count_tokens("hello") == 1
    assert counter.count_tokens("<|endoftext|>") > 1
    assert counter.count_tokens("日本語 🙂") > 0
    assert counter.identity == get_model("openai", "text-embedding-3-large").tokenizer
    assert get_model("openai", "text-embedding-3-large").dimensions == 3072
    with pytest.raises(EmbeddingError):
        get_model("openai", "gpt-4o")
    with pytest.raises(EmbeddingError):
        get_model("other", "text-embedding-3-small")


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
    assert len(schema["paths"]) == 3
    for path, methods in schema["paths"].items():
        assert path.startswith("/workspaces/{workspace_id}/search/configuration")
        for operation in methods.values():
            workspace = next(
                p for p in operation["parameters"] if p["name"] == "workspace_id"
            )
            assert workspace["in"] == "path"
            assert workspace["required"] is True


@pytest.mark.anyio
async def test_transport_exception_does_not_escape(configuration, credential):
    async def handler(request):
        raise httpx.ConnectError(
            "synthetic-private-key private-source-text", request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError) as caught:
            await EmbeddingClient(http).embed(
                configuration, credential, request_for(configuration)
            )
    assert caught.value.code == EmbeddingErrorCode.UNAVAILABLE
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert str(caught.value) == "UNAVAILABLE"


@pytest.mark.anyio
async def test_nonfinite_vector_is_rejected(configuration, credential):
    async def handler(request):
        # JSON decoders may accept NaN even though it is not standard JSON.
        return httpx.Response(
            200,
            content=b'{"model":"text-embedding-3-small","data":[{"index":0,"embedding":[NaN]}],"usage":{"prompt_tokens":1,"total_tokens":1}}',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError) as caught:
            await EmbeddingClient(http).embed(
                configuration, credential, request_for(configuration, ("hello",))
            )
    assert caught.value.code == EmbeddingErrorCode.RESPONSE_INVALID


@pytest.mark.anyio
async def test_token_counting_does_not_block_deadline(
    configuration, credential, monkeypatch
):
    class SlowCounter:
        def count_tokens(self, text):
            time.sleep(0.2)
            return 1

    monkeypatch.setattr(client_module, "EmbeddingTokenCounter", SlowCounter)

    async def handler(request):
        pytest.fail("Timed-out token preparation reached the provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        started = time.monotonic()
        with pytest.raises(EmbeddingError) as caught:
            await EmbeddingClient(http, timeout=0.02).embed(
                configuration, credential, request_for(configuration, ("hello",))
            )
        assert time.monotonic() - started < 0.15
    assert caught.value.code == EmbeddingErrorCode.TIMEOUT


@pytest.mark.anyio
async def test_invalid_unicode_is_sanitized(configuration, credential):
    request = request_for(configuration)
    item = replace(request.items[0], text="synthetic-private-text\ud800")
    request = replace(request, items=(item,))

    async def handler(request):
        pytest.fail("Invalid Unicode reached the provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EmbeddingError) as caught:
            await EmbeddingClient(http).embed(configuration, credential, request)
    assert caught.value.code == EmbeddingErrorCode.INPUT_INVALID
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


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


@pytest.mark.anyio
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
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(EmbeddingError) as caught:
                await EmbeddingClient(http).embed(
                    configuration, credential, request_for(configuration)
                )
        assert caught.value.code == EmbeddingErrorCode.UNAVAILABLE
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None
    finally:
        catalog_module._load_encoding.cache_clear()
