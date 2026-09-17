"""Self-hosted selection, native Ollama, and OpenAI-compatible vLLM contracts."""

import hashlib
import uuid

import httpx
import orjson
import pytest
from pydantic import SecretStr

from tracecat.search.embeddings import client as client_module
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.self_hosted import select_self_hosted_model
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
    ResolvedCredential,
)
from tracecat.search.types import EmbeddingInput, EmbeddingRequest, SearchScope

MODELS = [
    ("ollama", "all-minilm"),
    ("ollama", "all-minilm:latest"),
    ("ollama", "all-minilm:22m"),
    ("vllm", "sentence-transformers/all-MiniLM-L6-v2"),
]


@pytest.mark.parametrize("provider,model", MODELS)
@pytest.mark.parametrize(
    "base_url", ["http://models.test/v1", "https://models.test/proxy/v1/"]
)
def test_endpoint_uses_saved_provider_host_and_proxy_prefix(provider, model, base_url):
    spec = select_self_hosted_model(
        provider, [model], {f"{provider.upper()}_BASE_URL": base_url}
    )
    assert spec is not None
    expected = base_url.rstrip("/")
    expected = (
        expected[:-3] + "/api/embed"
        if provider == "ollama"
        else expected + "/embeddings"
    )
    assert spec.endpoint == expected
    assert spec.dimensions == 384
    assert spec.input_token_limit == 240


@pytest.mark.parametrize(
    "url",
    [
        "ftp://models.test/v1",
        "http:///v1",
        "http://user:secret@models.test/v1",
        "http://models.test/v1?q=secret",
        "http://models.test/v1#fragment",
        "http://models.test",
    ],
)
def test_invalid_endpoint_rejected(url):
    with pytest.raises(EmbeddingError) as caught:
        select_self_hosted_model("vllm", [MODELS[-1][1]], {"VLLM_BASE_URL": url})
    assert caught.value.code == EmbeddingErrorCode.CONFIGURATION_INVALID


def test_missing_model_never_infers_embedding_support_from_chat():
    for provider in ("ollama", "vllm"):
        assert select_self_hosted_model(provider, ["synthetic-chat"], {}) is None
    with pytest.raises(EmbeddingError):
        select_self_hosted_model("vllm", [MODELS[-1][1]], {})
    spec = select_self_hosted_model("ollama", ["all-minilm:latest"], {})
    assert spec is not None
    assert spec.endpoint == "http://localhost:11434/api/embed"


def test_model_choice_is_independent_of_catalog_order():
    for models in (
        ["all-minilm:latest", "all-minilm:22m"],
        ["all-minilm:22m", "all-minilm:latest"],
    ):
        spec = select_self_hosted_model("ollama", models, {})
        assert spec is not None and spec.model == "all-minilm:22m"


@pytest.mark.anyio
@pytest.mark.parametrize("provider,model", MODELS)
@pytest.mark.parametrize("api_key", ["", "synthetic-key"])
async def test_payload_auth_and_ordered_vectors(provider, model, api_key, monkeypatch):
    spec = select_self_hosted_model(
        provider, [model], {f"{provider.upper()}_BASE_URL": "http://models.test/v1"}
    )
    assert spec is not None
    configuration = PinnedConfiguration(1, spec, uuid.uuid4(), "default")
    texts = ("First document", "Second document")
    request = EmbeddingRequest(
        SearchScope(uuid.uuid4(), uuid.uuid4()),
        1,
        384,
        tuple(
            EmbeddingInput(i, hashlib.sha256(text.encode()).hexdigest(), text)
            for i, text in enumerate(texts)
        ),
    )

    async def handler(http_request):
        body = orjson.loads(http_request.content)
        assert http_request.headers.get("authorization") == (
            f"Bearer {api_key}" if api_key else None
        )
        assert body["input"] == list(texts)
        assert body["model"] == model
        if provider == "ollama":
            assert body["truncate"] is False
            return httpx.Response(
                200,
                json={
                    "model": model if ":" in model else model + ":latest",
                    "embeddings": [[1.0] * 384, [2.0] * 384],
                },
            )
        assert body["encoding_format"] == "float"
        assert "dimensions" not in body
        return httpx.Response(
            200,
            json={
                "model": model,
                "data": [
                    {"index": 1, "embedding": [2.0] * 384},
                    {"index": 0, "embedding": [1.0] * 384},
                ],
                "usage": {"prompt_tokens": 8, "total_tokens": 8},
            },
        )

    def outbound_client(*, origin_url, timeout):
        assert origin_url == spec.endpoint
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(client_module, "create_outbound_http_client", outbound_client)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await EmbeddingClient(http).embed(
            configuration, ResolvedCredential(SecretStr(api_key), b"digest"), request
        )
    assert [r.vector[0] for r in result.results] == [1.0, 2.0]
    assert result.prompt_tokens == (None if provider == "ollama" else 8)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        "wrong_model",
        "wrong_dimension",
        "wrong_count",
        "zero",
        "oversize",
        "redirect",
        "long_unicode",
    ],
)
@pytest.mark.parametrize("provider,model", [MODELS[1], MODELS[-1]])
async def test_invalid_responses_and_inputs_are_rejected(
    provider, model, failure, monkeypatch
):
    spec = select_self_hosted_model(
        provider, [model], {f"{provider.upper()}_BASE_URL": "http://models.test/v1"}
    )
    assert spec is not None
    text = "界" * 81 if failure == "long_unicode" else "A document"
    request = EmbeddingRequest(
        SearchScope(uuid.uuid4(), uuid.uuid4()),
        1,
        384,
        (EmbeddingInput(0, hashlib.sha256(text.encode()).hexdigest(), text),),
    )
    calls = []

    async def handler(http_request):
        calls.append(http_request)
        if failure == "redirect":
            return httpx.Response(
                302, headers={"Location": "https://other.test/embeddings"}
            )
        if failure == "oversize":
            return httpx.Response(200, content=b" " * 4_000_001)
        response_model = "unrelated-model" if failure == "wrong_model" else model
        vector = [0.0 if failure == "zero" else 1.0] * (
            3 if failure == "wrong_dimension" else 384
        )
        vectors = [] if failure == "wrong_count" else [vector]
        payload = (
            {"model": response_model, "embeddings": vectors}
            if provider == "ollama"
            else {
                "model": response_model,
                "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        )
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        client_module,
        "create_outbound_http_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as http:
        with pytest.raises(EmbeddingError) as caught:
            await EmbeddingClient(http).embed(
                PinnedConfiguration(1, spec, uuid.uuid4(), "default"),
                ResolvedCredential(SecretStr("synthetic-key"), b"digest"),
                request,
            )
    expected = (
        EmbeddingErrorCode.INPUT_INVALID
        if failure == "long_unicode"
        else EmbeddingErrorCode.CONFIGURATION_INVALID
        if failure == "redirect"
        else EmbeddingErrorCode.RESPONSE_INVALID
    )
    assert caught.value.code == expected
    assert len(calls) == (0 if failure == "long_unicode" else 1)
    assert caught.value.__context__ is None
