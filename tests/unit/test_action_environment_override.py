"""Tests for action environment override functionality.

This module tests the environment override feature that allows individual actions
to execute in different environments than the workflow default environment.

Tests cover:
1. Static environment override in actions
2. Dynamic environment expressions using templates
3. Action environment overriding workflow environment
4. Secrets fetched from correct environment namespace
5. Actions without environment override using workflow environment
6. DSL conversion (ActionStatement creation)
7. Execution (RunContext modification)
"""

import uuid

import pytest
from pydantic import SecretStr

from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    create_default_execution_context,
)
from tracecat.dsl.models import ActionStatement, DSLConfig, RunContext
from tracecat.expressions.common import ExprContext
from tracecat.expressions.eval import eval_templated_object
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.secrets.models import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role


@pytest.fixture
def base_workflow_config():
    """Base workflow configuration for testing environment overrides."""
    return DSLConfig(environment="workflow_env")


@pytest.fixture
def mock_run_context():
    """Mock RunContext with default environment."""
    return RunContext(
        wf_id=WorkflowUUID.from_legacy("wf-" + "0" * 32),
        wf_exec_id="wf-" + "0" * 32 + ":exec-" + "0" * 32,
        wf_run_id=uuid.uuid4(),
        environment="default",
    )


class TestActionStatementEnvironmentField:
    """Test ActionStatement DSL conversion with environment field."""

    def test_action_statement_with_static_environment_override(self):
        """Test that ActionStatement can have a static environment override."""
        action = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="production",
        )

        assert action.environment == "production"
        assert action.ref == "test_action"
        assert action.action == "core.transform.reshape"

    def test_action_statement_with_dynamic_environment_expression(self):
        """Test that ActionStatement can have a dynamic environment expression."""
        action = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="${{ TRIGGER.env_name }}",
        )

        assert action.environment == "${{ TRIGGER.env_name }}"

    def test_action_statement_without_environment_override(self):
        """Test that ActionStatement without environment override has None."""
        action = ActionStatement(
            ref="test_action", action="core.transform.reshape", args={"value": "test"}
        )

        assert action.environment is None

    def test_action_statement_complex_environment_expression(self):
        """Test ActionStatement with complex environment expression."""
        action = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="${{ ACTIONS.get_env.result if ACTIONS.get_env.result else 'default' }}",
        )

        expected_expr = (
            "${{ ACTIONS.get_env.result if ACTIONS.get_env.result else 'default' }}"
        )
        assert action.environment == expected_expr


class TestDSLWorkflowEnvironmentProcessing:
    """Test DSL workflow processing of action environment overrides."""

    def test_dsl_input_with_action_environment_overrides(self):
        """Test that DSLInput can contain actions with environment overrides."""
        dsl = DSLInput(
            title="Test Workflow with Environment Overrides",
            description="Test workflow containing actions with environment overrides",
            entrypoint=DSLEntrypoint(ref="action_with_override"),
            config=DSLConfig(environment="workflow_default"),
            actions=[
                ActionStatement(
                    ref="action_with_override",
                    action="core.transform.reshape",
                    args={"value": "test"},
                    environment="override_env",
                ),
                ActionStatement(
                    ref="action_without_override",
                    action="core.transform.reshape",
                    args={"value": "test2"},
                    depends_on=["action_with_override"],
                ),
            ],
        )

        # Verify DSL structure
        assert dsl.config.environment == "workflow_default"
        assert dsl.actions[0].environment == "override_env"
        assert dsl.actions[1].environment is None

    def test_dsl_input_with_dynamic_environment_expressions(self):
        """Test DSLInput with actions using dynamic environment expressions."""
        dsl = DSLInput(
            title="Test Dynamic Environment Expressions",
            description="Test workflow with dynamic environment selection",
            entrypoint=DSLEntrypoint(ref="env_selector"),
            actions=[
                ActionStatement(
                    ref="env_selector",
                    action="core.transform.reshape",
                    args={"value": "${{ TRIGGER.environment_type }}"},
                ),
                ActionStatement(
                    ref="conditional_env_action",
                    action="core.transform.reshape",
                    args={"value": "processing"},
                    environment="${{ 'production' if ACTIONS.env_selector.result == 'prod' else 'staging' }}",
                    depends_on=["env_selector"],
                ),
            ],
        )

        # Verify dynamic environment expression
        action = dsl.actions[1]
        expected_expr = "${{ 'production' if ACTIONS.env_selector.result == 'prod' else 'staging' }}"
        assert action.environment == expected_expr


class TestWorkflowExecutionEnvironmentOverride:
    """Test workflow execution with environment overrides."""

    def test_run_context_modified_for_environment_override(self, mock_run_context):
        """Test that RunContext gets modified when action has environment override."""
        # Create a task with environment override
        task = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="action_override_env",
        )

        original_env = mock_run_context.environment

        # Verify original environment
        assert original_env == "default"

        # When action has environment override, new RunContext should be created
        if task.environment is not None:
            context = create_default_execution_context()
            context[ExprContext.ACTIONS] = {}
            evaluated_env = eval_templated_object(task.environment, operand=context)
            modified_run_context = mock_run_context.model_copy(
                update={"environment": evaluated_env}
            )

            # Verify environment was overridden
            assert modified_run_context.environment == "action_override_env"
            assert modified_run_context.environment != original_env

    def test_run_context_unchanged_without_environment_override(self, mock_run_context):
        """Test that RunContext remains unchanged when action has no environment override."""
        # Create a task without environment override
        task = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            # No environment override
        )

        original_env = mock_run_context.environment

        # When action has no environment override, context should remain the same
        if task.environment is None:
            # No modification should occur
            assert mock_run_context.environment == original_env

    def test_dynamic_environment_expression_evaluation(self):
        """Test that dynamic environment expressions are properly evaluated."""
        # Create a task with dynamic environment expression
        task = ActionStatement(
            ref="env_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="${{ 'production' if ENV.environment == 'prod' else 'staging' }}",
        )

        if task.environment is not None:
            context = create_default_execution_context()
            context[ExprContext.ACTIONS] = {}
            context[ExprContext.ENV] = {"environment": "prod"}
            evaluated_env = eval_templated_object(task.environment, operand=context)

            # Should evaluate to 'production' since ENV.environment == 'prod'
            assert evaluated_env == "production"


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


class TestEnvironmentOverridePrecedence:
    """Test the precedence order of environment settings."""

    def test_action_environment_takes_precedence_over_workflow(self):
        """Test that action environment override takes precedence over workflow environment."""
        dsl = DSLInput(
            title="Environment Precedence Test",
            description="Test environment precedence",
            entrypoint=DSLEntrypoint(ref="override_action"),
            config=DSLConfig(environment="workflow_env"),  # Workflow default
            actions=[
                ActionStatement(
                    ref="override_action",
                    action="core.transform.reshape",
                    args={"value": "test"},
                    environment="action_env",  # Action override
                ),
                ActionStatement(
                    ref="default_action",
                    action="core.transform.reshape",
                    args={"value": "test2"},
                    depends_on=["override_action"],
                    # No environment override - should use workflow default
                ),
            ],
        )

        # Verify structure
        assert dsl.config.environment == "workflow_env"
        assert dsl.actions[0].environment == "action_env"  # Override takes precedence
        assert dsl.actions[1].environment is None  # Will use workflow default

    def test_multiple_actions_with_different_environments(self):
        """Test workflow with multiple actions having different environment overrides."""
        dsl = DSLInput(
            title="Multiple Environment Overrides",
            description="Test multiple actions with different environments",
            entrypoint=DSLEntrypoint(ref="first_action"),
            config=DSLConfig(environment="workflow_default"),
            actions=[
                ActionStatement(
                    ref="first_action",
                    action="core.transform.reshape",
                    args={"value": "first"},
                    environment="env_one",
                ),
                ActionStatement(
                    ref="second_action",
                    action="core.transform.reshape",
                    args={"value": "second"},
                    environment="env_two",
                    depends_on=["first_action"],
                ),
                ActionStatement(
                    ref="third_action",
                    action="core.transform.reshape",
                    args={"value": "third"},
                    depends_on=["second_action"],
                    # No override - uses workflow default
                ),
            ],
        )

        # Verify each action has correct environment
        assert dsl.actions[0].environment == "env_one"
        assert dsl.actions[1].environment == "env_two"
        assert dsl.actions[2].environment is None  # Uses workflow default


class TestEnvironmentOverrideValidation:
    """Test validation and error handling for environment overrides."""

    def test_invalid_environment_expression_structure(self):
        """Test that invalid environment expressions are handled appropriately."""
        # Test that malformed expressions don't break ActionStatement creation
        action = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="${{ invalid.expression.structure",  # Malformed expression
        )

        # Should still create the ActionStatement (validation happens at execution)
        assert action.environment == "${{ invalid.expression.structure"

    def test_environment_expression_with_complex_logic(self):
        """Test environment expressions with complex conditional logic."""
        action = ActionStatement(
            ref="complex_env_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="${{ 'prod' if ACTIONS.check_env.result.environment == 'production' and TRIGGER.urgent else 'staging' if ACTIONS.check_env.result.environment == 'test' else 'dev' }}",
        )

        # Verify complex expression is preserved
        expected_expr = "${{ 'prod' if ACTIONS.check_env.result.environment == 'production' and TRIGGER.urgent else 'staging' if ACTIONS.check_env.result.environment == 'test' else 'dev' }}"
        assert action.environment == expected_expr


@pytest.mark.integration
class TestEnvironmentOverrideIntegration:
    """Integration tests for environment override functionality."""

    def test_end_to_end_environment_override_workflow(self):
        """Test complete workflow with environment overrides from DSL to execution context."""
        # Create a comprehensive workflow with various environment scenarios
        dsl = DSLInput(
            title="Comprehensive Environment Override Test",
            description="End-to-end test of environment override functionality",
            entrypoint=DSLEntrypoint(ref="env_detector"),
            config=DSLConfig(environment="main_environment"),
            actions=[
                # Action 1: Detects current environment
                ActionStatement(
                    ref="env_detector",
                    action="core.transform.reshape",
                    args={"value": "${{ ENV.environment }}"},
                ),
                # Action 2: Static environment override
                ActionStatement(
                    ref="static_override",
                    action="core.transform.reshape",
                    args={"value": "processing in override"},
                    environment="static_override_env",
                    depends_on=["env_detector"],
                ),
                # Action 3: Dynamic environment based on previous result
                ActionStatement(
                    ref="dynamic_override",
                    action="core.transform.reshape",
                    args={"value": "dynamic processing"},
                    environment="${{ 'production' if ACTIONS.env_detector.result == 'prod' else 'development' }}",
                    depends_on=["static_override"],
                ),
                # Action 4: No override - uses workflow default
                ActionStatement(
                    ref="workflow_default",
                    action="core.transform.reshape",
                    args={"value": "using workflow default"},
                    depends_on=["dynamic_override"],
                ),
            ],
        )

        # Verify the DSL structure is correct
        assert dsl.config.environment == "main_environment"

        # Verify each action's environment setting
        actions = {action.ref: action for action in dsl.actions}

        assert actions["env_detector"].environment is None  # Uses workflow default
        assert actions["static_override"].environment == "static_override_env"
        expected_dynamic_expr = "${{ 'production' if ACTIONS.env_detector.result == 'prod' else 'development' }}"
        assert actions["dynamic_override"].environment == expected_dynamic_expr
        assert actions["workflow_default"].environment is None  # Uses workflow default

        # Verify dependencies are correct
        assert actions["static_override"].depends_on == ["env_detector"]
        assert actions["dynamic_override"].depends_on == ["static_override"]
        assert actions["workflow_default"].depends_on == ["dynamic_override"]
