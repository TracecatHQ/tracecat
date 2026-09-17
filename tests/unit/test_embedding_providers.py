"""Provider wire contracts and explicit Bedrock authentication."""

import hashlib
import uuid
from dataclasses import replace

import httpx
import orjson
import pytest
from pydantic import SecretStr

from tracecat.search.embeddings import bedrock
from tracecat.search.embeddings.catalog import (
    default_model,
    recipe_revision,
    token_counter,
)
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
    ResolvedCredential,
)
from tracecat.search.types import EmbeddingInput, EmbeddingRequest, SearchScope


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["gemini", "bedrock"])
@pytest.mark.parametrize("invalid", [False, True])
async def test_provider_payload_and_validated_vectors(provider, invalid):
    spec = default_model(provider, "us-east-1")
    texts = (
        ("summary: alpha", "summary: beta")
        if provider == "gemini"
        else ("summary: alpha",)
    )
    config = PinnedConfiguration(1, spec, uuid.uuid4(), "default")
    request = EmbeddingRequest(
        SearchScope(uuid.uuid4(), uuid.uuid4()),
        1,
        spec.dimensions,
        tuple(
            EmbeddingInput(i, hashlib.sha256(text.encode()).hexdigest(), text)
            for i, text in enumerate(texts)
        ),
    )
    credential = ResolvedCredential(
        SecretStr("synthetic-key"),
        b"fingerprint",
        {
            "AWS_REGION": "us-east-1",
            "AWS_BEARER_TOKEN_BEDROCK": "synthetic-key",
        },
    )

    async def handler(http_request):
        body = orjson.loads(http_request.content)
        dimensions = 1 if invalid else spec.dimensions
        if provider == "gemini":
            assert http_request.headers["x-goog-api-key"] == "synthetic-key"
            assert [r["content"]["parts"][0]["text"] for r in body["requests"]] == list(
                texts
            )
            assert all(r["taskType"] == "SEMANTIC_SIMILARITY" for r in body["requests"])
            assert all(r["outputDimensionality"] == 3072 for r in body["requests"])
            return httpx.Response(
                200,
                json={
                    "embeddings": [
                        {"values": [float(i + 1)] * dimensions} for i in range(2)
                    ]
                },
            )
        assert http_request.headers["authorization"] == "Bearer synthetic-key"
        assert body == {"inputText": texts[0], "dimensions": 1024, "normalize": True}
        return httpx.Response(
            200, json={"embedding": [1.0] * dimensions, "inputTextTokenCount": 4}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = EmbeddingClient(http)
        if invalid:
            with pytest.raises(EmbeddingError) as caught:
                await client.embed(config, credential, request)
            assert caught.value.code == EmbeddingErrorCode.RESPONSE_INVALID
        else:
            result = await client.embed(config, credential, request)
            assert [r.ordinal for r in result.results] == list(range(len(texts)))
            assert [r.vector[0] for r in result.results] == [
                float(i + 1) for i in range(len(texts))
            ]
            assert result.prompt_tokens == (None if provider == "gemini" else 4)


@pytest.mark.anyio
async def test_bedrock_static_keys_sign_exact_request_without_ambient_credentials(
    monkeypatch,
):
    def no_session():
        pytest.fail("Static credentials must not resolve ambient AWS credentials")

    monkeypatch.setattr(bedrock.boto3, "Session", no_session)
    headers = await bedrock.request_headers(
        {
            "AWS_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "synthetic-access",
            "AWS_SECRET_ACCESS_KEY": "synthetic-secret",
            "AWS_SESSION_TOKEN": "synthetic-session",
        },
        SearchScope(uuid.uuid4(), uuid.uuid4()),
        default_model("bedrock", "us-east-1").endpoint,
        b'{"inputText":"alpha"}',
    )
    assert headers["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=synthetic-access/"
    )
    assert "/us-east-1/bedrock/aws4_request" in headers["Authorization"]
    assert headers["X-Amz-Security-Token"] == "synthetic-session"


@pytest.mark.anyio
async def test_bedrock_missing_credentials_never_uses_ambient_keys(monkeypatch):
    def no_session():
        pytest.fail("Missing credentials must not resolve ambient AWS credentials")

    monkeypatch.setattr(bedrock.boto3, "Session", no_session)
    with pytest.raises(EmbeddingError) as caught:
        await bedrock.request_headers(
            {"AWS_REGION": "us-east-1"},
            SearchScope(uuid.uuid4(), uuid.uuid4()),
            default_model("bedrock", "us-east-1").endpoint,
            b"{}",
        )
    assert caught.value.code == EmbeddingErrorCode.CREDENTIAL_INVALID


def test_non_openai_budget_counts_complete_utf8_input():
    for provider in ("gemini", "bedrock"):
        counter = token_counter(default_model(provider, "us-east-1"))
        assert counter.identity == "utf8-bytes:v1"
        assert counter.count_tokens("description: 日本語 🙂") == len(
            "description: 日本語 🙂".encode()
        )


@pytest.mark.anyio
async def test_bedrock_assumed_role_uses_workspace_external_id(monkeypatch):
    scope = SearchScope(uuid.uuid4(), uuid.uuid4())
    calls = []

    class STS:
        def assume_role(self, **kwargs):
            calls.append(kwargs)
            return {
                "Credentials": {
                    "AccessKeyId": "assumed-access",
                    "SecretAccessKey": "assumed-secret",
                    "SessionToken": "assumed-session",
                }
            }

        def close(self):
            calls.append("closed")

    class Session:
        def client(self, service, **kwargs):
            assert service == "sts"
            return STS()

    monkeypatch.setattr(bedrock.boto3, "Session", Session)
    headers = await bedrock.request_headers(
        {
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/synthetic-embedding",
            "AWS_REGION": "us-east-1",
        },
        scope,
        default_model("bedrock", "us-east-1").endpoint,
        b"{}",
    )
    assert calls[0]["ExternalId"] == bedrock.build_workspace_external_id(
        scope.workspace_id
    )
    assert calls[-1] == "closed"
    assert "Credential=assumed-access/" in headers["Authorization"]
    assert headers["X-Amz-Security-Token"] == "assumed-session"


def test_recipe_revision_excludes_operational_batch_limits():
    spec = default_model("openai")
    assert recipe_revision(spec) == recipe_revision(
        replace(spec, batch_size_limit=1, batch_token_limit=1000)
    )
    assert recipe_revision(spec) != recipe_revision(
        replace(spec, tokenizer="next-tokenizer")
    )
    assert recipe_revision(spec) != recipe_revision(replace(spec, recipe_version=2))
