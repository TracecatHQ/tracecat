"""Tests for workflow store functionality."""

from collections.abc import Iterable

import pytest

from tracecat.identifiers.workflow import WorkflowID
from tracecat.workflow.store.schemas import WorkflowSource, validate_short_branch_name


class TestWorkflowSource:
    """Test WorkflowSource model."""

    def test_workflow_source_creation(self):
        """Test WorkflowSource creation with required fields."""
        workflow_id = WorkflowID.new_uuid4()
        source = WorkflowSource(
            id=workflow_id,
            path="workflows/example.yml",
            sha="abc123",
        )
        assert source.path == "workflows/example.yml"
        assert source.sha == "abc123"
        assert source.id == workflow_id
        assert source.version is None

    def test_workflow_source_with_version(self):
        """Test WorkflowSource creation with version."""
        source = WorkflowSource(
            id=WorkflowID.new_uuid4(),
            path="workflows/example.yml",
            sha="abc123",
            version=2,
        )
        assert source.version == 2

    def test_workflow_source_validation(self):
        """Test WorkflowSource field validation."""
        # Test that all required fields are present
        with pytest.raises((TypeError, ValueError)):
            WorkflowSource()  # type: ignore # Missing required fields

        # Test that path is required
        with pytest.raises((TypeError, ValueError)):
            WorkflowSource(sha="abc123", id=WorkflowID.new_uuid4())  # type: ignore

        # Test that sha is required
        with pytest.raises((TypeError, ValueError)):
            WorkflowSource(path="test.yml", id=WorkflowID.new_uuid4())  # type: ignore

        # Test that workflow_id is required
        with pytest.raises((TypeError, ValueError)):
            WorkflowSource(path="test.yml", sha="abc123")  # type: ignore

    def test_workflow_source_serialization(self):
        """Test WorkflowSource can be serialized/deserialized."""
        workflow_id = WorkflowID.new_uuid4()
        source = WorkflowSource(
            path="workflows/example.yml",
            sha="abc123",
            id=workflow_id,
            version=1,
        )

        # Test model dump
        data = source.model_dump()
        assert data == {
            "path": "workflows/example.yml",
            "sha": "abc123",
            "id": workflow_id,
            "version": 1,
        }

        # Test recreation from dict
        recreated = WorkflowSource(**data)
        assert recreated == source


class TestExternalWorkflowStore:
    """Test ExternalWorkflowStore protocol."""

    def test_external_workflow_store_protocol(self):
        """Test that a class can implement ExternalWorkflowStore protocol."""

        class DummyWorkflowStore:
            """Dummy implementation of ExternalWorkflowStore protocol."""

            async def list_sources(self) -> Iterable[WorkflowSource]:
                """List all workflow sources."""
                return [
                    WorkflowSource(
                        path="workflows/example1.yml",
                        sha="abc123",
                        id=WorkflowID.new_uuid4(),
                    ),
                    WorkflowSource(
                        path="workflows/example2.yml",
                        sha="def456",
                        id=WorkflowID.new_uuid4(),
                        version=2,
                    ),
                ]

            async def fetch_yaml(self, path: str, sha: str) -> str:
                """Fetch YAML content for a workflow."""
                return """
title: Example Workflow
description: A test workflow
entrypoint:
  ref: start
  expects:
    inputs: ${FN.get_inputs()}
actions:
  - ref: start
    action: core.transform.passthrough
    args:
      value: ${INPUTS.inputs}
"""

        # Test that the dummy class satisfies the protocol
        store = DummyWorkflowStore()
        assert hasattr(store, "list_sources")
        assert hasattr(store, "fetch_yaml")
        assert callable(store.list_sources)
        assert callable(store.fetch_yaml)

    @pytest.mark.anyio
    async def test_external_workflow_store_usage(self):
        """Test using ExternalWorkflowStore protocol implementation."""

        class TestWorkflowStore:
            def __init__(self):
                self.sources = [
                    WorkflowSource(
                        path="workflows/test.yml",
                        sha="abc123",
                        id=WorkflowID.new_uuid4(),
                    )
                ]
                self.yaml_content = """
title: Test Workflow
description: A test workflow for validation
entrypoint:
  ref: main
  expects:
    inputs: ${{FN.get_inputs()}}
actions:
  - ref: main
    action: core.http_request
    args:
      url: https://api.example.com/test
      method: GET
"""

            async def list_sources(self) -> Iterable[WorkflowSource]:
                return self.sources

            async def fetch_yaml(self, path: str, sha: str) -> str:
                if path == "workflows/test.yml" and sha == "abc123":
                    return self.yaml_content
                raise FileNotFoundError(f"Workflow not found: {path}@{sha}")

        # Test the implementation
        store = TestWorkflowStore()

        # Test list_sources
        sources = await store.list_sources()
        source_list = list(sources)
        assert len(source_list) == 1
        assert source_list[0].path == "workflows/test.yml"
        assert source_list[0].sha == "abc123"

        # Test fetch_yaml
        yaml_content = await store.fetch_yaml("workflows/test.yml", "abc123")
        assert "title: Test Workflow" in yaml_content
        assert "core.http_request" in yaml_content

        # Test fetch_yaml with invalid path/sha
        with pytest.raises(FileNotFoundError):
            await store.fetch_yaml("invalid.yml", "invalid")


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
            "refs/heads/main",
            "feature//broken",
            "feature..broken",
            "feature/.hidden",
            "feature/trailing.",
            "-leading-dash",
            "feature/contains space",
            "feature/has@{token}",
            "feature/ends.lock",
        ],
    )
    def test_validate_short_branch_name_rejects_invalid_names(
        self, branch_name: str
    ) -> None:
        with pytest.raises(ValueError):
            validate_short_branch_name(branch_name, field_name="branch")
