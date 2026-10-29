"""Tests for host-side AWS AssumeRole secret preprocessing."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunContext
from tracecat.exceptions import TracecatCredentialsError
from tracecat.executor.secret_preprocessors import (
    _AWS_ROLE_ARN_PATTERN,
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
    "Expiration": datetime(2030, 1, 1, tzinfo=UTC),
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


@pytest.mark.parametrize(
    ("arn", "valid"),
    [
        ("arn:aws:iam::123456789012:role/my-role", True),
        ("arn:aws-us-gov:iam::123456789012:role/my-role", True),
        ("arn:aws-cn:iam::123456789012:role/my-role", True),
        ("arn:aws:iam::123456789012:role/path/to/role", True),
        ("123456789012:role/my-role", False),
        ("arn:aws:s3:::my-bucket", False),
    ],
)
def test_aws_role_arn_pattern(arn: str, valid: bool) -> None:
    assert bool(_AWS_ROLE_ARN_PATTERN.match(arn)) is valid


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
    @pytest.mark.parametrize("secret_name", ["amazon_s3", "aws"])
    async def test_role_arn_rewrites_projected_env(
        self, role: Role, run_context: RunContext, secret_name: str
    ) -> None:
        """Role ARNs should be replaced with temporary session credentials."""
        secrets = _base_aws_secrets(secret_name=secret_name)
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
    async def test_preprocessed_credentials_report_expiry_and_hide_from_repr(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Minted STS credentials carry their expiry and stay out of repr."""
        with patch(_IRSA_PATCH, _mock_assume_role()):
            preprocessed = await AwsAssumeRoleSecretPreprocessor().preprocess(
                secrets=_base_aws_secrets(), role=role, run_context=run_context
            )
            projection = await project_secret_env(
                secrets=_base_aws_secrets(), role=role, run_context=run_context
            )

        assert preprocessed.expires_at == _TEMP_CREDS["Expiration"]
        assert projection.expires_at == _TEMP_CREDS["Expiration"]
        assert "temp_secret" not in repr(preprocessed)
        assert "temp_token" not in repr(preprocessed)

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("overrides", "role_session_name", "region_name"),
        [
            pytest.param(
                {"AWS_ROLE_SESSION_NAME": "custom-audit-session"},
                "custom-audit-session",
                "us-east-1",
                id="custom_role_session_name",
            ),
            pytest.param(
                {"AWS_ROLE_SESSION_NAME": "  custom-audit-session  "},
                "custom-audit-session",
                "us-east-1",
                id="trimmed_role_session_name",
            ),
            pytest.param(
                {"AWS_REGION": " us-gov-west-1 "},
                None,
                "us-gov-west-1",
                id="aws_region",
            ),
        ],
    )
    async def test_assume_role_options_are_forwarded(
        self,
        role: Role,
        run_context: RunContext,
        overrides: dict[str, str],
        role_session_name: str | None,
        region_name: str,
    ) -> None:
        secrets = _base_aws_secrets()
        secrets["amazon_s3"].update(overrides)
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
            role_session_name=role_session_name,
            region_name=region_name,
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
    @pytest.mark.parametrize(
        "blank_key",
        [
            pytest.param({"AWS_PROFILE": ""}, id="blank_aws_profile"),
            pytest.param({"AWS_ROLE_ARN": " "}, id="blank_role_arn"),
        ],
    )
    async def test_blank_value_is_treated_as_unset(
        self, role: Role, run_context: RunContext, blank_key: dict[str, str]
    ) -> None:
        """Blank AWS_PROFILE and AWS_ROLE_ARN values should be treated as absent."""
        secrets = {
            "amazon_s3": {
                **blank_key,
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


class TestSecretValuesNeverEchoed:
    """Preprocessor errors name the key, never the value.

    Masks are exact-substring on the raw leaf; a stripped or escaped copy of
    the value does not match, so the only safe message is a constant one.
    """

    def test_invalid_role_arn_error_omits_the_value(self) -> None:
        padded = "  SUPERSECRET-not-an-arn  "
        with pytest.raises(TracecatCredentialsError) as exc_info:
            AwsAssumeRoleSecretPreprocessor()._get_role_arn(
                {"AWS_ROLE_ARN": padded}, "aws"
            )
        assert padded.strip() not in str(exc_info.value)
        assert "AWS_ROLE_ARN" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_sts_failure_error_omits_the_role_arn(self) -> None:
        role_arn = "arn:aws:iam::123456789012:role/SUPERSECRET-role"
        error = ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": f"not authorized to assume {role_arn}",
                }
            },
            "AssumeRole",
        )
        fake_sts = AsyncMock()
        fake_sts.assume_role = AsyncMock(side_effect=error)
        with (
            patch(
                "tracecat.executor.secret_preprocessors.aioboto3.Session"
            ) as mock_session_cls,
            pytest.raises(TracecatCredentialsError) as exc_info,
        ):
            mock_session = mock_session_cls.return_value
            mock_session.client.return_value.__aenter__.return_value = fake_sts
            await _assume_role_via_irsa(
                role_arn=role_arn,
                external_id="ext",
                workspace_id=str(uuid.uuid4()),
                run_id=str(uuid.uuid4()),
            )
        assert role_arn not in str(exc_info.value)
        assert "AccessDenied" in str(exc_info.value)

    def test_collect_mask_values_covers_stripped_form(self) -> None:
        from tracecat.executor.secret_preprocessors import collect_mask_values

        masks = collect_mask_values([{"aws": {"AWS_ROLE_ARN": "  padded-value \n"}}])
        assert "padded-value" in masks
        assert "  padded-value \n" in masks
