from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tracecat_registry.integrations.aws_boto3 as aws_boto3
from botocore.exceptions import ClientError
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
        patch("tracecat_registry.ctx.get_context", return_value=ctx),
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


def _chained_sts_session() -> tuple[MagicMock, MagicMock]:
    sts_client = MagicMock()
    sts_client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "chained-access",
            "SecretAccessKey": "chained-secret",
            "SessionToken": "chained-token",
        }
    }
    base_session = MagicMock()
    base_session.client.return_value = sts_client
    return base_session, sts_client


def test_get_sync_session_role_arn_chains_from_base_credentials() -> None:
    """A per-call role_arn is assumed with the secret's credentials, not ambient ones."""
    base_session, sts_client = _chained_sts_session()
    chained_session = MagicMock()

    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
                "AWS_SESSION_TOKEN": "token_test",
                "AWS_REGION": "us-east-1",
            }.get(key, default),
        ),
        patch.object(
            aws_boto3.boto3, "Session", side_effect=[base_session, chained_session]
        ) as session_cls,
    ):
        result = aws_boto3.get_sync_session(
            role_arn="arn:aws:iam::210987654321:role/target-role",
            external_id="partner-id",
            duration_seconds=1800,
        )

    assert result is chained_session
    base_session.client.assert_called_once_with("sts")
    call_kwargs = sts_client.assume_role.call_args.kwargs
    assert call_kwargs["RoleArn"] == "arn:aws:iam::210987654321:role/target-role"
    assert call_kwargs["ExternalId"] == "partner-id"
    assert call_kwargs["DurationSeconds"] == 1800
    assert call_kwargs["RoleSessionName"].startswith("tracecat")
    session_cls.assert_any_call(
        aws_access_key_id="chained-access",
        aws_secret_access_key="chained-secret",
        aws_session_token="chained-token",
        region_name="us-east-1",
    )


def test_get_sync_session_role_arn_custom_session_name_and_no_external_id() -> None:
    base_session, sts_client = _chained_sts_session()

    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
            }.get(key, default),
        ),
        patch.object(
            aws_boto3.boto3, "Session", side_effect=[base_session, MagicMock()]
        ),
    ):
        aws_boto3.get_sync_session(
            role_arn=" arn:aws:iam::210987654321:role/target-role ",
            role_session_name="  audit-run  ",
        )

    sts_client.assume_role.assert_called_once_with(
        RoleArn="arn:aws:iam::210987654321:role/target-role",
        RoleSessionName="audit-run",
    )


@pytest.mark.parametrize(
    "role_arn",
    [
        "not-an-arn",
        "arn:aws:iam::123:role/short-account",
        "arn:aws:iam::123456789012:user/not-a-role",
        "arn:aws:iam::123456789012:role/bad chars; DROP",
    ],
)
def test_get_sync_session_rejects_invalid_role_arn(role_arn: str) -> None:
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session") as session_cls,
        pytest.raises(ValueError, match="Invalid role_arn"),
    ):
        aws_boto3.get_sync_session(role_arn=role_arn)

    session_cls.return_value.client.assert_not_called()


@pytest.mark.parametrize("duration_seconds", [899, 3601, 0, -1])
def test_get_sync_session_rejects_out_of_range_duration(duration_seconds: int) -> None:
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session"),
        pytest.raises(ValueError, match="duration_seconds must be between"),
    ):
        aws_boto3.get_sync_session(
            role_arn="arn:aws:iam::210987654321:role/target-role",
            duration_seconds=duration_seconds,
        )


def test_get_sync_session_redacts_sts_errors() -> None:
    base_session, sts_client = _chained_sts_session()
    sts_client.assume_role.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDenied",
                "Message": "User arn:aws:sts::111122223333:assumed-role/x is not authorized",
            }
        },
        "AssumeRole",
    )

    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session", return_value=base_session),
        pytest.raises(RuntimeError) as exc_info,
    ):
        aws_boto3.get_sync_session(
            role_arn="arn:aws:iam::210987654321:role/target-role"
        )

    assert str(exc_info.value) == (
        "Failed to assume chained AWS role (error code AccessDenied)"
    )
    assert "111122223333" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_get_sync_session_without_role_arn_does_not_call_sts() -> None:
    with (
        patch.object(
            aws_boto3.secrets,
            "get_or_default",
            side_effect=lambda key, default=None: {
                "AWS_ACCESS_KEY_ID": "AKIA_TEST",
                "AWS_SECRET_ACCESS_KEY": "secret_test",
            }.get(key, default),
        ),
        patch.object(aws_boto3.boto3, "Session") as session_cls,
    ):
        aws_boto3.get_sync_session()

    session_cls.return_value.client.assert_not_called()


@pytest.mark.anyio
async def test_get_session_role_arn_chains_from_base_credentials() -> None:
    sts_client = MagicMock()
    sts_client.assume_role = AsyncMock(
        return_value={
            "Credentials": {
                "AccessKeyId": "chained-access",
                "SecretAccessKey": "chained-secret",
                "SessionToken": "chained-token",
            }
        }
    )
    sts_cm = MagicMock()
    sts_cm.__aenter__ = AsyncMock(return_value=sts_client)
    sts_cm.__aexit__ = AsyncMock(return_value=False)
    base_session = MagicMock()
    base_session.client.return_value = sts_cm
    chained_session = MagicMock()

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
        patch.object(
            aws_boto3.aioboto3,
            "Session",
            side_effect=[base_session, chained_session],
        ) as session_cls,
    ):
        result = await aws_boto3.get_session(
            role_arn="arn:aws:iam::210987654321:role/target-role",
            role_session_name="audit-run",
        )

    assert result is chained_session
    base_session.client.assert_called_once_with("sts")
    sts_client.assume_role.assert_awaited_once_with(
        RoleArn="arn:aws:iam::210987654321:role/target-role",
        RoleSessionName="audit-run",
    )
    session_cls.assert_any_call(
        aws_access_key_id="chained-access",
        aws_secret_access_key="chained-secret",
        aws_session_token="chained-token",
        region_name="eu-west-1",
    )
