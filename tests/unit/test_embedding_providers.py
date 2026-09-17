"""Provider wire contracts and explicit Bedrock authentication."""

import uuid
from dataclasses import replace

import httpx
import orjson
import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ParamValidationError,
)

from tests.embedding_helpers import (
    StubProvider,
    configuration_for,
    credential_for,
    request_for,
    wire_response,
)
from tracecat.search.embeddings import bedrock
from tracecat.search.embeddings import client as client_module
from tracecat.search.embeddings.catalog import (
    default_model,
    recipe_revision,
    token_counter,
)
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    ResolvedCredential,
)
from tracecat.search.types import SearchScope

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", None),
        ("gemini", None),
        ("bedrock", None),
        ("ollama", "all-minilm"),
        ("ollama", "all-minilm:latest"),
        ("ollama", "all-minilm:22m"),
        ("vllm", None),
    ],
)
@pytest.mark.parametrize("invalid", [False, True])
async def test_provider_payload_and_validated_vectors(
    provider, model, invalid, monkeypatch
):
    configuration = configuration_for(provider, model)
    spec = configuration.spec
    texts = (
        ("summary: alpha",)
        if provider == "bedrock"
        else ("summary: alpha", "summary: beta")
    )
    request = request_for(configuration, texts)

    async def handler(http_request):
        body = orjson.loads(http_request.content)
        if provider == "gemini":
            assert http_request.headers["x-goog-api-key"] == "synthetic-key"
            assert body == {
                "requests": [
                    {
                        "model": f"models/{spec.model}",
                        "content": {"parts": [{"text": text}]},
                        "taskType": "SEMANTIC_SIMILARITY",
                        "outputDimensionality": 3072,
                    }
                    for text in texts
                ]
            }
        else:
            assert http_request.headers["authorization"] == "Bearer synthetic-key"
            if provider == "bedrock":
                assert body == {
                    "inputText": texts[0],
                    "dimensions": 1024,
                    "normalize": True,
                }
            else:
                assert body == {
                    "model": spec.model,
                    "input": list(texts),
                    **(
                        {"truncate": False}
                        if provider == "ollama"
                        else {"encoding_format": "float"}
                    ),
                }
        vectors = [
            [float(i + 1)] * (1 if invalid else spec.dimensions)
            for i in range(len(texts))
        ]
        return httpx.Response(200, json=wire_response(configuration, vectors))

    stub = StubProvider(handler)
    monkeypatch.setattr(client_module, "create_outbound_http_client", stub.http)
    if invalid:
        with pytest.raises(EmbeddingError) as caught:
            await stub.embed(configuration, credential_for(provider), request)
        assert caught.value.code == EmbeddingErrorCode.RESPONSE_INVALID
    else:
        result = await stub.embed(configuration, credential_for(provider), request)
        assert [r.ordinal for r in result.results] == [i.ordinal for i in request.items]
        assert [r.vector[0] for r in result.results] == list(range(1, len(texts) + 1))
        assert result.prompt_tokens == result.total_tokens == 4
    assert len(stub.calls) == 1 and str(stub.calls[0].url) == spec.endpoint


@pytest.mark.parametrize("provider", ["gemini", "ollama"])
async def test_missing_usage_stays_unknown(provider, monkeypatch):
    configuration = configuration_for(provider)
    body = wire_response(configuration, [[1.0] * configuration.spec.dimensions])
    body.pop("usageMetadata" if provider == "gemini" else "prompt_eval_count")

    async def handler(request):
        return httpx.Response(200, json=body)

    stub = StubProvider(handler)
    monkeypatch.setattr(client_module, "create_outbound_http_client", stub.http)
    result = await stub.embed(
        configuration, credential_for(provider), request_for(configuration, ("hello",))
    )
    assert result.prompt_tokens is None and result.total_tokens is None


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


@pytest.mark.parametrize(
    "region,session_name,expected_name",
    [
        ("us-east-1", "  synthetic-session  ", "synthetic-session"),
        ("us-gov-west-1", "   ", "tracecat-search"),
        ("cn-north-1", "", "tracecat-search"),
        ("us-east-2", "synthetic-session", "synthetic-session"),
        ("us-west-2", None, "tracecat-search"),
    ],
)
async def test_bedrock_assumed_role_uses_workspace_external_id(
    monkeypatch, region, session_name, expected_name
):
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
            assert kwargs["region_name"] == region
            return STS()

    monkeypatch.setattr(bedrock.boto3, "Session", Session)
    headers = await bedrock.request_headers(
        {
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/synthetic-embedding",
            "AWS_REGION": region,
            **(
                {"AWS_ROLE_SESSION_NAME": session_name}
                if session_name is not None
                else {}
            ),
        },
        scope,
        default_model("bedrock", region).endpoint,
        b"{}",
    )
    assert calls[0]["ExternalId"] == bedrock.build_workspace_external_id(
        scope.workspace_id
    )
    assert calls[0]["RoleSessionName"] == expected_name
    assert calls[-1] == "closed"
    assert f"/{region}/bedrock/aws4_request" in headers["Authorization"]
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


@pytest.mark.parametrize(
    "aws_error, expected_code, retryable",
    [
        ("AccessDenied", "CREDENTIAL_INVALID", False),
        ("ValidationError", "CREDENTIAL_INVALID", False),
        ("InvalidClientTokenId", "CREDENTIAL_INVALID", False),
        ("Throttling", "RATE_LIMITED", True),
        ("ThrottlingException", "RATE_LIMITED", True),
        ("InternalFailure", "UNAVAILABLE", True),
        ("ExpiredToken", "UNAVAILABLE", True),
        ("unknown", "UNAVAILABLE", True),
        ("transport", "UNAVAILABLE", True),
        ("local_validation", "CREDENTIAL_INVALID", False),
    ],
)
async def test_sts_failures_are_classified_without_inference_or_secret_leaks(
    monkeypatch, aws_error, expected_code, retryable
):
    class STS:
        def assume_role(self, **kwargs):
            if aws_error == "transport":
                raise EndpointConnectionError(
                    endpoint_url="https://synthetic-private.example.com"
                )
            if aws_error == "local_validation":
                raise ParamValidationError(report="synthetic-private-role")
            raise ClientError(
                {"Error": {"Code": aws_error, "Message": "synthetic-private-role"}},
                "AssumeRole",
            )

        def close(self):
            pass

    class Session:
        def client(self, service, **kwargs):
            assert service == "sts"
            return STS()

    monkeypatch.setattr(bedrock.boto3, "Session", Session)
    configuration = configuration_for("bedrock")
    credential = ResolvedCredential(
        {
            "AWS_REGION": "us-east-1",
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/synthetic-role",
        }
    )

    async def no_inference(request):
        pytest.fail("Failed role assumption must not call the embedding endpoint")

    with pytest.raises(EmbeddingError) as caught:
        await StubProvider(no_inference).embed(
            configuration, credential, request_for(configuration, ("hello",))
        )
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "synthetic-private" not in str(caught.value)
