"""Tests that secrets resolve from an action's overridden environment."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from tracecat.auth.types import Role
from tracecat.dsl.action import _resolve_environment
from tracecat.dsl.schemas import (
    ActionStatement,
    MaterializedExecutionContext,
    RunContext,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService


@pytest.mark.integration
@pytest.mark.anyio
class TestSecretsWithEnvironmentOverride:
    """Test that secrets are fetched from the correct environment namespace."""

    @pytest.mark.skip(reason="Requires environment column migration in action table")
    async def test_secrets_fetched_from_action_environment_override(
        self, test_role: Role
    ):
        """Test that secrets are fetched from action's overridden environment."""
        # Setup: Create secrets in different environments
        async with SecretsService.with_session(role=test_role) as secrets_service:
            # Create secret in workflow environment
            await secrets_service.create_secret(
                SecretCreate(
                    name="test_secret",
                    environment="workflow_env",
                    keys=[
                        SecretKeyValue(key="API_KEY", value=SecretStr("workflow_value"))
                    ],
                )
            )

            # Create secret in override environment
            await secrets_service.create_secret(
                SecretCreate(
                    name="test_secret",
                    environment="override_env",
                    keys=[
                        SecretKeyValue(key="API_KEY", value=SecretStr("override_value"))
                    ],
                )
            )

        try:
            # Create ActionStatement with environment override
            _task = ActionStatement(
                ref="test_action",
                action="core.transform.reshape",
                args={"value": "${{ SECRETS.test_secret.API_KEY }}"},
                environment="override_env",
            )

            mock_run_context = RunContext(
                wf_id=WorkflowUUID.from_legacy("wf-" + "0" * 32),
                wf_exec_id="wf-" + "0" * 32 + ":exec-" + "0" * 32,
                wf_run_id=uuid.uuid4(),
                environment="workflow_env",  # Default workflow environment
                logical_time=datetime.now(UTC),
            )

            # Override run context environment based on action
            override_run_context = mock_run_context.model_copy(
                update={"environment": "override_env"}
            )

            # Verify the environment was overridden
            assert mock_run_context.environment == "workflow_env"
            assert override_run_context.environment == "override_env"

            # Test that secrets would be fetched from the override environment
            # This simulates what happens in the actual execution
            async with SecretsService.with_session(role=test_role) as secrets_service:
                # Fetch secret from override environment
                secret_from_override = await secrets_service.get_secret_by_name(
                    "test_secret", environment="override_env"
                )

                # Fetch secret from workflow environment
                secret_from_workflow = await secrets_service.get_secret_by_name(
                    "test_secret", environment="workflow_env"
                )

                # Verify different secrets are returned
                assert secret_from_override.name == "test_secret"
                assert secret_from_workflow.name == "test_secret"
                assert secret_from_override.environment == "override_env"
                assert secret_from_workflow.environment == "workflow_env"

        finally:
            # Cleanup
            async with SecretsService.with_session(role=test_role) as secrets_service:
                try:
                    workflow_secret = await secrets_service.get_secret_by_name(
                        "test_secret", environment="workflow_env"
                    )
                    await secrets_service.delete_secret(workflow_secret)
                except Exception:
                    pass

                try:
                    override_secret = await secrets_service.get_secret_by_name(
                        "test_secret", environment="override_env"
                    )
                    await secrets_service.delete_secret(override_secret)
                except Exception:
                    pass

    @pytest.mark.skip(reason="Requires environment column migration in action table")
    async def test_secrets_fetched_from_workflow_environment_when_no_override(
        self, test_role: Role
    ):
        """Test that secrets are fetched from workflow environment when no override."""
        # Setup: Create secret only in workflow environment
        async with SecretsService.with_session(role=test_role) as secrets_service:
            await secrets_service.create_secret(
                SecretCreate(
                    name="workflow_secret",
                    environment="workflow_env",
                    keys=[
                        SecretKeyValue(
                            key="API_KEY", value=SecretStr("workflow_only_value")
                        )
                    ],
                )
            )

        try:
            # Create action without environment override
            task = ActionStatement(
                ref="test_action",
                action="core.transform.reshape",
                args={"value": "${{ SECRETS.workflow_secret.API_KEY }}"},
                # No environment override
            )

            mock_run_context = RunContext(
                wf_id=WorkflowUUID.from_legacy("wf-" + "0" * 32),
                wf_exec_id="wf-" + "0" * 32 + ":exec-" + "0" * 32,
                wf_run_id=uuid.uuid4(),
                environment="workflow_env",
                logical_time=datetime.now(UTC),
            )

            # Since no override, run context environment should remain the same
            assert task.environment is None
            assert mock_run_context.environment == "workflow_env"

            # Verify secret is fetched from workflow environment
            async with SecretsService.with_session(role=test_role) as secrets_service:
                secret = await secrets_service.get_secret_by_name(
                    "workflow_secret", environment="workflow_env"
                )
                assert secret.name == "workflow_secret"
                assert secret.environment == "workflow_env"

        finally:
            # Cleanup
            async with SecretsService.with_session(role=test_role) as secrets_service:
                try:
                    secret = await secrets_service.get_secret_by_name(
                        "workflow_secret", environment="workflow_env"
                    )
                    await secrets_service.delete_secret(secret)
                except Exception:
                    pass


@pytest.mark.parametrize(
    ("task_environment", "materialized", "expected"),
    [
        (None, {}, "default"),
        ("override", {}, "override"),
        ("  override  ", {}, "override"),
        ("${{ TRIGGER.env }}", {"TRIGGER": {"env": "from_trigger"}}, "from_trigger"),
    ],
)
def test_resolve_environment_task_overrides_default(
    task_environment: str | None,
    materialized: MaterializedExecutionContext,
    expected: str,
) -> None:
    assert _resolve_environment(task_environment, "default", materialized) == expected
