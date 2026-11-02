from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tracecat_registry.integrations.aws_boto3 as aws_boto3
from botocore.exceptions import ClientError
from tracecat_registry import SecretNotFoundError

_ROLE_ARN = "arn:aws:iam::123456789012:role/customer-role"
_TARGET_ROLE_ARN = "arn:aws:iam::210987654321:role/target-role"
_STATIC_KEYS = {
    "AWS_ACCESS_KEY_ID": "AKIA_TEST",
    "AWS_SECRET_ACCESS_KEY": "secret_test",
}


def _secrets(values: dict[str, Any]) -> Any:
    return patch.object(
        aws_boto3.secrets,
        "get_or_default",
        side_effect=lambda key, default=None: values.get(key, default),
    )


def _sts_session() -> tuple[MagicMock, MagicMock]:
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


@pytest.mark.parametrize(
    ("configured_session_name", "expected_session_name"),
    [
        (None, "tracecat-ws-11111111-run-22222222"),
        ("custom-audit-session", "custom-audit-session"),
        ("  custom-audit-session  ", "custom-audit-session"),
    ],
    ids=["default-from-context", "custom", "custom-trimmed"],
)
def test_get_sync_temporary_credentials_uses_external_id_and_session_name(
    configured_session_name: str | None, expected_session_name: str
) -> None:
    session, sts_client = _sts_session()
    ctx = SimpleNamespace(
        workspace_id="11111111-1111-1111-1111-111111111111",
        run_id="22222222-2222-2222-2222-222222222222",
    )
    secret_values: dict[str, Any] = {"TRACECAT_AWS_EXTERNAL_ID": "tracecat-ws-deadbeef"}
    if configured_session_name is not None:
        secret_values["AWS_ROLE_SESSION_NAME"] = configured_session_name

    with (
        patch("tracecat_registry.ctx.get_context", return_value=ctx),
        _secrets(secret_values),
        patch.object(aws_boto3.boto3, "Session", return_value=session),
    ):
        creds = aws_boto3.get_sync_temporary_credentials(_ROLE_ARN)

    sts_client.assume_role.assert_called_once_with(
        RoleArn=_ROLE_ARN,
        RoleSessionName=expected_session_name,
        ExternalId="tracecat-ws-deadbeef",
    )
    assert creds["AccessKeyId"] == "chained-access"


def test_get_sync_temporary_credentials_uses_region_for_sts_client() -> None:
    session, _ = _sts_session()

    with (
        _secrets({"TRACECAT_AWS_EXTERNAL_ID": "tracecat-ws-deadbeef"}),
        patch.object(aws_boto3.boto3, "Session", return_value=session) as session_cls,
    ):
        aws_boto3.get_sync_temporary_credentials(_ROLE_ARN, region_name="us-gov-west-1")

    session_cls.assert_called_once_with(region_name="us-gov-west-1")
    session.client.assert_called_once_with("sts")


def test_get_sync_temporary_credentials_rejects_non_string_session_name() -> None:
    with _secrets(
        {
            "TRACECAT_AWS_EXTERNAL_ID": "tracecat-ws-deadbeef",
            "AWS_ROLE_SESSION_NAME": 123,
        }
    ):
        with pytest.raises(TypeError, match="AWS_ROLE_SESSION_NAME must be a string"):
            aws_boto3.get_sync_temporary_credentials(_ROLE_ARN)


def test_get_sync_session_with_static_keys() -> None:
    """Static AWS credentials produce a session without STS calls."""
    with (
        _secrets({**_STATIC_KEYS, "AWS_REGION": "us-east-1"}),
        patch.object(aws_boto3.boto3, "Session") as session_cls,
    ):
        aws_boto3.get_sync_session()

    session_cls.assert_called_once_with(
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key="secret_test",
        region_name="us-east-1",
    )
    session_cls.return_value.client.assert_not_called()


def test_get_sync_session_region_override_takes_precedence() -> None:
    """Per-call region override wins over the AWS_REGION secret."""
    with (
        _secrets({**_STATIC_KEYS, "AWS_REGION": "us-east-1"}),
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
        _secrets(
            {
                **_STATIC_KEYS,
                "AWS_SESSION_TOKEN": "token_test",
                "AWS_REGION": "us-west-2",
            }
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
        patch.object(aws_boto3.secrets, "get_or_default", return_value=None),
        pytest.raises(SecretNotFoundError, match="No AWS credentials found"),
    ):
        aws_boto3.get_sync_session()


def test_get_sync_session_role_arn_chains_from_base_credentials() -> None:
    """A per-call role_arn is assumed with the secret's credentials, not ambient ones."""
    base_session, sts_client = _sts_session()
    chained_session = MagicMock()

    with (
        _secrets(
            {
                **_STATIC_KEYS,
                "AWS_SESSION_TOKEN": "token_test",
                "AWS_REGION": "us-east-1",
            }
        ),
        patch.object(
            aws_boto3.boto3, "Session", side_effect=[base_session, chained_session]
        ) as session_cls,
    ):
        result = aws_boto3.get_sync_session(
            role_arn=_TARGET_ROLE_ARN,
            external_id="partner-id",
            duration_seconds=1800,
        )

    assert result is chained_session
    base_session.client.assert_called_once_with("sts")
    call_kwargs = sts_client.assume_role.call_args.kwargs
    assert call_kwargs["RoleArn"] == _TARGET_ROLE_ARN
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
    base_session, sts_client = _sts_session()

    with (
        _secrets(_STATIC_KEYS),
        patch.object(
            aws_boto3.boto3, "Session", side_effect=[base_session, MagicMock()]
        ),
    ):
        aws_boto3.get_sync_session(
            role_arn=f" {_TARGET_ROLE_ARN} ",
            role_session_name="  audit-run  ",
        )

    sts_client.assume_role.assert_called_once_with(
        RoleArn=_TARGET_ROLE_ARN,
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
        _secrets(_STATIC_KEYS),
        patch.object(aws_boto3.boto3, "Session") as session_cls,
        pytest.raises(ValueError, match="Invalid role_arn"),
    ):
        aws_boto3.get_sync_session(role_arn=role_arn)

    session_cls.return_value.client.assert_not_called()


@pytest.mark.parametrize("duration_seconds", [899, 3601, 0, -1])
def test_get_sync_session_rejects_out_of_range_duration(duration_seconds: int) -> None:
    with (
        _secrets(_STATIC_KEYS),
        patch.object(aws_boto3.boto3, "Session"),
        pytest.raises(ValueError, match="duration_seconds must be between"),
    ):
        aws_boto3.get_sync_session(
            role_arn=_TARGET_ROLE_ARN, duration_seconds=duration_seconds
        )


def test_get_sync_session_redacts_sts_errors() -> None:
    base_session, sts_client = _sts_session()
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
        _secrets(_STATIC_KEYS),
        patch.object(aws_boto3.boto3, "Session", return_value=base_session),
        pytest.raises(RuntimeError) as exc_info,
    ):
        aws_boto3.get_sync_session(role_arn=_TARGET_ROLE_ARN)

    assert str(exc_info.value) == (
        "Failed to assume chained AWS role (error code AccessDenied)"
    )
    assert "111122223333" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


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
        _secrets({**_STATIC_KEYS, "AWS_REGION": "eu-west-1"}),
        patch.object(
            aws_boto3.aioboto3,
            "Session",
            side_effect=[base_session, chained_session],
        ) as session_cls,
    ):
        result = await aws_boto3.get_session(
            role_arn=_TARGET_ROLE_ARN,
            role_session_name="audit-run",
        )

    assert result is chained_session
    session_cls.assert_any_call(
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key="secret_test",
        region_name="eu-west-1",
    )
    base_session.client.assert_called_once_with("sts")
    sts_client.assume_role.assert_awaited_once_with(
        RoleArn=_TARGET_ROLE_ARN,
        RoleSessionName="audit-run",
    )
    session_cls.assert_any_call(
        aws_access_key_id="chained-access",
        aws_secret_access_key="chained-secret",
        aws_session_token="chained-token",
        region_name="eu-west-1",
    )
