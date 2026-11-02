"""Shared embedding fixtures and a recording transport for the real client."""

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
import pytest

from tracecat.search.embeddings.catalog import default_model
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.self_hosted import select_self_hosted_model
from tracecat.search.embeddings.types import (
    EmbeddingError,
    PinnedConfiguration,
    ResolvedCredential,
)
from tracecat.search.types import EmbeddingInput, EmbeddingRequest, SearchScope


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


@dataclass
class StubProvider:
    """Supply a response or handler and record calls to the real embedding client."""

    handler: httpx.Response | Callable[[httpx.Request], Awaitable[httpx.Response]]
    calls: list[httpx.Request] = field(default_factory=list)

    async def send(self, request):
        self.calls.append(request)
        return (
            self.handler
            if isinstance(self.handler, httpx.Response)
            else await self.handler(request)
        )

    def http(self, **kwargs):
        # Production self-hosted calls use an origin-bound client. Unit tests inject
        # this transport at that boundary; integration tests use the real policy.
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.send), follow_redirects=True
        )

    async def embed(self, configuration, credential, request=None, *, timeout=30.0):
        async with self.http() as http:
            return await EmbeddingClient(http, timeout=timeout).embed(
                configuration,
                credential,
                request if request is not None else request_for(configuration),
            )

    async def rejects(
        self, configuration, credential, code, request=None, *, timeout=30.0
    ):
        with pytest.raises(EmbeddingError) as caught:
            await self.embed(configuration, credential, request, timeout=timeout)
        error = caught.value
        assert error.code == code
        assert error.__context__ is None and error.__cause__ is None
        assert "private" not in str(error)
        return error


def configuration_for(provider, model=None):
    if provider in {"ollama", "vllm"}:
        model = model or (
            "all-minilm:latest"
            if provider == "ollama"
            else "sentence-transformers/all-MiniLM-L6-v2"
        )
        spec = select_self_hosted_model(
            provider, [model], {f"{provider.upper()}_BASE_URL": "http://models.test/v1"}
        )
        assert spec is not None
    else:
        spec = default_model(provider, "us-east-1")
    return PinnedConfiguration(2, spec, uuid.uuid4(), "default")


def credential_for(provider, key="synthetic-key"):
    if provider == "bedrock":
        return ResolvedCredential(
            {"AWS_REGION": "us-east-1", "AWS_BEARER_TOKEN_BEDROCK": key}
        )
    return ResolvedCredential({f"{provider.upper()}_API_KEY": key})


def wire_response(configuration, vectors=None, *, tokens=4):
    """Representative API responses; the decoder under test never builds fixtures."""
    if vectors is None:
        vectors = [[float(i + 1)] * configuration.spec.dimensions for i in range(2)]
    provider, model = configuration.spec.provider, configuration.spec.model
    if provider == "gemini":
        return {
            "embeddings": [{"values": v} for v in vectors],
            "usageMetadata": {"promptTokenCount": tokens},
        }
    if provider == "bedrock":
        return {"embedding": vectors[0], "inputTextTokenCount": tokens}
    if provider == "ollama":
        return {
            "model": model if ":" in model else model + ":latest",
            "embeddings": vectors,
            "prompt_eval_count": tokens,
        }
    return {
        "model": model,
        "data": [
            {"index": i, "embedding": v} for i, v in reversed(list(enumerate(vectors)))
        ],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }
