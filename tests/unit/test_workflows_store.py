"""Tests for workflow store functionality."""

import pytest

from tracecat.workflow.store.schemas import validate_short_branch_name


class TestWorkflowPublishBranchValidation:
    """Test Git branch validation helpers for workflow publish."""

    @pytest.mark.parametrize(
        ("branch_name"),
        [
            "main",
            "feature/shared-workflow",
            "fix/workflow_publish",
            "release/2026.02",
        ],
    )
    def test_validate_short_branch_name_accepts_valid_names(
        self, branch_name: str
    ) -> None:
        assert (
            validate_short_branch_name(branch_name, field_name="branch") == branch_name
        )

    @pytest.mark.parametrize(
        ("branch_name"),
        [
            "",
            "refs/heads/main",
            "feature//broken",
            "feature..broken",
            "feature/.hidden",
            "feature/trailing.",
            "-leading-dash",
            "feature/contains space",
            "feature/has@{token}",
            "feature/ends.lock",
            "feature/foo.lock/bar",
        ],
    )
    def test_validate_short_branch_name_rejects_invalid_names(
        self, branch_name: str
    ) -> None:
        with pytest.raises(ValueError):
            validate_short_branch_name(branch_name, field_name="branch")
