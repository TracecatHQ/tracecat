"""Tests for host-side AWS AssumeRole in executor service.

Covers _build_execution_secrets_for_action and integration with
prepare_resolved_context / ResolvedContext.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from tracecat.auth.types import Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.exceptions import TracecatCredentialsError
from tracecat.executor.schemas import ActionImplementation, ResolvedContext
from tracecat.executor.service import (
    _AWS_ROLE_ARN_PATTERN,
    _assume_role_via_irsa,
    _build_execution_secrets_for_action,
    _prepare_step_context,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
) -> dict:
    return {
        secret_name: {
            "AWS_ROLE_ARN": role_arn,
            "AWS_REGION": "us-east-1",
        },
        "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
    }


def _mock_assume_role(**kwargs: object) -> AsyncMock:
    """Create a mock for _assume_role_via_irsa that returns temp creds."""
    return AsyncMock(return_value=_TEMP_CREDS)


def _step_input(run_context: RunContext, step_action: str) -> RunActionInput:
    return RunActionInput(
        task=ActionStatement(ref="template", action="testing.parent_template", args={}),
        exec_context=create_default_execution_context(),
        run_context=run_context,
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "v1"},
            actions={
                "testing.parent_template": "tracecat_registry",
                step_action: "tracecat_registry",
            },
        ),
    )


def _parent_resolved_context(
    *, run_context: RunContext, secrets: dict[str, object]
) -> ResolvedContext:
    return ResolvedContext(
        secrets=secrets,
        execution_secrets=copy.deepcopy(secrets),
        variables={"ENV_NAME": "default"},
        action_impl=ActionImplementation(
            type="template",
            action_name="testing.parent_template",
            template_definition={},
        ),
        evaluated_args={},
        workspace_id=str(_WORKSPACE_ID),
        workflow_id=str(run_context.wf_id),
        run_id=str(run_context.wf_run_id),
        executor_token="test-token",
        logical_time=run_context.logical_time,
    )


# ---------------------------------------------------------------------------
# ARN regex
# ---------------------------------------------------------------------------


class TestAWSRoleArnPattern:
    def test_valid_arn(self) -> None:
        assert _AWS_ROLE_ARN_PATTERN.match("arn:aws:iam::123456789012:role/my-role")

    def test_valid_govcloud_arn(self) -> None:
        assert _AWS_ROLE_ARN_PATTERN.match(
            "arn:aws-us-gov:iam::123456789012:role/my-role"
        )

    def test_valid_china_arn(self) -> None:
        assert _AWS_ROLE_ARN_PATTERN.match("arn:aws-cn:iam::123456789012:role/my-role")

    def test_valid_arn_with_path(self) -> None:
        assert _AWS_ROLE_ARN_PATTERN.match(
            "arn:aws:iam::123456789012:role/path/to/role"
        )

    def test_invalid_arn_missing_prefix(self) -> None:
        assert not _AWS_ROLE_ARN_PATTERN.match("123456789012:role/my-role")

    def test_invalid_arn_wrong_service(self) -> None:
        assert not _AWS_ROLE_ARN_PATTERN.match("arn:aws:s3:::my-bucket")


# ---------------------------------------------------------------------------
# _assume_role_via_irsa
# ---------------------------------------------------------------------------


class TestAssumeRoleViaIrsa:
    @pytest.mark.anyio
    async def test_sts_client_error_raises_credentials_error(self) -> None:
        """STS ClientError is wrapped in TracecatCredentialsError."""
        mock_sts_client = AsyncMock()
        mock_sts_client.assume_role = AsyncMock(
            side_effect=ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                "AssumeRole",
            )
        )

        with (
            patch("tracecat.executor.service.aioboto3.Session") as mock_session_cls,
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
        """Successful STS call returns credential dict."""
        mock_sts_client = AsyncMock()
        mock_sts_client.assume_role = AsyncMock(
            return_value={"Credentials": _TEMP_CREDS}
        )

        with patch("tracecat.executor.service.aioboto3.Session") as mock_session_cls:
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


# ---------------------------------------------------------------------------
# _build_execution_secrets_for_action
# ---------------------------------------------------------------------------

_IRSA_PATCH = "tracecat.executor.service._assume_role_via_irsa"


class TestBuildExecutionSecretsForAction:
    """Unit tests for _build_execution_secrets_for_action."""

    @pytest.mark.anyio
    async def test_non_aws_action_returns_deep_copy(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Non-AWS actions get an unchanged deep copy."""
        secrets = {
            "crowdstrike": {
                "CROWDSTRIKE_CLIENT_ID": "abc",
                "CROWDSTRIKE_CLIENT_SECRET": "xyz",
            }
        }
        result = await _build_execution_secrets_for_action(
            action_name="tools.crowdstrike.list_detections",
            secrets=secrets,
            role=role,
            run_context=run_context,
        )
        assert result == secrets
        # Must be a deep copy, not the same object
        assert result is not secrets
        assert result["crowdstrike"] is not secrets["crowdstrike"]

    @pytest.mark.anyio
    async def test_s3_parse_uri_skips_assume_role(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Parse-only S3 helpers should not depend on runtime AWS credentials."""
        secrets = _base_aws_secrets()
        mock_assume_role = _mock_assume_role()

        with patch(_IRSA_PATCH, mock_assume_role):
            result = await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.parse_uri",
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        assert result == secrets
        mock_assume_role.assert_not_awaited()

    @pytest.mark.anyio
    async def test_s3_action_with_role_arn_rewrites_execution_secrets(
        self, role: Role, run_context: RunContext
    ) -> None:
        """AWS S3 action with AWS_ROLE_ARN replaces it with temp creds."""
        secrets = _base_aws_secrets()
        original_secrets = copy.deepcopy(secrets)

        with patch(_IRSA_PATCH, _mock_assume_role()):
            result = await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.list_objects",
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        # Original secrets must be untouched
        assert secrets == original_secrets

        # Execution secrets should have temp creds
        s3_exec = result["amazon_s3"]
        assert "AWS_ROLE_ARN" not in s3_exec
        assert s3_exec["AWS_ACCESS_KEY_ID"] == "ASIA_TEMP_KEY"
        assert s3_exec["AWS_SECRET_ACCESS_KEY"] == "temp_secret"
        assert s3_exec["AWS_SESSION_TOKEN"] == "temp_token"
        assert s3_exec["AWS_REGION"] == "us-east-1"

    @pytest.mark.anyio
    async def test_boto3_action_with_role_arn_rewrites_execution_secrets(
        self, role: Role, run_context: RunContext
    ) -> None:
        """AWS boto3 action with AWS_ROLE_ARN replaces it with temp creds."""
        secrets = _base_aws_secrets(secret_name="aws")
        original_secrets = copy.deepcopy(secrets)

        with patch(_IRSA_PATCH, _mock_assume_role()):
            result = await _build_execution_secrets_for_action(
                action_name="tools.aws_boto3.call_api",
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        # Original secrets must be untouched
        assert secrets == original_secrets

        aws_exec = result["aws"]
        assert "AWS_ROLE_ARN" not in aws_exec
        assert aws_exec["AWS_ACCESS_KEY_ID"] == "ASIA_TEMP_KEY"

    @pytest.mark.anyio
    async def test_custom_role_session_name_is_forwarded(
        self, role: Role, run_context: RunContext
    ) -> None:
        """AWS_ROLE_SESSION_NAME is forwarded to host-side AssumeRole."""
        secrets = _base_aws_secrets()
        secrets["amazon_s3"]["AWS_ROLE_SESSION_NAME"] = "custom-audit-session"
        mock_assume_role = _mock_assume_role()

        with patch(_IRSA_PATCH, mock_assume_role):
            await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.list_objects",
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
        """Whitespace around AWS_ROLE_SESSION_NAME is removed before STS."""
        secrets = _base_aws_secrets()
        secrets["amazon_s3"]["AWS_ROLE_SESSION_NAME"] = "  custom-audit-session  "
        mock_assume_role = _mock_assume_role()

        with patch(_IRSA_PATCH, mock_assume_role):
            await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.list_objects",
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
        """Invalid ARN format raises TracecatCredentialsError."""
        secrets = _base_aws_secrets(role_arn="not-a-valid-arn")

        with pytest.raises(TracecatCredentialsError, match="Invalid AWS role ARN"):
            await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.list_objects",
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

    @pytest.mark.anyio
    async def test_missing_external_id_raises_credentials_error(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Missing external ID raises TracecatCredentialsError."""
        secrets = {
            "amazon_s3": {
                "AWS_ROLE_ARN": _VALID_ROLE_ARN,
                "AWS_REGION": "us-east-1",
            },
            # No TRACECAT_AWS_EXTERNAL_ID
        }

        with pytest.raises(TracecatCredentialsError, match="Missing runtime AWS"):
            await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.list_objects",
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

    @pytest.mark.anyio
    async def test_sts_failure_raises_credentials_error(
        self, role: Role, run_context: RunContext
    ) -> None:
        """STS assume_role failure raises TracecatCredentialsError."""
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
            await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.list_objects",
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

    @pytest.mark.anyio
    async def test_aws_profile_raises_credentials_error(
        self, role: Role, run_context: RunContext
    ) -> None:
        """AWS_PROFILE in secrets raises TracecatCredentialsError."""
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
            await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.list_objects",
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

    @pytest.mark.anyio
    async def test_blank_aws_profile_is_treated_as_unset(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Blank AWS_PROFILE values are ignored for backward compatibility."""
        secrets = {
            "amazon_s3": {
                "AWS_PROFILE": "",
                "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
                "AWS_SECRET_ACCESS_KEY": "static_secret",
                "AWS_REGION": "us-west-2",
            },
            "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
        }

        result = await _build_execution_secrets_for_action(
            action_name="tools.amazon_s3.list_objects",
            secrets=secrets,
            role=role,
            run_context=run_context,
        )

        assert result["amazon_s3"] == {
            "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
            "AWS_SECRET_ACCESS_KEY": "static_secret",
            "AWS_REGION": "us-west-2",
        }

    @pytest.mark.anyio
    async def test_blank_role_arn_is_treated_as_unset(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Blank AWS_ROLE_ARN values are ignored for backward compatibility."""
        secrets = {
            "amazon_s3": {
                "AWS_ROLE_ARN": " ",
                "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
                "AWS_SECRET_ACCESS_KEY": "static_secret",
                "AWS_REGION": "us-west-2",
            },
            "TRACECAT_AWS_EXTERNAL_ID": _EXTERNAL_ID,
        }

        result = await _build_execution_secrets_for_action(
            action_name="tools.amazon_s3.list_objects",
            secrets=secrets,
            role=role,
            run_context=run_context,
        )

        assert result["amazon_s3"] == {
            "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
            "AWS_SECRET_ACCESS_KEY": "static_secret",
            "AWS_REGION": "us-west-2",
        }

    @pytest.mark.anyio
    async def test_static_keys_pass_through_unchanged(
        self, role: Role, run_context: RunContext
    ) -> None:
        """AWS action with static keys (no role ARN) passes through unchanged."""
        secrets = {
            "amazon_s3": {
                "AWS_ACCESS_KEY_ID": "AKIA_STATIC",
                "AWS_SECRET_ACCESS_KEY": "static_secret",
                "AWS_REGION": "us-west-2",
            },
        }
        result = await _build_execution_secrets_for_action(
            action_name="tools.amazon_s3.list_objects",
            secrets=secrets,
            role=role,
            run_context=run_context,
        )
        assert result["amazon_s3"] == secrets["amazon_s3"]
        assert result["amazon_s3"] is not secrets["amazon_s3"]  # deep copy


class TestSecretExpressionNoRegression:
    """Verify that raw secrets used for expression evaluation are never mutated."""

    @pytest.mark.anyio
    async def test_raw_secrets_unchanged_after_build(
        self, role: Role, run_context: RunContext
    ) -> None:
        """SECRETS.amazon_s3.AWS_ROLE_ARN still resolves to the raw ARN."""
        secrets = _base_aws_secrets()
        original_arn = secrets["amazon_s3"]["AWS_ROLE_ARN"]

        with patch(_IRSA_PATCH, _mock_assume_role()):
            await _build_execution_secrets_for_action(
                action_name="tools.amazon_s3.list_objects",
                secrets=secrets,
                role=role,
                run_context=run_context,
            )

        # Original secrets dict must be completely untouched
        assert secrets["amazon_s3"]["AWS_ROLE_ARN"] == original_arn
        assert "AWS_ACCESS_KEY_ID" not in secrets["amazon_s3"]

    @pytest.mark.anyio
    async def test_non_aws_secrets_identical_in_execution_copy(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Non-AWS secrets in an AWS action are preserved in execution_secrets."""
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
        result = await _build_execution_secrets_for_action(
            action_name="tools.amazon_s3.list_objects",
            secrets=secrets,
            role=role,
            run_context=run_context,
        )
        assert result["other_secret"] == {"API_KEY": "some-key"}


class TestPrepareStepContext:
    """Verify template steps derive execution secrets per child action."""

    @pytest.mark.anyio
    async def test_aws_step_recomputes_execution_secrets(
        self, role: Role, run_context: RunContext
    ) -> None:
        """AWS child steps should assume role on the host even under templates."""
        secrets = _base_aws_secrets()
        parent_resolved = _parent_resolved_context(
            run_context=run_context, secrets=secrets
        )
        step_input = _step_input(run_context, "tools.amazon_s3.list_objects")
        mock_assume_role = _mock_assume_role()

        with (
            patch(
                "tracecat.executor.service.registry_resolver.resolve_action",
                AsyncMock(
                    return_value=ActionImplementation(
                        type="udf", action_name="tools.amazon_s3.list_objects"
                    )
                ),
            ),
            patch(_IRSA_PATCH, mock_assume_role),
        ):
            step_resolved = await _prepare_step_context(
                step_action="tools.amazon_s3.list_objects",
                evaluated_args={"bucket": "example"},
                parent_resolved=parent_resolved,
                input=step_input,
                role=role,
            )

        mock_assume_role.assert_awaited_once()
        assert step_resolved.secrets == secrets
        assert (
            step_resolved.execution_secrets["amazon_s3"]["AWS_ACCESS_KEY_ID"]
            == "ASIA_TEMP_KEY"
        )
        assert "AWS_ROLE_ARN" not in step_resolved.execution_secrets["amazon_s3"]
        assert parent_resolved.secrets == secrets
        assert parent_resolved.execution_secrets == secrets

    @pytest.mark.anyio
    async def test_exempt_step_keeps_raw_execution_secrets(
        self, role: Role, run_context: RunContext
    ) -> None:
        """Exempt helper steps should not trigger host-side AssumeRole."""
        secrets = _base_aws_secrets()
        parent_resolved = _parent_resolved_context(
            run_context=run_context, secrets=secrets
        )
        step_input = _step_input(run_context, "tools.amazon_s3.parse_uri")
        mock_assume_role = _mock_assume_role()

        with (
            patch(
                "tracecat.executor.service.registry_resolver.resolve_action",
                AsyncMock(
                    return_value=ActionImplementation(
                        type="udf", action_name="tools.amazon_s3.parse_uri"
                    )
                ),
            ),
            patch(_IRSA_PATCH, mock_assume_role),
        ):
            step_resolved = await _prepare_step_context(
                step_action="tools.amazon_s3.parse_uri",
                evaluated_args={"uri": "s3://bucket/key"},
                parent_resolved=parent_resolved,
                input=step_input,
                role=role,
            )

        mock_assume_role.assert_not_awaited()
        assert step_resolved.execution_secrets == secrets
        assert step_resolved.execution_secrets is not parent_resolved.execution_secrets
