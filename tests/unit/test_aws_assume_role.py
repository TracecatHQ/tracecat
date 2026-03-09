from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import tracecat_registry.integrations.aws_boto3 as aws_boto3


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
        aws_assume_role_external_id="tracecat-ws-deadbeef",
        workspace_id="11111111-1111-1111-1111-111111111111",
        run_id="22222222-2222-2222-2222-222222222222",
    )

    with (
        patch.object(aws_boto3, "get_context", return_value=ctx),
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
