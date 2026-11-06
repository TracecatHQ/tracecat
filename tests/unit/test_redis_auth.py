"""Unit tests for Redis IAM token login. No cloud calls."""

from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import sys
import time
from types import TracebackType
from typing import Self
from urllib.parse import parse_qs

import boto3
import orjson
import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from redis.asyncio.connection import Connection, SSLConnection
from redis.auth.token import TokenInterface
from redis.exceptions import AuthenticationError, ConnectionError, ResponseError

from tracecat import config
from tracecat.redis import auth
from tracecat.redis.auth import (
    AWS_IAM_TOKEN_CACHE_SECONDS,
    AwsIamCredentialProvider,
    AzureEntraCredentialProvider,
    IamSSLConnection,
    RedisCredentialUnavailableError,
    RedisIamAuthUnavailableError,
    aws_iam_signing_url,
    close_redis_client,
    create_redis_client,
)
from tracecat.redis.types import (
    RedisAuthMode,
    RedisAwsIamAuth,
    RedisAzureEntraAuth,
    RedisPasswordAuth,
)


def _jwt(claims: dict[str, object]) -> str:
    def part(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(orjson.dumps(value)).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.signature"


AWS_AUTH = RedisAwsIamAuth(
    user_id="app-user", replication_group_id="tracecat-redis", region="us-east-1"
)


def _static_session() -> boto3.Session:
    return boto3.Session(
        aws_access_key_id="AKIDEXAMPLE",
        aws_secret_access_key="secret",
        region_name="us-east-1",
    )


# --- AWS ---


def test_aws_signing_url_targets_replication_group() -> None:
    assert (
        aws_iam_signing_url(AWS_AUTH)
        == "https://tracecat-redis/?Action=connect&User=app-user"
    )


@pytest.mark.parametrize(
    "group",
    ["master.tracecat-redis.abc123.use1.cache.amazonaws.com", "Tracecat", ""],
)
def test_aws_auth_rejects_endpoints_and_bad_group_ids(group: str) -> None:
    with pytest.raises(ValueError, match="replication group ID"):
        RedisAwsIamAuth(
            user_id="app-user", replication_group_id=group, region="us-east-1"
        )


@pytest.mark.parametrize(("user_id", "region"), [("", "us-east-1"), ("app-user", "")])
def test_aws_auth_requires_user_and_region(user_id: str, region: str) -> None:
    with pytest.raises(ValueError, match="user ID and a region"):
        RedisAwsIamAuth(
            user_id=user_id, replication_group_id="tracecat-redis", region=region
        )


def test_aws_token_is_presigned_connect_url_without_scheme() -> None:
    provider = AwsIamCredentialProvider(
        AWS_AUTH,
        session=_static_session(),
    )

    user, token = provider.get_credentials()

    assert user == "app-user"
    assert token.startswith("tracecat-redis/?")
    assert "https://" not in token
    query = parse_qs(token.split("?", 1)[1])
    assert query["Action"] == ["connect"]
    assert query["User"] == ["app-user"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["900"]
    assert query["X-Amz-Credential"][0].startswith("AKIDEXAMPLE/")
    assert query["X-Amz-Credential"][0].endswith("/us-east-1/elasticache/aws4_request")
    assert "X-Amz-Signature" in query


def test_aws_token_is_cached_then_resigned(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    provider = AwsIamCredentialProvider(
        AWS_AUTH,
        session=_static_session(),
        clock=lambda: now[0],
    )
    signed: list[str] = []

    def fake_sign() -> str:
        signed.append(f"token-{len(signed)}")
        return signed[-1]

    monkeypatch.setattr(provider, "_sign", fake_sign)

    assert provider.get_credentials() == ("app-user", "token-0")
    now[0] += AWS_IAM_TOKEN_CACHE_SECONDS - 1
    assert provider.get_credentials() == ("app-user", "token-0")
    now[0] += 1
    assert provider.get_credentials() == ("app-user", "token-1")
    assert len(signed) == 2


@pytest.mark.anyio
async def test_aws_async_credentials_sign_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AwsIamCredentialProvider(
        AWS_AUTH,
        session=_static_session(),
    )
    calls = 0

    def fake_sign() -> str:
        nonlocal calls
        calls += 1
        return "token"

    monkeypatch.setattr(provider, "_sign", fake_sign)
    assert calls == 0

    assert await provider.get_credentials_async() == ("app-user", "token")
    assert await provider.get_credentials_async() == ("app-user", "token")
    assert calls == 1


def test_aws_missing_credentials_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = boto3.Session(region_name="us-east-1")
    provider = AwsIamCredentialProvider(
        AWS_AUTH,
        session=session,
    )
    monkeypatch.setattr(session, "get_credentials", lambda: None)

    with pytest.raises(RedisCredentialUnavailableError) as exc_info:
        provider.get_credentials()

    # A ConnectionError, so callers retry it and redis-py closes the socket.
    assert isinstance(exc_info.value, ConnectionError)
    assert "AWS credentials are unavailable" in str(exc_info.value.__cause__)


# --- Handshake retry classification ---


@pytest.fixture
def handshake(monkeypatch: pytest.MonkeyPatch) -> list[BaseException | None]:
    """Script SSLConnection.connect_check_health outcomes, in order."""
    outcomes: list[BaseException | None] = []

    async def fake_connect(self, check_health=True, retry_socket_connect=True):
        del self, check_health, retry_socket_connect
        if (outcome := outcomes.pop(0)) is not None:
            raise outcome

    monkeypatch.setattr(SSLConnection, "connect_check_health", fake_connect)
    monkeypatch.setattr(auth, "IAM_AUTH_BACKOFF_SECONDS", 0)
    return outcomes


@pytest.mark.anyio
async def test_iam_unavailable_is_retried(
    handshake: list[BaseException | None],
) -> None:
    unavailable = ResponseError("IAM Authentication service is not available.")
    handshake.extend([unavailable, unavailable, None])

    await IamSSLConnection(host="localhost").connect()

    assert handshake == []


@pytest.mark.anyio
async def test_iam_unavailable_becomes_connection_error_after_bounded_retries(
    handshake: list[BaseException | None],
) -> None:
    unavailable = ResponseError("IAM Authentication service is not available.")
    handshake.extend([unavailable] * auth.IAM_AUTH_MAX_ATTEMPTS)

    with pytest.raises(RedisIamAuthUnavailableError) as exc_info:
        await IamSSLConnection(host="localhost").connect()

    assert isinstance(exc_info.value, ConnectionError)
    assert handshake == []


@pytest.mark.anyio
async def test_rejected_token_is_not_retried(
    handshake: list[BaseException | None],
) -> None:
    handshake.extend(
        [AuthenticationError("invalid username-password pair"), None, None]
    )

    with pytest.raises(AuthenticationError):
        await IamSSLConnection(host="localhost").connect()

    assert len(handshake) == 2


@pytest.mark.anyio
async def test_unexpected_handshake_error_closes_the_socket(
    handshake: list[BaseException | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing but a completed login leaves the socket open for the pool."""
    handshake.append(ValueError("unexpected"))
    closed: list[bool] = []

    async def record_disconnect(self: IamSSLConnection, nowait: bool = False) -> None:
        del self
        closed.append(nowait)

    monkeypatch.setattr(IamSSLConnection, "disconnect", record_disconnect)

    with pytest.raises(ValueError, match="unexpected"):
        await IamSSLConnection(host="localhost").connect()

    assert closed == [True]


# --- Azure ---


class FakeAzureCredential:
    """Returns scripted tokens (or raises scripted errors) in order."""

    def __init__(self, outcomes: list[AccessToken | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.closed = False

    async def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: object,
    ) -> AccessToken:
        del claims, tenant_id, enable_cae, kwargs
        assert scopes == ("https://redis.azure.com/.default",)
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.close()


def _access_token(oid: str, expires_in: float) -> AccessToken:
    expires_on = int(time.time() + expires_in)
    return AccessToken(_jwt({"oid": oid, "exp": expires_on}), expires_on)


def _azure_provider(credential: FakeAzureCredential) -> AzureEntraCredentialProvider:
    return AzureEntraCredentialProvider(
        RedisAzureEntraAuth(), credential_factory=lambda: credential
    )


@pytest.mark.anyio
async def test_azure_credentials_use_token_oid_and_cache() -> None:
    token = _access_token("object-id-1", 3600)
    credential = FakeAzureCredential([token])
    provider = _azure_provider(credential)
    try:
        assert await provider.get_credentials_async() == ("object-id-1", token.token)
        assert await provider.get_credentials_async() == ("object-id-1", token.token)
        assert credential.calls == 1
        assert provider.is_streaming()
    finally:
        await provider.aclose()
    assert credential.closed


@pytest.mark.anyio
async def test_azure_refresh_publishes_new_token_for_reauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "AZURE_REFRESH_RETRY_SECONDS", 0.01)
    # First token reaches the refresh lead about 1 s from now, so the loop
    # refreshes then; the second is long-lived.
    first = _access_token("object-id-1", auth.AZURE_REFRESH_LEAD_SECONDS + 1)
    second = _access_token("object-id-1", 3600)
    credential = FakeAzureCredential([first, second])
    provider = _azure_provider(credential)
    published: asyncio.Queue[TokenInterface] = asyncio.Queue()

    async def on_next(token: TokenInterface) -> None:
        await published.put(token)

    provider.on_next(on_next)
    try:
        assert await provider.get_credentials_async() == ("object-id-1", first.token)
        token = await asyncio.wait_for(published.get(), timeout=2)
        assert token.get_value() == second.token
        assert token.try_get("oid") == "object-id-1"
        assert await provider.get_credentials_async() == ("object-id-1", second.token)
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("token endpoint"),
        AccessToken("opaque-not-a-jwt", int(time.time()) + 3600),
    ],
    ids=["fetch-error", "malformed-token"],
)
async def test_azure_refresh_failure_keeps_retrying(
    monkeypatch: pytest.MonkeyPatch, failure: AccessToken | Exception
) -> None:
    """A failed or unusable refresh keeps the old token and tries again."""
    monkeypatch.setattr(auth, "AZURE_REFRESH_RETRY_SECONDS", 0.01)
    first = _access_token("object-id-1", auth.AZURE_REFRESH_LEAD_SECONDS + 1)
    second = _access_token("object-id-1", 3600)
    credential = FakeAzureCredential([first, failure, second])
    provider = _azure_provider(credential)
    published: asyncio.Queue[TokenInterface] = asyncio.Queue()

    async def on_next(token: TokenInterface) -> None:
        await published.put(token)

    provider.on_next(on_next)
    try:
        await provider.get_credentials_async()
        token = await asyncio.wait_for(published.get(), timeout=2)
        assert token.get_value() == second.token
        assert credential.calls == 3
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_azure_reauth_that_hangs_is_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-AUTH stuck on a half-open socket must not hold the pool lock forever."""
    monkeypatch.setattr(auth, "AZURE_REAUTH_TIMEOUT_SECONDS", 0.05)
    provider = _azure_provider(FakeAzureCredential([]))
    cancelled = asyncio.Event()

    async def hang(token: TokenInterface) -> None:
        del token
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    provider.on_next(hang)
    await asyncio.wait_for(
        provider._publish(_access_token("object-id-1", 3600), "object-id-1"),
        timeout=2,
    )
    assert cancelled.is_set()


@pytest.mark.anyio
async def test_azure_credential_failure_is_a_connection_error() -> None:
    """An Entra outage must reach callers' Redis retries, not escape them."""
    provider = _azure_provider(
        FakeAzureCredential([ClientAuthenticationError("token endpoint down")])
    )
    try:
        with pytest.raises(RedisCredentialUnavailableError) as exc_info:
            await provider.get_credentials_async()
    finally:
        await provider.aclose()

    assert isinstance(exc_info.value, ConnectionError)
    assert isinstance(exc_info.value.__cause__, ClientAuthenticationError)


def test_azure_token_without_identity_claim_is_rejected() -> None:
    with pytest.raises(ValueError, match="no oid or sub"):
        auth._token_username(_jwt({"exp": 1}))
    with pytest.raises(ValueError, match="not a JWT"):
        auth._token_username("opaque")


# --- Client factory ---


@pytest.mark.anyio
async def test_password_mode_is_plain_from_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client = create_redis_client(
        "redis://user:pass@localhost:6379/0", auth=RedisPasswordAuth()
    )
    try:
        pool = client.connection_pool
        assert pool.connection_class is Connection
        assert pool.connection_kwargs["password"] == "pass"
        assert pool.connection_kwargs.get("credential_provider") is None
    finally:
        await close_redis_client(client)


@pytest.mark.anyio
async def test_aws_iam_mode_builds_tls_pool_with_provider() -> None:
    client = create_redis_client(
        "rediss://master.tracecat-redis.abc123.use1.cache.amazonaws.com:6379/0",
        auth=AWS_AUTH,
        decode_responses=True,
        max_connections=7,
    )
    try:
        pool = client.connection_pool
        assert pool.connection_class is IamSSLConnection
        assert pool.max_connections == 7
        assert pool.connection_kwargs["decode_responses"] is True
        assert isinstance(
            pool.connection_kwargs["credential_provider"], AwsIamCredentialProvider
        )
    finally:
        await close_redis_client(client)


AWS_HOST = "master.tracecat-redis.abc123.use1.cache.amazonaws.com"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        (
            "redis://master.tracecat-redis.abc123.use1.cache.amazonaws.com:6379",
            "requires TLS",
        ),
        ("rediss://collector.example.com:6379", "only sends tokens to hosts ending in"),
        ("rediss://cache.amazonaws.com.example.com:6379", "only sends tokens to hosts"),
        (
            f"rediss://{AWS_HOST}:6379?ssl_ca_data=-----BEGIN%20CERTIFICATE-----",
            "does not accept TLS options ssl_ca_data",
        ),
        (f"rediss://{AWS_HOST}:6379?ssl_ciphers=NULL", "ssl_ciphers"),
        (
            "rediss://default:secret@master.tracecat-redis.abc123.use1.cache.amazonaws.com:6379",
            "remove the username",
        ),
        (
            "rediss://master.tracecat-redis.abc123.use1.cache.amazonaws.com:6379?ssl_cert_reqs=none",
            "ssl_cert_reqs",
        ),
    ],
)
def test_aws_iam_mode_rejects_insecure_urls(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        create_redis_client(url, auth=AWS_AUTH)


@pytest.mark.parametrize("check_hostname", [False, 0, None])
def test_aws_iam_mode_rejects_disabled_hostname_check(check_hostname: object) -> None:
    with pytest.raises(ValueError, match="ssl_check_hostname"):
        create_redis_client(
            "rediss://master.tracecat-redis.abc123.use1.cache.amazonaws.com:6379",
            auth=AWS_AUTH,
            ssl_check_hostname=check_hostname,
        )


@pytest.mark.parametrize("option", ["ssl_ca_certs", "ssl_ca_data", "ssl_certfile"])
def test_aws_iam_mode_rejects_url_trust_material(option: str) -> None:
    with pytest.raises(ValueError, match=f"does not accept TLS options {option}"):
        create_redis_client(
            f"rediss://{AWS_HOST}:6379?{option}=/tmp/ca.pem", auth=AWS_AUTH
        )


@pytest.mark.parametrize(
    "host",
    ["tracecat.westus2.redis.azure.net", "tracecat.redis.cache.windows.net"],
)
def test_azure_entra_mode_accepts_azure_hosts(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    mode = RedisAuthMode.AZURE_ENTRA
    auth._validate_iam_url(f"rediss://{host}:10000", {"host": host}, mode)
    with pytest.raises(ValueError, match="redis.azure.net"):
        auth._validate_iam_url(f"rediss://{AWS_HOST}:6379", {"host": AWS_HOST}, mode)


# --- Parsing REDIS_AUTH from the environment ---


@pytest.fixture
def redis_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "REDIS_AUTH_MODE",
        "REDIS_AWS_IAM_USER_ID",
        "REDIS_AWS_IAM_REPLICATION_GROUP_ID",
        "REDIS_AWS_REGION",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "REDIS_AZURE_CLIENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_parse_redis_auth_defaults_to_password(redis_env: pytest.MonkeyPatch) -> None:
    assert config._parse_redis_auth() == RedisPasswordAuth()


def test_parse_redis_auth_aws(redis_env: pytest.MonkeyPatch) -> None:
    redis_env.setenv("REDIS_AUTH_MODE", " AWS_IAM ")
    redis_env.setenv("REDIS_AWS_IAM_USER_ID", "app-user")
    redis_env.setenv("REDIS_AWS_IAM_REPLICATION_GROUP_ID", "Tracecat-Redis")
    redis_env.setenv("AWS_DEFAULT_REGION", "eu-west-1")

    assert config._parse_redis_auth() == RedisAwsIamAuth(
        user_id="app-user", replication_group_id="tracecat-redis", region="eu-west-1"
    )


@pytest.mark.parametrize(
    ("client_id", "expected"), [(None, None), ("  ", None), ("cid", "cid")]
)
def test_parse_redis_auth_azure(
    redis_env: pytest.MonkeyPatch, client_id: str | None, expected: str | None
) -> None:
    redis_env.setenv("REDIS_AUTH_MODE", "azure_entra")
    if client_id is not None:
        redis_env.setenv("REDIS_AZURE_CLIENT_ID", client_id)

    assert config._parse_redis_auth() == RedisAzureEntraAuth(client_id=expected)


# --- Startup validation ---


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"REDIS_AUTH_MODE": "iam"}, "REDIS_AUTH_MODE must be one of"),
        (
            {"REDIS_AUTH_MODE": "aws_iam", "REDIS_URL": "rediss://cache:6379"},
            "requires REDIS_AWS_IAM_USER_ID, REDIS_AWS_IAM_REPLICATION_GROUP_ID",
        ),
        (
            {"REDIS_AUTH_MODE": "azure_entra", "REDIS_URL": "redis://cache:6379"},
            "REDIS_URL must use rediss://",
        ),
    ],
)
def test_invalid_redis_auth_config_fails_at_import(
    env: dict[str, str], message: str
) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("REDIS_", "AWS_"))
    }
    result = subprocess.run(
        [sys.executable, "-c", "import tracecat.config"],
        env=environment | env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert message in result.stderr
