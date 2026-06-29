from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tracecat_registry.integrations.amazon_s3 as amazon_s3
import tracecat_registry.integrations.aws_boto3 as aws_boto3
from tracecat_registry import SecretNotFoundError


def test_get_sync_temporary_credentials_uses_external_id_and_default_session_name() -> (
    None
):
    sts_client = MagicMock()
    sts_client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "access",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        }
    }
    session = MagicMock()
    session.client.return_value = sts_client

    ctx = SimpleNamespace(
        workspace_id="11111111-1111-1111-1111-111111111111",
        run_id="22222222-2222-2222-2222-222222222222",
    )

    with (
        patch.object(aws_boto3, "get_context", return_value=ctx),
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: (
                "tracecat-ws-deadbeef" if key == "TRACECAT_AWS_EXTERNAL_ID" else default
            ),
        ),
        patch.object(aws_boto3.boto3, "Session", return_value=session),
    ):
        creds = aws_boto3.get_sync_temporary_credentials(
            "arn:aws:iam::123456789012:role/customer-role"
        )

    sts_client.assume_role.assert_called_once_with(
        RoleArn="arn:aws:iam::123456789012:role/customer-role",
        RoleSessionName="tracecat-ws-11111111-run-22222222",
        ExternalId="tracecat-ws-deadbeef",
    )
    assert creds["AccessKeyId"] == "access"


def test_get_sync_temporary_credentials_uses_custom_session_name() -> None:
    sts_client = MagicMock()
    sts_client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "access",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        }
    }
    session = MagicMock()
    session.client.return_value = sts_client

    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "TRACECAT_AWS_EXTERNAL_ID": "tracecat-ws-deadbeef",
                "AWS_ROLE_SESSION_NAME": "custom-audit-session",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session", return_value=session),
    ):
        aws_boto3.get_sync_temporary_credentials(
            "arn:aws:iam::123456789012:role/customer-role"
        )

    sts_client.assume_role.assert_called_once_with(
        RoleArn="arn:aws:iam::123456789012:role/customer-role",
        RoleSessionName="custom-audit-session",
        ExternalId="tracecat-ws-deadbeef",
    )


def test_get_sync_temporary_credentials_uses_region_for_sts_client() -> None:
    sts_client = MagicMock()
    sts_client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "access",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        }
    }
    session = MagicMock()
    session.client.return_value = sts_client

    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "TRACECAT_AWS_EXTERNAL_ID": "tracecat-ws-deadbeef",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session", return_value=session) as session_cls,
    ):
        aws_boto3.get_sync_temporary_credentials(
            "arn:aws:iam::123456789012:role/customer-role",
            region_name="us-gov-west-1",
        )

    session_cls.assert_called_once_with(region_name="us-gov-west-1")
    session.client.assert_called_once_with("sts")


def test_get_sync_temporary_credentials_trims_custom_session_name() -> None:
    sts_client = MagicMock()
    sts_client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "access",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        }
    }
    session = MagicMock()
    session.client.return_value = sts_client

    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "TRACECAT_AWS_EXTERNAL_ID": "tracecat-ws-deadbeef",
                "AWS_ROLE_SESSION_NAME": "  custom-audit-session  ",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session", return_value=session),
    ):
        aws_boto3.get_sync_temporary_credentials(
            "arn:aws:iam::123456789012:role/customer-role"
        )

    sts_client.assume_role.assert_called_once_with(
        RoleArn="arn:aws:iam::123456789012:role/customer-role",
        RoleSessionName="custom-audit-session",
        ExternalId="tracecat-ws-deadbeef",
    )


def test_get_sync_temporary_credentials_rejects_non_string_session_name() -> None:
    with patch.object(
        aws_boto3.secrets,
        "get_or_default",
        side_effect=lambda key, default=None: {
            "TRACECAT_AWS_EXTERNAL_ID": "tracecat-ws-deadbeef",
            "AWS_ROLE_SESSION_NAME": 123,
        }.get(key, default),
    ):
        with pytest.raises(TypeError, match="AWS_ROLE_SESSION_NAME must be a string"):
            aws_boto3.get_sync_temporary_credentials(
                "arn:aws:iam::123456789012:role/customer-role"
            )


def test_get_sync_session_with_static_keys() -> None:
    """Static AWS credentials produce a session without STS calls."""
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
                "AWS_REGION": "us-east-1",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session") as session_cls,
    ):
        aws_boto3.get_sync_session()

    session_cls.assert_called_once_with(
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key="secret_test",
        region_name="us-east-1",
    )


def test_get_sync_session_region_override_takes_precedence() -> None:
    """Per-call region override wins over the AWS_REGION secret."""
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
                "AWS_REGION": "us-east-1",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session") as session_cls,
    ):
        aws_boto3.get_sync_session(region_name=" us-gov-west-1 ")

    session_cls.assert_called_once_with(
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key="secret_test",
        region_name="us-gov-west-1",
    )


def test_get_sync_session_with_session_token() -> None:
    """Session credentials (key + secret + token) produce a session."""
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
                "AWS_SESSION_TOKEN": "token_test",
                "AWS_REGION": "us-west-2",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session") as session_cls,
    ):
        aws_boto3.get_sync_session()

    session_cls.assert_called_once_with(
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key="secret_test",
        aws_session_token="token_test",
        region_name="us-west-2",
    )


def test_get_sync_session_no_credentials_raises() -> None:
    """Missing all credential types raises SecretNotFoundError."""
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            return_value=None,
        ),
        pytest.raises(SecretNotFoundError, match="No AWS credentials found"),
    ):
        aws_boto3.get_sync_session()


@pytest.mark.anyio
async def test_get_session_with_static_keys() -> None:
    """Static AWS credentials produce an async session."""
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
                "AWS_REGION": "eu-west-1",
            }.get(key, default),
        ),
        patch.object(aws_boto3.aioboto3, "Session") as session_cls,
    ):
        await aws_boto3.get_session()

    session_cls.assert_called_once_with(
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key="secret_test",
        region_name="eu-west-1",
    )


@pytest.mark.anyio
async def test_get_session_region_override_takes_precedence() -> None:
    """Async sessions also support a per-call region override."""
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
                "AWS_REGION": "eu-west-1",
            }.get(key, default),
        ),
        patch.object(aws_boto3.aioboto3, "Session") as session_cls,
    ):
        await aws_boto3.get_session(region_name="ap-south-2")

    session_cls.assert_called_once_with(
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key="secret_test",
        region_name="ap-south-2",
    )


class _AsyncAwsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def list_buckets(self) -> dict[str, object]:
        self.calls.append(("list_buckets", {}))
        return {"Buckets": []}

    async def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("list_objects_v2", kwargs))
        return {"Contents": []}


class _AsyncAwsSession:
    def __init__(self, client: _AsyncAwsClient) -> None:
        self.client_instance = client
        self.client_calls: list[tuple[str, str | None]] = []

    def client(self, service_name: str, endpoint_url: str | None = None):
        self.client_calls.append((service_name, endpoint_url))
        return self.client_instance


@pytest.mark.anyio
async def test_call_api_accepts_region_override() -> None:
    client = _AsyncAwsClient()
    session = _AsyncAwsSession(client)

    with patch.object(
        aws_boto3, "get_session", AsyncMock(return_value=session)
    ) as get_session:
        result = await aws_boto3.call_api(
            service_name="s3",
            method_name="list_buckets",
            endpoint_url="https://s3.example.test",
            region_name="custom-region-1",
        )

    get_session.assert_awaited_once_with(region_name="custom-region-1")
    assert session.client_calls == [("s3", "https://s3.example.test")]
    assert client.calls == [("list_buckets", {})]
    assert result == {"Buckets": []}


@pytest.mark.anyio
async def test_s3_list_objects_accepts_region_override() -> None:
    client = _AsyncAwsClient()
    session = _AsyncAwsSession(client)

    with patch.object(
        aws_boto3, "get_session", AsyncMock(return_value=session)
    ) as get_session:
        result = await amazon_s3.list_objects(
            bucket="example-bucket",
            prefix="logs/",
            endpoint_url="https://s3.example.test",
            region_name="custom-region-1",
        )

    get_session.assert_awaited_once_with(region_name="custom-region-1")
    assert session.client_calls == [("s3", "https://s3.example.test")]
    assert client.calls == [
        (
            "list_objects_v2",
            {"Bucket": "example-bucket", "Prefix": "logs/", "MaxKeys": 1000},
        )
    ]
    assert result == {"Contents": []}
