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
from tracecat.dsl.schemas import ActionStatement, DSLConfig
from tracecat.expressions.common import ExprType
from tracecat.validation.service import (
    ActionEnvPair,
    get_effective_environment,
    validate_dsl_expressions,
)


class TestGetEffectiveEnvironment:
    """Test the get_effective_environment function."""

    @pytest.mark.parametrize(
        "environment, default_env, expected",
        [
            (None, "default_env", "default_env"),
            ("production", "default_env", "production"),
            ("${{ ACTIONS.env_selector.result }}", "default_env", "default_env"),
            (
                "${{ 'prod' if TRIGGER.env == 'production' else 'staging' }}",
                "workflow_env",
                "workflow_env",
            ),
        ],
    )
    def test_get_effective_environment_scenarios(
        self, environment, default_env, expected
    ):
        """Test get_effective_environment with various environment scenarios."""
        stmt = ActionStatement(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment=environment,
        )

        result = get_effective_environment(stmt, default_env)
        assert result == expected

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

    async def test_rejects_non_string_environment_override(self):
        """Test that non-string environment values are rejected."""
        # Create action with non-string environment by bypassing Pydantic validation
        action = ActionStatement.model_construct(
            ref="test_action",
            action="core.transform.reshape",
            args={"value": "test"},
            environment=123,  # Non-string - should be rejected
        )
        dsl = self._create_dsl_with_actions([action])

        results = await validate_dsl_expressions(dsl)

        # Should have error for non-string environment
        env_errors = [r for r in results if r.expression_type == ExprType.ENV]
        assert len(env_errors) == 1
        assert (
            "Template expressions are not allowed in `environment` overrides"
            in env_errors[0].msg
        )
        assert env_errors[0].ref == "test_action"
        assert env_errors[0].status == "error"

    async def test_environment_error_does_not_block_other_validations(self):
        """Test that environment validation errors don't prevent other expression validations."""
        actions = [
            # Action with template environment - should trigger ENV error and early continue
            ActionStatement.model_construct(
                ref="env_error_action",
                action="core.transform.reshape",
                args={"value": "test"},
                environment="${{ ACTIONS.some_action.result }}",  # Template - not allowed
            ),
            # Action with malformed expression - should trigger GENERIC error
            ActionStatement(
                ref="expr_error_action",
                action="core.transform.reshape",
                args={
                    "malformed_expr": "${{ invalid syntax }}"
                },  # Bad expression syntax
            ),
        ]
        dsl = self._create_dsl_with_actions(actions)

        results = await validate_dsl_expressions(dsl)

        # Should have both ENV and GENERIC errors (proves early continue didn't stop other validations)
        env_errors = [r for r in results if r.expression_type == ExprType.ENV]
        other_errors = [r for r in results if r.expression_type != ExprType.ENV]

        assert len(env_errors) == 1
        assert env_errors[0].ref == "env_error_action"
        assert (
            "Template expressions are not allowed in `environment` overrides"
            in env_errors[0].msg
        )

        # Should have at least one non-ENV error, proving validation continued after env error
        assert len(other_errors) >= 1

        # Verify that both error types are present - proves early continue didn't block other validations
        error_types = {r.expression_type for r in results}
        assert ExprType.ENV in error_types
        assert len(error_types) > 1  # At least ENV + one other type


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

            # Create mock IndexedActionResult for get_actions_from_index
            mock_manifest = MagicMock()
            mock_manifest.actions = {
                "core.http_request": MagicMock(),
                "core.transform.reshape": MagicMock(),
            }

            # Mock get_actions_from_index return value using IndexedActionResult-like objects
            mock_result = MagicMock()
            mock_result.manifest = mock_manifest
            mock_actions_data = {
                "core.http_request": mock_result,
                "core.transform.reshape": mock_result,
            }

            mock_registry = AsyncMock()
            mock_registry_service.return_value = mock_registry
            mock_registry.get_actions_from_index.return_value = mock_actions_data

            mock_tg = AsyncMock()
            mock_task_group.return_value.__aenter__.return_value = mock_tg
            mock_task_group.return_value.__aexit__.return_value = None
            mock_tg.create_task = MagicMock()
            mock_tg.results = MagicMock(return_value=[])  # Add missing results method

            # Call the function
            await validate_actions_have_defined_secrets(dsl)

            # Verify that registry service was called with correct action names
            mock_registry.get_actions_from_index.assert_called_once()
            actual_action_names = mock_registry.get_actions_from_index.call_args[0][0]
            assert set(actual_action_names) == {
                "core.http_request",
                "core.transform.reshape",
            }

    async def test_effective_environment_used_in_secret_validation(self):
        """Test that effective environments are used when validating secrets."""
        from unittest.mock import AsyncMock, MagicMock, patch

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

        captured_environments = []
        captured_actions = []

        async def mock_check_action_secrets_from_manifest(
            _secrets_service, _checked_keys, environment, _manifest, action_name
        ):
            """Mock function that captures the environment and action passed to it."""
            captured_environments.append(environment)
            captured_actions.append(action_name)
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
                "tracecat.validation.service.check_action_secrets_from_manifest",
                side_effect=mock_check_action_secrets_from_manifest,
            ),
            patch("tracecat.validation.service.GatheringTaskGroup") as mock_task_group,
        ):
            # Setup mocks
            mock_session = AsyncMock()
            mock_session_mgr.return_value.__aenter__.return_value = mock_session
            mock_session_mgr.return_value.__aexit__.return_value = None

            mock_secrets = AsyncMock()
            mock_secrets_service.return_value = mock_secrets

            # Create mock IndexedActionResult for get_actions_from_index
            mock_manifest = MagicMock()
            mock_manifest.actions = {
                "tools.http.get": MagicMock(),
                "tools.http.post": MagicMock(),
            }

            # Mock get_actions_from_index return value using IndexedActionResult-like objects
            mock_result = MagicMock()
            mock_result.manifest = mock_manifest
            mock_actions_data = {
                "tools.http.get": mock_result,
                "tools.http.post": mock_result,
            }

            mock_registry = AsyncMock()
            mock_registry_service.return_value = mock_registry
            mock_registry.get_actions_from_index.return_value = mock_actions_data

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

                    def results(self):
                        return []  # Return empty results for testing

                return MockTaskGroup()

            mock_task_group.return_value = await mock_task_group_impl()

            # Call the function
            await validate_actions_have_defined_secrets(dsl)

            # Verify that check_action_secrets_from_manifest was called with correct environments
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
