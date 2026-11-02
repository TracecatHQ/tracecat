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


async def test_ollama_rejects_a_different_model(monkeypatch):
    configuration = configuration_for("ollama")
    payload = wire_response(configuration)
    payload["model"] = "unrelated-model"
    stub = StubProvider(httpx.Response(200, json=payload))
    monkeypatch.setattr(client_module, "create_outbound_http_client", stub.http)
    await stub.rejects(configuration, credential_for("ollama"), "RESPONSE_INVALID")


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("redirect", [False, True])
async def test_both_http_paths_reject_redirects_and_oversized_responses(
    provider, redirect, monkeypatch
):
    configuration = configuration_for(provider)
    response = (
        httpx.Response(302, headers={"Location": "https://other.test/embeddings"})
        if redirect
        else httpx.Response(200, content=b" " * 4_000_001)
    )
    stub = StubProvider(response)
    monkeypatch.setattr(client_module, "create_outbound_http_client", stub.http)
    code = "CONFIGURATION_INVALID" if redirect else "RESPONSE_INVALID"
    await stub.rejects(configuration, credential_for(provider), code)
    assert len(stub.calls) == 1
