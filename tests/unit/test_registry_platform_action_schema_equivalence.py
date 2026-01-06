"""Tests for validation equivalence between Pydantic models and registry actions.

This test module ensures that validation behaves consistently for
ExecuteChildWorkflowArgs vs core.workflow.execute.

Note: We test validation behavior rather than schema equivalence because
ExecuteChildWorkflowArgs has custom validators that prevent JSON schema generation.
"""

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from tracecat.exceptions import RegistryValidationError  # noqa: E402


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
