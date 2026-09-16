"""End-to-end AWS-backed secret resolution against a moto server.

Exercises the real ``aioboto3`` code path (STS AssumeRole then Secrets Manager
GetSecretValue) via ``AWS_ENDPOINT_URL``, then the full store -> authorize ->
reference -> ``AuthSandbox`` flow against the test database.
"""

import json
import socket
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import boto3
import pytest
from moto.server import ThreadedMotoServer
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.sandbox import AuthSandbox
from tracecat.auth.types import Role
from tracecat.db.models import Workspace
from tracecat.exceptions import TracecatCredentialsError
from tracecat.secrets.enums import AwsSecretMappingMode, AwsSecretResolutionErrorCode
from tracecat.secrets.schemas import (
    AwsSecretJsonField,
    AwsSecretKeyMapping,
    AwsSecretReferenceCreate,
    SecretStoreCreate,
)
from tracecat.secrets.service import SecretsService
from tracecat.secrets.store_service import SecretStoresService

pytestmark = pytest.mark.usefixtures("db")

REGION = "us-east-1"
ROLE_ARN = "arn:aws:iam::123456789012:role/tracecat-secrets-reader"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def moto_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    endpoint = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    try:
        yield endpoint
    finally:
        server.stop()


def _seed_secret(endpoint: str, name: str, value: str) -> str:
    client = boto3.client("secretsmanager", region_name=REGION, endpoint_url=endpoint)
    return client.create_secret(Name=name, SecretString=value)["ARN"]


@pytest.fixture
async def stores(session: AsyncSession, svc_admin_role: Role) -> SecretStoresService:
    return SecretStoresService(session=session, role=svc_admin_role)


@pytest.fixture
async def secrets(
    session: AsyncSession, svc_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> SecretsService:
    """Service bound to the test transaction, also used by ``AuthSandbox``.

    The test session runs inside a savepoint, so a fresh ``with_session``
    would not see uncommitted rows.
    """
    service = SecretsService(session=session, role=svc_admin_role)

    @asynccontextmanager
    async def _with_session(*_: object, **__: object) -> AsyncIterator[SecretsService]:
        yield service

    monkeypatch.setattr(SecretsService, "with_session", _with_session)
    return service


@pytest.mark.anyio
async def test_e2e_whole_string_and_json_references_resolve_in_sandbox(
    moto_endpoint: str,
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
    svc_admin_role: Role,
) -> None:
    token_arn = _seed_secret(moto_endpoint, "app/token", "tok-123")
    creds_arn = _seed_secret(
        moto_endpoint,
        "app/creds",
        json.dumps({"username": "svc", "password": "pw", "extra": 1}),
    )

    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    await stores.authorize_workspace(store, svc_workspace.id)

    await secrets.create_aws_secret_reference(
        AwsSecretReferenceCreate(
            name="aws_token",
            store_id=store.id,
            remote_reference=token_arn,
            key_mapping=AwsSecretKeyMapping(
                mode=AwsSecretMappingMode.WHOLE_STRING, keys=["API_TOKEN"]
            ),
        )
    )
    await secrets.create_aws_secret_reference(
        AwsSecretReferenceCreate(
            name="aws_creds",
            store_id=store.id,
            remote_reference=creds_arn,
            key_mapping=AwsSecretKeyMapping(
                mode=AwsSecretMappingMode.JSON,
                fields=[
                    AwsSecretJsonField(key="USER", field="username"),
                    AwsSecretJsonField(key="PASS", field="password"),
                ],
            ),
        )
    )

    check = await secrets.check_aws_secret_reference(
        await secrets.get_secret_by_name("aws_creds")
    )
    assert check.ok is True
    assert sorted(check.resolved_keys) == ["PASS", "USER"]

    async with AuthSandbox(
        role=svc_admin_role,
        secrets=["aws_token.API_TOKEN", "aws_creds.USER", "aws_creds.PASS"],
    ) as sandbox:
        assert sandbox.secrets == {
            "aws_token": {"API_TOKEN": "tok-123"},
            "aws_creds": {"USER": "svc", "PASS": "pw"},
        }


@pytest.mark.anyio
async def test_e2e_missing_remote_secret_fails_without_leaking(
    moto_endpoint: str,
    stores: SecretStoresService,
    secrets: SecretsService,
    svc_workspace: Workspace,
    svc_admin_role: Role,
) -> None:
    store = await stores.create_store(
        SecretStoreCreate(name="prod", role_arn=ROLE_ARN, region=REGION)
    )
    await stores.authorize_workspace(store, svc_workspace.id)
    missing_arn = f"arn:aws:secretsmanager:{REGION}:123456789012:secret:nope-AbCdEf"
    await secrets.create_aws_secret_reference(
        AwsSecretReferenceCreate(
            name="aws_missing",
            store_id=store.id,
            remote_reference=missing_arn,
            key_mapping=AwsSecretKeyMapping(
                mode=AwsSecretMappingMode.WHOLE_STRING, keys=["K"]
            ),
        )
    )

    check = await secrets.check_aws_secret_reference(
        await secrets.get_secret_by_name("aws_missing")
    )
    assert check.ok is False
    assert check.error_code == AwsSecretResolutionErrorCode.NOT_FOUND

    with pytest.raises(TracecatCredentialsError):
        async with AuthSandbox(role=svc_admin_role, secrets=["aws_missing.K"]):
            pass
