"""Unit tests for Postgres IAM token login. No cloud calls."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from botocore.exceptions import NoCredentialsError
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.db import engine as db_engine
from tracecat.db.exceptions import DatabaseCredentialUnavailableError
from tracecat.db.iam import aws as db_aws
from tracecat.db.iam import azure as db_azure
from tracecat.db.iam.aws import RdsIamTokenProvider
from tracecat.db.iam.azure import (
    AZURE_POSTGRES_SCOPE,
    AzureEntraTokenProvider,
    mint_entra_token,
)
from tracecat.db.iam.tokens import build_token_provider
from tracecat.db.iam.types import (
    DbAwsIamAuth,
    DbAzureEntraAuth,
    DbPasswordAuth,
)

AWS_AUTH = DbAwsIamAuth(
    host="db.abc123.us-east-1.rds.amazonaws.com", port=5432, region="us-east-1"
)


def _static_session() -> boto3.Session:
    return boto3.Session(
        aws_access_key_id="AKIDEXAMPLE",
        aws_secret_access_key="secret",
        region_name="us-east-1",
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _FakeRdsClient:
    def __init__(self, sign: Callable[..., str]) -> None:
        self.generate_db_auth_token = sign

    def close(self) -> None:
        pass


# --- AWS ---


def test_rds_token_is_signed_for_the_rds_endpoint_and_user() -> None:
    """The token is RDS's presigned connect URL for the IAM host, not the pooler."""
    token = RdsIamTokenProvider(AWS_AUTH, "app", session=_static_session()).mint()

    parts = urlsplit(f"https://{token}")
    query = parse_qs(parts.query)
    assert parts.netloc == "db.abc123.us-east-1.rds.amazonaws.com:5432"
    assert query["Action"] == ["connect"]
    assert query["DBUser"] == ["app"]
    assert query["X-Amz-Expires"] == ["900"]
    assert "us-east-1/rds-db/aws4_request" in query["X-Amz-Credential"][0]


def test_rds_token_is_cached_then_resigned(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    provider = RdsIamTokenProvider(
        AWS_AUTH, "app", session=_static_session(), clock=clock
    )
    calls: list[dict[str, Any]] = []

    def sign(**kwargs: Any) -> str:
        calls.append(kwargs)
        return f"token-{len(calls)}"

    monkeypatch.setattr(provider, "_client", _FakeRdsClient(sign))

    assert provider.mint() == "token-1"
    clock.now += AWS_AUTH.cache_seconds - 1
    assert provider.mint() == "token-1"
    clock.now += 1
    assert provider.mint() == "token-2"
    assert calls[0] == {
        "DBHostname": AWS_AUTH.host,
        "Port": 5432,
        "DBUsername": "app",
        "Region": "us-east-1",
    }


@pytest.mark.anyio
async def test_rds_provider_is_an_async_password_callable() -> None:
    provider = RdsIamTokenProvider(AWS_AUTH, "app", session=_static_session())
    first = await provider()
    assert first.startswith("db.abc123.us-east-1.rds.amazonaws.com:5432/?")
    assert await provider() == first  # cached
    await provider.aclose()


@pytest.mark.anyio
async def test_rds_client_is_created_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating the client can resolve credentials over the network."""
    threads: list[str] = []

    class Session:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def client(self, *args: Any, **kwargs: Any) -> _FakeRdsClient:
            threads.append(threading.current_thread().name)
            return _FakeRdsClient(lambda **_: "token")

    monkeypatch.setattr(db_aws.boto3, "Session", Session)

    provider = RdsIamTokenProvider(AWS_AUTH, "app")
    assert threads == []
    assert await provider() == "token"
    assert threads and threads[0] != threading.main_thread().name
    await provider.aclose()


def test_rds_missing_credentials_fail_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RdsIamTokenProvider(AWS_AUTH, "app", session=_static_session())

    def sign(**kwargs: Any) -> str:
        raise NoCredentialsError()

    monkeypatch.setattr(provider, "_client", _FakeRdsClient(sign))

    with pytest.raises(DatabaseCredentialUnavailableError) as exc_info:
        provider.mint()
    assert isinstance(exc_info.value.__cause__, NoCredentialsError)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "", "port": 5432, "region": "us-east-1"},
        {"host": "h", "port": 5432, "region": ""},
        {"host": "h", "port": 0, "region": "us-east-1"},
        {"host": "h", "port": 5432, "region": "us-east-1", "cache_seconds": 900},
        {"host": "h", "port": 5432, "region": "us-east-1", "cache_seconds": 0},
    ],
)
def test_aws_auth_rejects_invalid_settings(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        DbAwsIamAuth(**kwargs)


# --- Azure ---


class _FakeCredential:
    def __init__(self, *, fail: bool = False) -> None:
        self.scopes: list[str] = []
        self.closed = False
        self.fail = fail

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        self.scopes.extend(scopes)
        if self.fail:
            raise ClientAuthenticationError("no identity")
        return AccessToken("entra-token", 2_000_000_000)

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


@pytest.mark.anyio
async def test_azure_provider_fetches_postgres_scoped_token() -> None:
    credential = _FakeCredential()
    created: list[_FakeCredential] = []

    def factory() -> Any:
        created.append(credential)
        return credential

    provider = AzureEntraTokenProvider(DbAzureEntraAuth(), credential_factory=factory)

    assert await provider() == "entra-token"
    assert await provider() == "entra-token"
    assert credential.scopes == [AZURE_POSTGRES_SCOPE, AZURE_POSTGRES_SCOPE]
    assert len(created) == 1  # one credential; the SDK caches the token
    await provider.aclose()
    assert credential.closed


@pytest.mark.anyio
async def test_azure_failure_is_a_credential_error() -> None:
    provider = AzureEntraTokenProvider(
        DbAzureEntraAuth(), credential_factory=lambda: _FakeCredential(fail=True)
    )
    with pytest.raises(DatabaseCredentialUnavailableError):
        await provider()


def test_azure_sync_mint_uses_client_id_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class SyncCredential:
        def __init__(self, **kwargs: Any) -> None:
            seen["kwargs"] = kwargs

        def get_token(self, *scopes: str) -> AccessToken:
            seen["scopes"] = scopes
            return AccessToken("sync-token", 2_000_000_000)

        def close(self) -> None:
            seen["closed"] = True

    monkeypatch.setattr(db_azure, "SyncDefaultAzureCredential", SyncCredential)

    assert mint_entra_token(DbAzureEntraAuth(client_id="cid")) == "sync-token"
    assert seen == {
        "kwargs": {
            "managed_identity_client_id": "cid",
            "workload_identity_client_id": "cid",
        },
        "scopes": (AZURE_POSTGRES_SCOPE,),
        "closed": True,
    }


# --- Engine wiring ---


@pytest.fixture
def aws_token_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__DB_AUTH", AWS_AUTH)
    monkeypatch.setattr(config, "TRACECAT__DB_USER", "app")
    monkeypatch.setattr(config, "TRACECAT__DB_PASS", None)
    monkeypatch.setattr(config, "TRACECAT__DB_PASS__ARN", None)
    monkeypatch.setattr(config, "TRACECAT__DB_ENDPOINT", "tracecat-pgdog")
    monkeypatch.setattr(config, "TRACECAT__DB_PORT", "6432")
    monkeypatch.setattr(config, "TRACECAT__DB_NAME", "tracecat")
    monkeypatch.setattr(config, "TRACECAT__DB_SSLMODE", "require")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")


@pytest.mark.usefixtures("aws_token_mode")
def test_token_mode_uri_has_no_password() -> None:
    uri = make_url(db_engine._get_db_uri(driver="asyncpg"))

    assert uri.password is None
    assert (uri.username, uri.host, uri.port, uri.database) == (
        "app",
        "tracecat-pgdog",
        6432,
        "tracecat",
    )
    assert uri.query == {"ssl": "require"}


@pytest.mark.usefixtures("aws_token_mode")
def test_token_mode_engines_get_their_own_token_provider() -> None:
    main = db_engine._connect_args("tracecat-api")
    auth = db_engine._connect_args("tracecat-api-auth")

    assert isinstance(main["password"], RdsIamTokenProvider)
    assert isinstance(auth["password"], RdsIamTokenProvider)
    assert main["password"] is not auth["password"]
    assert main["server_settings"] == {"application_name": "tracecat-api"}


def test_password_mode_connect_args_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__DB_AUTH", DbPasswordAuth())
    assert db_engine._connect_args("svc") == {
        "server_settings": {"application_name": "svc"}
    }


@pytest.mark.anyio
async def test_asyncpg_calls_password_provider_for_each_new_connection(
    db: None,
) -> None:
    """SQLAlchemy forwards the callable and asyncpg awaits it on every login."""
    url = make_url(TEST_DB_CONFIG.sys_url)
    password = url.password
    assert password is not None
    calls = 0

    async def provider() -> str:
        nonlocal calls
        calls += 1
        return password

    engine = create_async_engine(
        url.set(password=None),
        poolclass=NullPool,
        connect_args={"password": provider},
    )
    try:
        for _ in range(2):
            async with engine.connect() as conn:
                assert (await conn.execute(text("SELECT 1"))).scalar() == 1
    finally:
        await engine.dispose()
    assert calls == 2


def test_build_token_provider_dispatches_on_mode() -> None:
    assert isinstance(
        build_token_provider(DbAzureEntraAuth(), "app"), AzureEntraTokenProvider
    )


# --- Parsing TRACECAT__DB_AUTH from the environment ---


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "TRACECAT__DB_AUTH_MODE",
        "TRACECAT__DB_AWS_IAM_HOST",
        "TRACECAT__DB_AWS_IAM_PORT",
        "TRACECAT__DB_AWS_REGION",
        "TRACECAT__DB_AZURE_CLIENT_ID",
        "TRACECAT__DB_ENDPOINT",
        "TRACECAT__DB_PORT",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_parse_db_auth_defaults_to_password(db_env: pytest.MonkeyPatch) -> None:
    assert config._parse_db_auth() == DbPasswordAuth()


def test_parse_db_auth_aws_defaults_to_the_connection_endpoint(
    db_env: pytest.MonkeyPatch,
) -> None:
    db_env.setenv("TRACECAT__DB_AUTH_MODE", " AWS_IAM ")
    db_env.setenv("TRACECAT__DB_ENDPOINT", "db.rds.amazonaws.com")
    db_env.setenv("TRACECAT__DB_PORT", "5433")
    db_env.setenv("AWS_DEFAULT_REGION", "eu-west-1")

    assert config._parse_db_auth() == DbAwsIamAuth(
        host="db.rds.amazonaws.com", port=5433, region="eu-west-1"
    )


def test_parse_db_auth_aws_signs_for_rds_behind_a_pooler(
    db_env: pytest.MonkeyPatch,
) -> None:
    db_env.setenv("TRACECAT__DB_AUTH_MODE", "aws_iam")
    db_env.setenv("TRACECAT__DB_ENDPOINT", "tracecat-pgdog")
    db_env.setenv("TRACECAT__DB_PORT", "6432")
    db_env.setenv("TRACECAT__DB_AWS_IAM_HOST", "db.rds.amazonaws.com")
    db_env.setenv("TRACECAT__DB_AWS_IAM_PORT", "5432")
    db_env.setenv("TRACECAT__DB_AWS_REGION", "us-east-1")
    db_env.setenv("AWS_REGION", "eu-west-1")

    assert config._parse_db_auth() == DbAwsIamAuth(
        host="db.rds.amazonaws.com", port=5432, region="us-east-1"
    )


@pytest.mark.parametrize(
    ("client_id", "expected"), [(None, None), ("  ", None), ("cid", "cid")]
)
def test_parse_db_auth_azure(
    db_env: pytest.MonkeyPatch, client_id: str | None, expected: str | None
) -> None:
    db_env.setenv("TRACECAT__DB_AUTH_MODE", "azure_entra")
    if client_id is not None:
        db_env.setenv("TRACECAT__DB_AZURE_CLIENT_ID", client_id)

    assert config._parse_db_auth() == DbAzureEntraAuth(client_id=expected)


# --- Startup validation ---

_TOKEN_ENV = {
    "TRACECAT__DB_AUTH_MODE": "aws_iam",
    "TRACECAT__DB_USER": "app",
    "TRACECAT__DB_ENDPOINT": "tracecat-pgdog",
    "TRACECAT__DB_PORT": "6432",
    "TRACECAT__DB_NAME": "tracecat",
    "TRACECAT__DB_SSLMODE": "require",
    "TRACECAT__DB_AWS_IAM_HOST": "db.rds.amazonaws.com",
    "TRACECAT__DB_AWS_REGION": "us-east-1",
}


def _import_config(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TRACECAT__DB_", "AWS_"))
    }
    return subprocess.run(
        [sys.executable, "-c", "import tracecat.config"],
        env=environment | env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_valid_token_config_imports() -> None:
    result = _import_config(_TOKEN_ENV)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            # A credential pasted into the wrong variable is never echoed.
            {"TRACECAT__DB_AUTH_MODE": "leftover-credential"},
            "TRACECAT__DB_AUTH_MODE must be one of",
        ),
        (
            {**_TOKEN_ENV, "TRACECAT__DB_AWS_REGION": ""},
            "requires TRACECAT__DB_AWS_REGION",
        ),
        (
            {**_TOKEN_ENV, "TRACECAT__DB_AWS_IAM_PORT": "abc"},
            "must be a port number",
        ),
        (
            {**_TOKEN_ENV, "TRACECAT__DB_NAME": ""},
            "requires TRACECAT__DB_NAME",
        ),
        (
            {**_TOKEN_ENV, "TRACECAT__DB_PASS": "leftover"},
            "unset TRACECAT__DB_PASS and TRACECAT__DB_PASS__ARN",
        ),
        (
            {**_TOKEN_ENV, "TRACECAT__DB_PASS__ARN": "arn:aws:secretsmanager:x"},
            "unset TRACECAT__DB_PASS and TRACECAT__DB_PASS__ARN",
        ),
        (
            {**_TOKEN_ENV, "TRACECAT__DB_SSLMODE": "prefer"},
            "requires TLS",
        ),
        (
            {
                **_TOKEN_ENV,
                "TRACECAT__DB_AUTH_MODE": "azure_entra",
                "TRACECAT__DB_SSLMODE": "disable",
            },
            "requires TLS",
        ),
    ],
)
def test_invalid_db_auth_config_fails_at_import(
    env: dict[str, str], message: str
) -> None:
    result = _import_config(env)
    assert result.returncode != 0
    assert message in result.stderr
    assert "leftover" not in result.stderr


@pytest.mark.parametrize(
    ("statement", "absent"),
    [
        (
            "import tracecat.db.engine",
            ("tracecat.db.iam.aws", "tracecat.db.iam.azure", "azure.identity"),
        ),
        (
            "from tracecat.db.iam.tokens import build_token_provider\n"
            "from tracecat.db.iam.types import DbAwsIamAuth\n"
            "build_token_provider(DbAwsIamAuth('h', 5432, 'us-east-1'), 'app')",
            ("tracecat.db.iam.azure", "azure.identity"),
        ),
    ],
    ids=["password-mode", "aws-mode"],
)
def test_cloud_sdks_load_only_for_their_mode(
    statement: str, absent: tuple[str, ...]
) -> None:
    """A deployment only imports its own cloud's provider and SDK."""
    check = f"import sys\n{statement}\nprint(sorted(m for m in {absent!r} if m in sys.modules))"
    result = subprocess.run(
        [sys.executable, "-c", check],
        env={
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("TRACECAT__DB_", "REDIS_"))
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "[]"
