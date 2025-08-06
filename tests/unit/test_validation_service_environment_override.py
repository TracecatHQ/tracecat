"""Tests for validation service environment override functionality.

This module tests the validation logic for action-level environment overrides
introduced in the validation service module.

Tests cover:
1. get_effective_environment function behavior
2. Environment override validation in DSL expressions
3. Action-environment pair handling in secret validation
"""

import pytest

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.models import ActionStatement, DSLConfig
from tracecat.expressions.common import ExprType
from tracecat.validation.service import (
    ActionEnvPair,
    get_effective_environment,
    validate_dsl_expressions,
)


class TestGetEffectiveEnvironment:
    """Test the get_effective_environment function."""

    def test_returns_default_when_no_environment_override(self):
        """Test that default environment is returned when statement has no environment."""
        stmt = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
        )

        result = get_effective_environment(stmt, "default_env")
        assert result == "default_env"

    def test_returns_default_when_environment_is_none(self):
        """Test that default environment is returned when environment is explicitly None."""
        stmt = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment=None,
        )

        result = get_effective_environment(stmt, "default_env")
        assert result == "default_env"

    def test_returns_literal_environment_override(self):
        """Test that literal string environment override is returned."""
        stmt = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="production",
        )

        result = get_effective_environment(stmt, "default_env")
        assert result == "production"

    def test_returns_default_for_template_environment(self):
        """Test that default environment is returned for template expressions."""
        stmt = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="${{ ACTIONS.env_selector.result }}",
        )

        result = get_effective_environment(stmt, "default_env")
        assert result == "default_env"

    def test_returns_default_for_complex_template_environment(self):
        """Test that default environment is returned for complex template expressions."""
        stmt = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="${{ 'prod' if TRIGGER.env == 'production' else 'staging' }}",
        )

        result = get_effective_environment(stmt, "workflow_env")
        assert result == "workflow_env"

    def test_handles_non_string_environment(self):
        """Test that non-string environment values return default."""
        # This should not normally happen due to Pydantic validation, but testing edge case
        stmt = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
        )
        # Manually set environment to non-string (bypassing Pydantic)
        stmt.__dict__["environment"] = 123

        result = get_effective_environment(stmt, "default_env")
        assert result == "default_env"


class TestActionEnvPair:
    """Test the ActionEnvPair dataclass."""

    def test_action_env_pair_creation(self):
        """Test that ActionEnvPair can be created and is hashable."""
        pair = ActionEnvPair(action="core.transform.reshape", environment="production")

        assert pair.action == "core.transform.reshape"
        assert pair.environment == "production"

    def test_action_env_pair_equality(self):
        """Test that ActionEnvPair instances with same values are equal."""
        pair1 = ActionEnvPair(action="core.transform.reshape", environment="production")
        pair2 = ActionEnvPair(action="core.transform.reshape", environment="production")
        pair3 = ActionEnvPair(action="core.transform.reshape", environment="staging")

        assert pair1 == pair2
        assert pair1 != pair3

    def test_action_env_pair_hashable(self):
        """Test that ActionEnvPair instances can be used in sets."""
        pair1 = ActionEnvPair(action="core.transform.reshape", environment="production")
        pair2 = ActionEnvPair(
            action="core.transform.reshape", environment="production"
        )  # Duplicate
        pair3 = ActionEnvPair(action="core.transform.reshape", environment="staging")
        pair4 = ActionEnvPair(action="core.http_request", environment="production")

        pairs_set = {pair1, pair2, pair3, pair4}

        # Should have 3 unique pairs (pair1 and pair2 are identical)
        assert len(pairs_set) == 3
        assert pair1 in pairs_set
        assert pair3 in pairs_set
        assert pair4 in pairs_set


@pytest.mark.anyio
class TestEnvironmentOverrideValidation:
    """Test DSL expression validation with environment overrides."""

    def _create_dsl_with_actions(self, actions: list[ActionStatement]) -> DSLInput:
        """Helper to create DSL bypassing validation during construction."""
        return DSLInput.model_construct(
            title="Test Workflow",
            description="Test workflow",
            entrypoint=DSLEntrypoint(ref=actions[0].ref),
            config=DSLConfig(environment="default_env"),
            actions=actions,
        )

    async def test_allows_literal_string_environment_override(self):
        """Test that literal string environment overrides are allowed."""
        action = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="production",  # Literal string - should be allowed
        )
        dsl = self._create_dsl_with_actions([action])

        results = await validate_dsl_expressions(dsl)

        # Should not have any errors related to environment override
        env_errors = [r for r in results if r.expression_type == ExprType.ENV]
        assert len(env_errors) == 0

    async def test_rejects_template_environment_override(self):
        """Test that template expressions in environment overrides are rejected."""
        action = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment="${{ ACTIONS.env_selector.result }}",  # Template - should be rejected
        )
        dsl = self._create_dsl_with_actions([action])

        results = await validate_dsl_expressions(dsl)

        # Should have error for template expression in environment override
        env_errors = [r for r in results if r.expression_type == ExprType.ENV]
        assert len(env_errors) == 1
        assert (
            "Template expressions are not allowed in `environment` overrides"
            in env_errors[0].msg
        )
        assert env_errors[0].ref == "test_action"
        assert env_errors[0].status == "error"

    async def test_allows_none_environment_override(self):
        """Test that None environment override (no override) is allowed."""
        action = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            # No environment override - should be fine
        )
        dsl = self._create_dsl_with_actions([action])

        results = await validate_dsl_expressions(dsl)

        # Should not have any errors related to environment override
        env_errors = [r for r in results if r.expression_type == ExprType.ENV]
        assert len(env_errors) == 0

    async def test_multiple_actions_mixed_environment_overrides(self):
        """Test validation with multiple actions having different environment override types."""
        actions = [
            ActionStatement(
                ref="action1",
                action="core.transform.reshape",
                args={"value": "test1"},
                environment="production",  # Literal - allowed
            ),
            ActionStatement(
                ref="action2",
                action="core.transform.reshape",
                args={"value": "test2"},
                # No override - allowed
                depends_on=["action1"],
            ),
            ActionStatement(
                ref="action3",
                action="core.transform.reshape",
                args={"value": "test3"},
                environment="${{ ACTIONS.action1.result }}",  # Template - not allowed
                depends_on=["action2"],
            ),
            ActionStatement(
                ref="action4",
                action="core.transform.reshape",
                args={"value": "test4"},
                environment="staging",  # Literal - allowed
                depends_on=["action3"],
            ),
        ]
        dsl = self._create_dsl_with_actions(actions)

        results = await validate_dsl_expressions(dsl)

        # Should have exactly one error for action3's template environment
        env_errors = [r for r in results if r.expression_type == ExprType.ENV]
        assert len(env_errors) == 1
        assert env_errors[0].ref == "action3"
        assert (
            "Template expressions are not allowed in `environment` overrides"
            in env_errors[0].msg
        )


@pytest.mark.anyio
class TestSecretValidationWithEnvironmentOverride:
    """Test secret validation logic with action-environment pairs."""

    async def test_action_env_pairs_generation_with_overrides(self):
        """Test that ActionEnvPair instances are correctly generated from DSL actions."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tracecat.validation.service import validate_actions_have_defined_secrets

        dsl = DSLInput(
            title="Test Workflow",
            description="Test workflow with mixed environment overrides",
            entrypoint=DSLEntrypoint(ref="action1"),
            config=DSLConfig(environment="default_env"),
            actions=[
                ActionStatement(
                    ref="action1",
                    action="core.http_request",
                    args={"url": "https://api.example.com"},
                    environment="production",  # Override to production
                ),
                ActionStatement(
                    ref="action2",
                    action="core.transform.reshape",
                    args={"value": "${{ ACTIONS.action1.result }}"},
                    # No override - should use default_env
                ),
                ActionStatement(
                    ref="action3",
                    action="core.http_request",
                    args={"url": "https://api2.example.com"},
                    environment="staging",  # Override to staging
                ),
                ActionStatement(
                    ref="action4",
                    action="core.http_request",
                    args={"url": "https://api3.example.com"},
                    environment="production",  # Same as action1 - should dedupe
                ),
            ],
        )

        # Mock the database session and services
        with (
            patch(
                "tracecat.validation.service.get_async_session_context_manager"
            ) as mock_session_mgr,
            patch("tracecat.validation.service.SecretsService") as mock_secrets_service,
            patch(
                "tracecat.validation.service.RegistryActionsService"
            ) as mock_registry_service,
            patch("tracecat.validation.service.GatheringTaskGroup") as mock_task_group,
        ):
            # Setup mocks
            mock_session = AsyncMock()
            mock_session_mgr.return_value.__aenter__.return_value = mock_session
            mock_session_mgr.return_value.__aexit__.return_value = None

            mock_secrets = AsyncMock()
            mock_secrets_service.return_value = mock_secrets

            mock_registry = AsyncMock()
            mock_registry_service.return_value = mock_registry
            mock_registry.list_actions.return_value = []  # No registry actions found

            mock_tg = AsyncMock()
            mock_task_group.return_value.__aenter__.return_value = mock_tg
            mock_task_group.return_value.__aexit__.return_value = None
            mock_tg.create_task = MagicMock()

            # Call the function
            await validate_actions_have_defined_secrets(dsl)

            # Verify that registry service was called with correct action keys
            expected_action_keys = {"core.http_request", "core.transform.reshape"}
            mock_registry.list_actions.assert_called_once_with(
                include_keys=expected_action_keys
            )

    async def test_effective_environment_used_in_secret_validation(self):
        """Test that effective environments are used when validating secrets."""
        from unittest.mock import AsyncMock, patch

        from tracecat.validation.service import validate_actions_have_defined_secrets

        dsl = DSLInput(
            title="Test Workflow",
            description="Test workflow for secret validation",
            entrypoint=DSLEntrypoint(ref="action1"),
            config=DSLConfig(environment="workflow_env"),
            actions=[
                ActionStatement(
                    ref="action1",
                    action="tools.http.get",
                    args={"url": "https://api.example.com"},
                    environment="override_env",  # Should use override_env for secrets
                ),
                ActionStatement(
                    ref="action2",
                    action="tools.http.post",
                    args={"url": "https://api2.example.com"},
                    # No override - should use workflow_env for secrets
                ),
            ],
        )

        # Create simple mock registry actions
        mock_registry_action1 = AsyncMock()
        mock_registry_action1.action = "tools.http.get"
        mock_registry_action1.definition = {"secrets": ["api_token"]}

        mock_registry_action2 = AsyncMock()
        mock_registry_action2.action = "tools.http.post"
        mock_registry_action2.definition = {"secrets": ["api_key"]}

        captured_environments = []
        captured_actions = []

        async def mock_check_action_secrets(
            _secrets_service, _registry_service, _checked_keys, environment, action
        ):
            """Mock function that captures the environment and action passed to it."""
            captured_environments.append(environment)
            captured_actions.append(action.action)
            return []  # Return empty results

        with (
            patch(
                "tracecat.validation.service.get_async_session_context_manager"
            ) as mock_session_mgr,
            patch("tracecat.validation.service.SecretsService") as mock_secrets_service,
            patch(
                "tracecat.validation.service.RegistryActionsService"
            ) as mock_registry_service,
            patch(
                "tracecat.validation.service.check_action_secrets",
                side_effect=mock_check_action_secrets,
            ),
            patch("tracecat.validation.service.GatheringTaskGroup") as mock_task_group,
        ):
            # Setup mocks
            mock_session = AsyncMock()
            mock_session_mgr.return_value.__aenter__.return_value = mock_session
            mock_session_mgr.return_value.__aexit__.return_value = None

            mock_secrets = AsyncMock()
            mock_secrets_service.return_value = mock_secrets

            mock_registry = AsyncMock()
            mock_registry_service.return_value = mock_registry
            mock_registry.list_actions.return_value = [
                mock_registry_action1,
                mock_registry_action2,
            ]

            # Mock task group to actually execute tasks synchronously for testing
            async def mock_task_group_impl():
                # Simulate the behavior of the actual task group
                tasks = []

                class MockTaskGroup:
                    def create_task(self, coro):
                        tasks.append(coro)

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *args):
                        # Execute all tasks
                        for task in tasks:
                            await task
                        return None

                return MockTaskGroup()

            mock_task_group.return_value = await mock_task_group_impl()

            # Call the function
            await validate_actions_have_defined_secrets(dsl)

            # Verify that check_action_secrets was called with correct environments
            assert len(captured_environments) == 2
            assert len(captured_actions) == 2

            # Should be called with override_env for action1 and workflow_env for action2
            # Note: Order may vary due to set iteration, so check both combinations
            env_action_pairs = list(
                zip(captured_environments, captured_actions, strict=False)
            )

            expected_pairs = [
                ("override_env", "tools.http.get"),
                ("workflow_env", "tools.http.post"),
            ]

            # Check that all expected pairs are present (order doesn't matter)
            for expected_env, expected_action in expected_pairs:
                assert (expected_env, expected_action) in env_action_pairs


class TestEnvironmentOverrideIntegration:
    """Integration tests for environment override validation logic."""

    def test_get_effective_environment_with_real_action_statements(self):
        """Test get_effective_environment with realistic ActionStatement instances."""
        # Action without environment override
        action1 = ActionStatement(
            ref="fetch_data",
            action="core.http_request",
            args={
                "url": "https://api.example.com/data",
                "method": "GET",
            },
        )

        # Action with literal environment override
        action2 = ActionStatement(
            ref="process_data",
            action="core.transform.reshape",
            args={"value": "${{ ACTIONS.fetch_data.result }}"},
            environment="production",
        )

        # Action with template environment (should use default)
        action3 = ActionStatement(
            ref="store_data",
            action="core.http_request",
            args={
                "url": "https://api.example.com/store",
                "method": "POST",
                "payload": "${{ ACTIONS.process_data.result }}",
            },
            environment="${{ 'prod' if TRIGGER.is_urgent else 'staging' }}",
        )

        default_env = "workflow_default"

        # Test effective environments
        assert get_effective_environment(action1, default_env) == "workflow_default"
        assert get_effective_environment(action2, default_env) == "production"
        assert get_effective_environment(action3, default_env) == "workflow_default"

    def test_action_env_pairs_deduplication(self):
        """Test that ActionEnvPair deduplicates properly in sets."""
        # Simulate multiple actions using same action type and environment
        pairs = [
            ActionEnvPair(action="core.http_request", environment="production"),
            ActionEnvPair(
                action="core.http_request", environment="production"
            ),  # Duplicate
            ActionEnvPair(action="core.http_request", environment="staging"),
            ActionEnvPair(action="core.transform.reshape", environment="production"),
            ActionEnvPair(
                action="core.transform.reshape", environment="production"
            ),  # Duplicate
        ]

        unique_pairs = set(pairs)

        # Should have 3 unique pairs
        assert len(unique_pairs) == 3
        expected_pairs = {
            ActionEnvPair(action="core.http_request", environment="production"),
            ActionEnvPair(action="core.http_request", environment="staging"),
            ActionEnvPair(action="core.transform.reshape", environment="production"),
        }
        assert unique_pairs == expected_pairs
