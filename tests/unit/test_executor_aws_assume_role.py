"""Tests for host-side AWS AssumeRole secret preprocessing."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunContext
from tracecat.exceptions import TracecatCredentialsError
from tracecat.executor.secret_preprocessors import (
    AwsAssumeRoleSecretPreprocessor,
    _assume_role_via_irsa,
    project_secret_env,
)
from tracecat.identifiers.workflow import WorkflowUUID

_WORKSPACE_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_WF_ID = WorkflowUUID.new("wf-" + "0" * 32)
_WF_EXEC_ID = "wf-" + "0" * 32 + ":exec-" + "0" * 32
_EXTERNAL_ID = "aaaaaaaabbbbccccddddeeeeeeeeeeee"
_VALID_ROLE_ARN = "arn:aws:iam::123456789012:role/customer-role"

_TEMP_CREDS = {
    "AccessKeyId": "ASIA_TEMP_KEY",
    "SecretAccessKey": "temp_secret",
    "SessionToken": "temp_token",
}

_IRSA_PATCH = "tracecat.executor.secret_preprocessors._assume_role_via_irsa"


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        workspace_id=_WORKSPACE_ID,
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=UUID("00000000-0000-0000-0000-000000000099"),
        service_id="tracecat-executor",
    )


@pytest.fixture
def run_context() -> RunContext:
    return RunContext(
        wf_id=_WF_ID,
        wf_exec_id=_WF_EXEC_ID,
        wf_run_id=uuid.uuid4(),
        environment="default",
        logical_time=datetime(2024, 1, 1),
    )


def _base_aws_secrets(
    *, role_arn: str = _VALID_ROLE_ARN, secret_name: str = "amazon_s3"
) -> dict[str, Any]:
    return {
        secret_name: {
            "AWS_ROLE_ARN": role_arn,
            "AWS_REGION": "us-east-1",
        },
        "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
    }


def _mock_assume_role() -> AsyncMock:
    return AsyncMock(return_value=_TEMP_CREDS)


class TestAWSRoleArnPattern:
    """Unit tests for the AWS role ARN validation regex."""

    def test_valid_arn(self) -> None:
        """Standard commercial AWS role ARNs should match."""
        assert AwsAssumeRoleSecretPreprocessor.ROLE_ARN_PATTERN.match(
            "arn:aws:iam::123456789012:role/my-role"
        )

    def test_valid_govcloud_arn(self) -> None:
        """GovCloud AWS role ARNs should match."""
        assert AwsAssumeRoleSecretPreprocessor.ROLE_ARN_PATTERN.match(
            "arn:aws-us-gov:iam::123456789012:role/my-role"
        )

    def test_valid_china_arn(self) -> None:
        """China partition AWS role ARNs should match."""
        assert AwsAssumeRoleSecretPreprocessor.ROLE_ARN_PATTERN.match(
            "arn:aws-cn:iam::123456789012:role/my-role"
        )

    def test_valid_arn_with_path(self) -> None:
        """Role ARNs with nested paths should match."""
        assert AwsAssumeRoleSecretPreprocessor.ROLE_ARN_PATTERN.match(
            "arn:aws:iam::123456789012:role/path/to/role"
        )

    def test_invalid_arn_missing_prefix(self) -> None:
        """Strings without the ARN prefix should not match."""
        assert not AwsAssumeRoleSecretPreprocessor.ROLE_ARN_PATTERN.match(
            "123456789012:role/my-role"
        )

    def test_invalid_arn_wrong_service(self) -> None:
        """ARNs for non-IAM services should not match the role pattern."""
        assert not AwsAssumeRoleSecretPreprocessor.ROLE_ARN_PATTERN.match(
            "arn:aws:s3:::my-bucket"
        )


class TestAssumeRoleViaIrsa:
    @pytest.mark.anyio
    async def test_sts_client_error_raises_credentials_error(self) -> None:
        """STS ClientError should be wrapped in TracecatCredentialsError."""
        mock_sts_client = AsyncMock()
        mock_sts_client.assume_role = AsyncMock(
            side_effect=ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                "AssumeRole",
            )
        )

        with (
            patch(
                "tracecat.executor.secret_preprocessors.aioboto3.Session"
            ) as mock_session_cls,
            pytest.raises(TracecatCredentialsError, match="Failed to assume AWS role"),
        ):
            mock_session = mock_session_cls.return_value
            mock_session.client.return_value.__aenter__.return_value = mock_sts_client

            await _assume_role_via_irsa(
                role_arn=_VALID_ROLE_ARN,
                external_id=_EXTERNAL_ID,
                workspace_id=str(_WORKSPACE_ID),
                run_id=str(uuid.uuid4()),
            )

    @pytest.mark.anyio
    async def test_success_returns_temp_creds(self) -> None:
        """Successful STS responses should return the temp credential mapping."""
        mock_sts_client = AsyncMock()
        mock_sts_client.assume_role = AsyncMock(
            return_value={"Credentials": _TEMP_CREDS}
        )

        with patch(
            "tracecat.executor.secret_preprocessors.aioboto3.Session"
        ) as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_session.client.return_value.__aenter__.return_value = mock_sts_client

            result = await _assume_role_via_irsa(
                role_arn=_VALID_ROLE_ARN,
                external_id=_EXTERNAL_ID,
                workspace_id=str(_WORKSPACE_ID),
                run_id=str(uuid.uuid4()),
            )

        assert result["AccessKeyId"] == "ASIA_TEMP_KEY"
        assert result["SecretAccessKey"] == "temp_secret"
        assert result["SessionToken"] == "temp_token"


class TestProjectSecretEnv:
    @pytest.mark.anyio
    async def test_non_aws_secrets_project_without_copy(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Non-AWS secrets should flatten directly without AWS-specific rewriting."""
        secrets = {
            "crowdstrike": {
                "CROWDSTRIKE_CLIENT_ID": "abc",
                "CROWDSTRIKE_CLIENT_SECRET": "xyz",
            }
        }

        projection = await project_secret_env(
            secrets=secrets,
            role=role,
            run_context=run_context,
        )

        assert projection.env == {
            "CROWDSTRIKE_CLIENT_ID": "abc",
            "CROWDSTRIKE_CLIENT_SECRET": "xyz",
        }

    @pytest.mark.anyio
    async def test_s3_role_arn_rewrites_projected_env(
        self, role: Role, run_context: RunContext
    ) -> None:
        """S3 role ARNs should be replaced with temporary session credentials."""
        secrets = _base_aws_secrets()
        original_secrets = copy.deepcopy(secrets)

        with patch(_IRSA_PATCH, _mock_assume_role()):
            projection = await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        assert secrets == original_secrets
        assert projection.env["AWS_ACCESS_KEY_ID"] == "ASIA_TEMP_KEY"
        assert projection.env["AWS_SECRET_ACCESS_KEY"] == "temp_secret"
        assert projection.env["AWS_SESSION_TOKEN"] == "temp_token"
        assert projection.env["AWS_REGION"] == "us-east-1"
        assert "AWS_ROLE_ARN" not in projection.env
        assert "ASIA_TEMP_KEY" in projection.mask_values
        assert "temp_token" in projection.mask_values

    @pytest.mark.anyio
    async def test_boto3_role_arn_rewrites_projected_env(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Generic AWS secrets should also rewrite role ARNs into temp creds."""
        secrets = _base_aws_secrets(secret_name="aws")

        with patch(_IRSA_PATCH, _mock_assume_role()):
            projection = await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        assert projection.env["AWS_ACCESS_KEY_ID"] == "ASIA_TEMP_KEY"
        assert "AWS_ROLE_ARN" not in projection.env

    @pytest.mark.anyio
    async def test_custom_role_session_name_is_forwarded(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Custom AWS role session names should be forwarded to AssumeRole."""
        secrets = _base_aws_secrets()
        secrets["amazon_s3"]["AWS_ROLE_SESSION_NAME"] = "custom-audit-session"
        mock_assume_role = _mock_assume_role()

        with patch(_IRSA_PATCH, mock_assume_role):
            await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        mock_assume_role.assert_awaited_once_with(
            role_arn=_VALID_ROLE_ARN,
            external_id=_EXTERNAL_ID,
            workspace_id=str(_WORKSPACE_ID),
            run_id=str(run_context.wf_run_id),
            role_session_name="custom-audit-session",
        )

    @pytest.mark.anyio
    async def test_role_session_name_is_trimmed_before_forwarding(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Whitespace around AWS_ROLE_SESSION_NAME should be trimmed first."""
        secrets = _base_aws_secrets()
        secrets["amazon_s3"]["AWS_ROLE_SESSION_NAME"] = "  custom-audit-session  "
        mock_assume_role = _mock_assume_role()

        with patch(_IRSA_PATCH, mock_assume_role):
            await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        mock_assume_role.assert_awaited_once_with(
            role_arn=_VALID_ROLE_ARN,
            external_id=_EXTERNAL_ID,
            workspace_id=str(_WORKSPACE_ID),
            run_id=str(run_context.wf_run_id),
            role_session_name="custom-audit-session",
        )

    @pytest.mark.anyio
    async def test_invalid_arn_raises_credentials_error(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Invalid role ARN values should fail before AssumeRole is attempted."""
        secrets = _base_aws_secrets(role_arn="not-a-valid-arn")

        with pytest.raises(TracecatCredentialsError, match="Invalid AWS role ARN"):
            await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

    @pytest.mark.anyio
    async def test_missing_external_id_raises_credentials_error(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Missing runtime external IDs should raise a credentials error."""
        secrets = {
            "amazon_s3": {
                "AWS_ROLE_ARN": _VALID_ROLE_ARN,
                "AWS_REGION": "us-east-1",
            }
        }

        with pytest.raises(TracecatCredentialsError, match="Missing runtime AWS"):
            await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

    @pytest.mark.anyio
    async def test_sts_failure_raises_credentials_error(
        self, role: Role, run_context: RunContext
    ) -> None:
        """AssumeRole failures should propagate as credential errors."""
        secrets = _base_aws_secrets()

        with (
            patch(
                _IRSA_PATCH,
                AsyncMock(
                    side_effect=TracecatCredentialsError("Failed to assume AWS role")
                ),
            ),
            pytest.raises(TracecatCredentialsError, match="Failed to assume AWS role"),
        ):
            await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

    @pytest.mark.anyio
    async def test_aws_profile_raises_credentials_error(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Non-empty AWS_PROFILE values should be rejected."""
        secrets = {
            "amazon_s3": {
                "AWS_PROFILE": "customer-profile",
                "AWS_REGION": "us-east-1",
            },
            "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
        }

        with pytest.raises(
            TracecatCredentialsError, match="AWS_PROFILE is not supported"
        ):
            await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

    @pytest.mark.anyio
    async def test_blank_aws_profile_is_treated_as_unset(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Blank AWS_PROFILE values should be removed and otherwise ignored."""
        secrets = {
            "amazon_s3": {
                "AWS_PROFILE": "",
                "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
                "AWS_SECRET_ACCESS_KEY": "static_secret",
                "AWS_REGION": "us-west-2",
            },
            "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
        }

        projection = await project_secret_env(
            secrets=secrets,
            role=role,
            run_context=run_context,
        )

        assert projection.env == {
            "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
            "AWS_SECRET_ACCESS_KEY": "static_secret",
            "AWS_REGION": "us-west-2",
            "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
        }

    @pytest.mark.anyio
    async def test_blank_role_arn_is_treated_as_unset(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Blank AWS_ROLE_ARN values should be treated as absent."""
        secrets = {
            "amazon_s3": {
                "AWS_ROLE_ARN": " ",
                "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
                "AWS_SECRET_ACCESS_KEY": "static_secret",
                "AWS_REGION": "us-west-2",
            },
            "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
        }

        projection = await project_secret_env(
            secrets=secrets,
            role=role,
            run_context=run_context,
        )

        assert projection.env == {
            "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
            "AWS_SECRET_ACCESS_KEY": "static_secret",
            "AWS_REGION": "us-west-2",
            "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
        }

    @pytest.mark.anyio
    async def test_static_keys_pass_through_unchanged(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Static AWS credentials should pass through without mutation."""
        secrets = {
            "amazon_s3": {
                "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
                "AWS_SECRET_ACCESS_KEY": "static_secret",
                "AWS_REGION": "us-west-2",
            }
        }

        projection = await project_secret_env(
            secrets=secrets,
            role=role,
            run_context=run_context,
        )

        assert projection.env == {
            "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
            "AWS_SECRET_ACCESS_KEY": "static_secret",
            "AWS_REGION": "us-west-2",
        }

    @pytest.mark.anyio
    async def test_raw_secrets_unchanged_after_projection(
        self, role: Role, run_context: RunContext
    ) -> None:
        """The original secret mapping should remain unchanged after projection."""
        secrets = _base_aws_secrets()
        original_arn = secrets["amazon_s3"]["AWS_ROLE_ARN"]

        with patch(_IRSA_PATCH, _mock_assume_role()):
            await project_secret_env(
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        assert secrets["amazon_s3"]["AWS_ROLE_ARN"] == original_arn
        assert "AWS_ACCESS_KEY_ID" not in secrets["amazon_s3"]

    @pytest.mark.anyio
    async def test_non_aws_secrets_are_preserved_in_projected_env(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Unrelated secret namespaces should remain available after projection."""
        secrets = {
            "amazon_s3": {
                "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
                "AWS_SECRET_ACCESS_KEY": "static_secret",
                "AWS_REGION": "us-west-2",
            },
            "other_secret": {
                "API_KEY": "some-key",
            },
        }

        projection = await project_secret_env(
            secrets=secrets,
            role=role,
            run_context=run_context,
        )

        assert projection.env["API_KEY"] == "some-key"
