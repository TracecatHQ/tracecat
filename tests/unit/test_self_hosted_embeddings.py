"""Self-hosted selection, native Ollama, and OpenAI-compatible vLLM contracts."""

import httpx
import pytest

from tests.embedding_helpers import (
    StubProvider,
    configuration_for,
    credential_for,
    request_for,
    wire_response,
)
from tracecat.search.embeddings import client as client_module
from tracecat.search.embeddings.self_hosted import select_self_hosted_model
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
)

pytestmark = pytest.mark.anyio

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


@pytest.mark.parametrize("provider", ["ollama", "vllm"])
async def test_servers_without_api_keys(provider, monkeypatch):
    configuration = configuration_for(provider)

    async def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, json=wire_response(configuration, [[1.0] * 384]))

    stub = StubProvider(handler)
    monkeypatch.setattr(client_module, "create_outbound_http_client", stub.http)
    await stub.embed(
        configuration,
        credential_for(provider, ""),
        request_for(configuration, ("hello",)),
    )


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
    configuration = configuration_for(provider, model)
    text = "界" * 81 if failure == "long_unicode" else "A document"
    request = request_for(configuration, (text,))

    async def handler(http_request):
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
        payload = wire_response(configuration, vectors)
        payload["model"] = response_model
        return httpx.Response(200, json=payload)

    stub = StubProvider(handler)
    monkeypatch.setattr(client_module, "create_outbound_http_client", stub.http)
    with pytest.raises(EmbeddingError) as caught:
        await stub.embed(configuration, credential_for(provider), request)
    expected = (
        EmbeddingErrorCode.INPUT_INVALID
        if failure == "long_unicode"
        else EmbeddingErrorCode.CONFIGURATION_INVALID
        if failure == "redirect"
        else EmbeddingErrorCode.RESPONSE_INVALID
    )
    assert caught.value.code == expected
    assert len(stub.calls) == (0 if failure == "long_unicode" else 1)
    assert caught.value.__context__ is None
