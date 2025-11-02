"""Tests for validation equivalence between Pydantic models and registry actions.

This test module ensures that validation behaves consistently for:
1. ExecuteChildWorkflowArgs model vs core.workflow.execute registry action
2. ApprovalsAgentActionArgs model vs ai.approvals_agent registry action

These tests are critical because validate_registry_action_args uses the models directly
for special cases (PlatformAction.CHILD_WORKFLOW_EXECUTE and PlatformAction.AI_APPROVALS_AGENT)
instead of the registry action schemas.

Note: We test validation behavior rather than schema equivalence because ExecuteChildWorkflowArgs
has custom validators that prevent JSON schema generation.
"""

# Enable agent-approvals feature flag before any imports
# This ensures ai.approvals_agent is always available for testing
import os

_original_flags = os.environ.get("TRACECAT__FEATURE_FLAGS", "")
_flags = {f.strip() for f in _original_flags.split(",") if f.strip()}
_flags.add("agent-approvals")
os.environ["TRACECAT__FEATURE_FLAGS"] = ",".join(_flags)

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from tracecat.registry.actions.models import RegistryValidationError  # noqa: E402


@pytest.fixture(scope="module")
def registry_repo():
    """Create a registry repository with all actions loaded."""
    from tracecat.registry.repository import Repository

    repo = Repository()
    repo.init()
    return repo


def test_execute_child_workflow_validation_equivalence(registry_repo):
    """Test that ExecuteChildWorkflowArgs validation is compatible with core.workflow.execute.

    This ensures that inputs that pass model validation would also be accepted by the
    registry action, and vice versa.
    """
    from tracecat.dsl.common import ExecuteChildWorkflowArgs

    # Get the registry action
    action = registry_repo.get("core.workflow.execute")
    assert action is not None, "core.workflow.execute action not found in registry"

    # Valid args that should work for both
    valid_args = {
        "workflow_alias": "test_workflow",
        "trigger_inputs": {"key": "value"},
        "environment": "production",
        "timeout": 30.0,
        "version": 1,
        "loop_strategy": "parallel",
        "batch_size": 16,
        "fail_strategy": "isolated",
        "wait_strategy": "wait",
    }

    # Both should validate successfully
    model_validated = ExecuteChildWorkflowArgs.model_validate(valid_args)
    assert model_validated.workflow_alias == "test_workflow"

    action_validated = action.validate_args(valid_args)
    assert action_validated["workflow_alias"] == "test_workflow"

    # Invalid args - missing workflow_alias and workflow_id
    invalid_args = {
        "trigger_inputs": {"key": "value"},
    }

    # Both should fail validation
    with pytest.raises(ValidationError):
        ExecuteChildWorkflowArgs.model_validate(invalid_args)

    with pytest.raises(RegistryValidationError):
        action.validate_args(invalid_args)


def test_approvals_agent_validation_equivalence(registry_repo):
    """Test that ApprovalsAgentActionArgs validation is compatible with ai.approvals_agent.

    This ensures that inputs that pass model validation would also be accepted by the
    registry action, and vice versa.
    """
    # Skip if EE package is not installed
    pytest.importorskip("tracecat_ee.agent.actions")

    from tracecat_ee.agent.actions import ApprovalsAgentActionArgs

    # Skip if action is not available (feature flag not enabled)
    try:
        action = registry_repo.get("ai.approvals_agent")
    except KeyError:
        pytest.skip(
            "ai.approvals_agent action not available (feature flag not enabled)"
        )

    assert action is not None, "ai.approvals_agent action not found in registry"

    # Valid args that should work for both
    valid_args = {
        "user_prompt": "Test prompt",
        "model_name": "gpt-4",
        "model_provider": "openai",
        "actions": ["tools.slack.post_message"],
        "tool_approvals": {"tools.slack.post_message": True},
        "instructions": "Test instructions",
        "max_requests": 20,
        "retries": 2,
    }

    # Both should validate successfully
    model_validated = ApprovalsAgentActionArgs.model_validate(valid_args)
    assert model_validated.user_prompt == "Test prompt"

    action_validated = action.validate_args(valid_args)
    assert action_validated["user_prompt"] == "Test prompt"

    # Invalid args - missing required fields
    invalid_args = {
        "user_prompt": "Test prompt",
        # Missing model_name and model_provider
    }

    # Both should fail validation
    with pytest.raises(ValidationError):
        ApprovalsAgentActionArgs.model_validate(invalid_args)

    with pytest.raises(RegistryValidationError):
        action.validate_args(invalid_args)
