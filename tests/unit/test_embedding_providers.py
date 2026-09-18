"""Provider wire contracts and explicit Bedrock authentication."""

import asyncio
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

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
async def test_provider_payload_and_validated_vectors(provider, model, monkeypatch):
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
        vectors = [[float(i + 1)] * spec.dimensions for i in range(len(texts))]
        return httpx.Response(200, json=wire_response(configuration, vectors))

    stub = StubProvider(handler)
    monkeypatch.setattr(client_module, "create_outbound_http_client", stub.http)
    result = await stub.embed(configuration, credential_for(provider), request)
    assert [r.ordinal for r in result.results] == [i.ordinal for i in request.items]
    assert [r.input_hash for r in result.results] == [
        i.input_hash for i in request.items
    ]
    assert all(r.config_version == request.config_version for r in result.results)
    assert [r.vector[0] for r in result.results] == list(range(1, len(texts) + 1))
    assert all(len(r.vector) == spec.dimensions for r in result.results)
    assert result.prompt_tokens == result.total_tokens == 4
    assert len(stub.calls) == 1 and str(stub.calls[0].url) == spec.endpoint


@pytest.mark.parametrize("provider", ["gemini", "ollama"])
async def test_missing_usage_stays_unknown(provider, monkeypatch):
    configuration = configuration_for(provider)
    body = wire_response(configuration, [[1.0] * configuration.spec.dimensions])
    body.pop("usageMetadata" if provider == "gemini" else "prompt_eval_count")

    stub = StubProvider(httpx.Response(200, json=body))
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


@pytest.mark.parametrize("provider", ["gemini", "bedrock", "ollama", "vllm"])
async def test_utf8_budget_rejects_oversized_input_before_http(provider):
    configuration = configuration_for(provider)
    text = "界" * (configuration.spec.input_token_limit // 3 + 1)
    counter = token_counter(configuration.spec)
    assert counter.identity == "utf8-bytes:v1"
    assert counter.count_tokens(text) == len(text.encode())

    async def no_http(request):
        pytest.fail("Oversized input reached provider")

    await StubProvider(no_http).rejects(
        configuration,
        credential_for(provider),
        "INPUT_INVALID",
        request_for(configuration, (text,)),
    )


@pytest.fixture
def aws_session(monkeypatch):
    session = Mock()
    monkeypatch.setattr(bedrock.boto3, "Session", Mock(return_value=session))
    session.client.return_value.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "assumed-access",
            "SecretAccessKey": "assumed-secret",
            "SessionToken": "assumed-session",
            "Expiration": datetime.now(UTC) + timedelta(hours=1),
        }
    }
    bedrock._assume_role.cache_clear()
    yield session
    bedrock._assume_role.cache_clear()


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
    aws_session, region, session_name, expected_name
):
    scope = SearchScope(uuid.uuid4(), uuid.uuid4())
    sts = aws_session.client.return_value
    values = {
        "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/synthetic-embedding",
        "AWS_REGION": region,
        **({"AWS_ROLE_SESSION_NAME": session_name} if session_name is not None else {}),
    }
    endpoint = default_model("bedrock", region).endpoint
    headers = await bedrock.request_headers(values, scope, endpoint, b"{}")
    changed = await bedrock.request_headers(
        values, scope, endpoint, b'{"inputText":"new"}'
    )
    sts.assume_role.assert_called_once()
    assert headers["Authorization"] != changed["Authorization"]
    assert aws_session.client.call_args.args == ("sts",)
    assert aws_session.client.call_args.kwargs["region_name"] == region
    options = aws_session.client.call_args.kwargs["config"]
    assert options.connect_timeout == 5 and options.read_timeout == 10
    assert options.retries == {"total_max_attempts": 1}
    assert sts.assume_role.call_args.kwargs["ExternalId"] == scope.workspace_id.hex
    assert sts.assume_role.call_args.kwargs["RoleSessionName"] == expected_name
    sts.close.assert_called_once()
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
    aws_session, aws_error, expected_code, retryable
):
    if aws_error == "transport":
        error = EndpointConnectionError(endpoint_url="https://private.example.com")
    elif aws_error == "local_validation":
        error = ParamValidationError(report="synthetic-private-role")
    else:
        error = ClientError(
            {"Error": {"Code": aws_error, "Message": "synthetic-private-role"}},
            "AssumeRole",
        )
    aws_session.client.return_value.assume_role.side_effect = error
    configuration = configuration_for("bedrock")
    credential = ResolvedCredential(
        {
            "AWS_REGION": "us-east-1",
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/synthetic-role",
        }
    )

    async def no_inference(request):
        pytest.fail("Failed role assumption must not call the embedding endpoint")

    error = await StubProvider(no_inference).rejects(
        configuration, credential, expected_code, request_for(configuration, ("hello",))
    )
    assert error.retryable is retryable


async def test_concurrent_chunks_share_one_sts_call(aws_session):
    entered, release = threading.Event(), threading.Event()
    sts = aws_session.client.return_value
    response = sts.assume_role.return_value

    def assume(**kwargs):
        entered.set()
        assert release.wait(3)
        return response

    sts.assume_role.side_effect = assume
    args = (
        "synthetic-role",
        "us-east-1",
        "search",
        SearchScope(uuid.uuid4(), uuid.uuid4()),
    )
    tasks = [
        asyncio.create_task(asyncio.to_thread(bedrock._assume_role, *args))
        for _ in range(8)
    ]
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        # Keep STS in flight while the other callers reach the same cache key.
        await asyncio.sleep(0.05)
    finally:
        release.set()
    results = await asyncio.gather(*tasks)
    sts.assume_role.assert_called_once()
    assert all(result is results[0] for result in results)
    assert "assumed-secret" not in repr(results[0])


@pytest.mark.parametrize(
    "field", ["role", "region", "session", "organization_id", "workspace_id"]
)
def test_role_cache_separates_authentication_contexts(aws_session, field):
    scope = SearchScope(uuid.uuid4(), uuid.uuid4())
    bedrock._assume_role("role", "us-east-1", "session", scope)
    other_scope = (
        replace(scope, **{field: uuid.uuid4()}) if field.endswith("_id") else scope
    )
    bedrock._assume_role(
        "other-role" if field == "role" else "role",
        "us-west-2" if field == "region" else "us-east-1",
        "other-session" if field == "session" else "session",
        other_scope,
    )
    assert aws_session.client.return_value.assume_role.call_count == 2


def test_role_cache_refresh_failure_and_recovery(aws_session, monkeypatch):
    sts = aws_session.client.return_value
    credentials = sts.assume_role.return_value["Credentials"]
    expiry = credentials["Expiration"].timestamp()
    now = [expiry - 3600]
    monkeypatch.setattr(bedrock, "time", lambda: now[0])
    args = ("role", "us-east-1", "session", SearchScope(uuid.uuid4(), uuid.uuid4()))
    first = bedrock._assume_role(*args)
    now[0] = expiry - 301
    assert bedrock._assume_role(*args) is first
    sts.assume_role.assert_called_once()
    now[0] = expiry - 300
    sts.assume_role.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "AssumeRole"
    )
    with pytest.raises(ClientError):
        bedrock._assume_role(*args)  # Never serve stale credentials on refresh failure.
    sts.assume_role.side_effect = None
    credentials["Expiration"] = datetime.fromtimestamp(now[0] + 3600, UTC)
    assert bedrock._assume_role(*args) is not first
    assert sts.assume_role.call_count == 3
    bedrock._assume_role.cache_clear()
    credentials["Expiration"] = datetime.fromtimestamp(now[0] + 30, UTC)
    with pytest.raises(EmbeddingError) as caught:
        bedrock._assume_role(*args)
    assert caught.value.code == EmbeddingErrorCode.UNAVAILABLE
