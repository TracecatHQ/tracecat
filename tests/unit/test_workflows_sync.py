"""Tests for workflow sync functionality."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.models import ActionStatement, GatherArgs, ScatterArgs
from tracecat.identifiers.workflow import WorkflowUUID, WorkspaceUUID
from tracecat.store.core import WorkflowSource
from tracecat.store.sync import upsert_workflow_definitions


@pytest.fixture
def test_dsl() -> DSLInput:
    """Fixture providing a sample DSL for testing."""
    return DSLInput(
        title="Hierarchical stream variable lookup: a -> scatter -> b -> gather",
        description=(
            "Test that a value produced before a scatter can be accessed by a reshape inside the scatter, "
            "and then gathered. This validates hierarchical stream variable lookup."
        ),
        entrypoint=DSLEntrypoint(ref="gather1"),
        actions=[
            # a: produce a value
            ActionStatement(
                ref="a",
                action="core.transform.reshape",
                args={"value": 42},
            ),
            # scatter: scatter over a collection
            ActionStatement(
                ref="scatter1",
                action="core.transform.scatter",
                depends_on=["a"],
                args=ScatterArgs(collection=[1, 2, 3]).model_dump(),
            ),
            # b: inside scatter, access a's value and ad to item
            ActionStatement(
                ref="b",
                action="core.transform.reshape",
                depends_on=["scatter1"],
                args={"value": "${{ ACTIONS.a.result + ACTIONS.scatter1.result }}"},
            ),
            # gather: collect results from b
            ActionStatement(
                ref="gather1",
                action="core.transform.gather",
                depends_on=["b"],
                args=GatherArgs(items="${{ ACTIONS.b.result }}").model_dump(),
            ),
        ],
    )


class TestUpsertWorkflowDefinitions:
    """Test upsert_workflow_definitions function."""

    @pytest.mark.anyio
    async def test_upsert_workflow_definitions_success(self, test_dsl: DSLInput):
        """Test successful workflow definitions upsert."""
        # Sample workflow sources
        sources = [
            WorkflowSource(
                path="workflows/example1.yml",
                sha="abc123",
                workflow_id=WorkflowUUID.new_uuid4(),
            ),
            WorkflowSource(
                path="workflows/example2.yml",
                sha="def456",
                workflow_id=WorkflowUUID.new_uuid4(),
                version=2,
            ),
        ]

        # Mock YAML content
        yaml_content = test_dsl.dump_yaml()

        # Mock fetch_yaml function
        async def mock_fetch_yaml(_path: str, _sha: str) -> str:
            return yaml_content

        # Mock service and definition
        mock_service = AsyncMock()
        mock_definition = MagicMock()
        mock_definition.version = 1
        mock_service.create_workflow_definition.return_value = mock_definition
        mock_service.session = AsyncMock()

        workspace_id = WorkspaceUUID.new_uuid4()

        with patch(
            "tracecat.workflow.management.definitions.WorkflowDefinitionsService.with_session"
        ) as mock_service_ctx:
            # Mock the async context manager
            mock_service_ctx.return_value.__aenter__.return_value = mock_service
            mock_service_ctx.return_value.__aexit__.return_value = None

            # Call the function
            await upsert_workflow_definitions(
                sources=sources,
                fetch_yaml=mock_fetch_yaml,
                commit_sha="commit123",
                workspace_id=workspace_id,
            )

        # Verify service was called correctly
        assert mock_service.create_workflow_definition.call_count == 2
        mock_service.session.commit.assert_called_once()

    @pytest.mark.anyio
    async def test_upsert_workflow_definitions_yaml_parsing(self, test_dsl: DSLInput):
        """Test YAML parsing in workflow definitions upsert."""
        sources = [
            WorkflowSource(
                path="workflows/test.yml",
                sha="abc123",
                workflow_id=WorkflowUUID.new_uuid4(),
            ),
        ]

        async def mock_fetch_yaml(path: str, sha: str) -> str:
            assert path == "workflows/test.yml"
            assert sha == "abc123"
            return test_dsl.dump_yaml()

        mock_service = AsyncMock()
        mock_definition = MagicMock()
        mock_definition.version = 1
        mock_service.create_workflow_definition.return_value = mock_definition
        mock_service.session = AsyncMock()

        with patch(
            "tracecat.workflow.management.definitions.WorkflowDefinitionsService.with_session"
        ) as mock_service_ctx:
            mock_service_ctx.return_value.__aenter__.return_value = mock_service
            mock_service_ctx.return_value.__aexit__.return_value = None

            await upsert_workflow_definitions(
                sources=sources,
                fetch_yaml=mock_fetch_yaml,
                commit_sha="commit123",
                workspace_id=WorkspaceUUID.new_uuid4(),
            )

        # Verify the DSL was created correctly
        call_args = mock_service.create_workflow_definition.call_args
        dsl = call_args[1]["dsl"]  # Get the DSL from kwargs
        assert (
            dsl.title
            == "Hierarchical stream variable lookup: a -> scatter -> b -> gather"
        )
        assert (
            dsl.description
            == "Test that a value produced before a scatter can be accessed by a reshape inside the scatter, and then gathered. This validates hierarchical stream variable lookup."
        )

    @pytest.mark.anyio
    async def test_upsert_workflow_definitions_metadata(self, test_dsl: DSLInput):
        """Test metadata handling in workflow definitions upsert."""
        sources = [
            WorkflowSource(
                path="workflows/meta-test.yml",
                sha="meta123",
                workflow_id=WorkflowUUID.new_uuid4(),
            ),
        ]

        async def mock_fetch_yaml(_path: str, _sha: str) -> str:
            return test_dsl.dump_yaml()

        mock_service = AsyncMock()
        mock_definition = MagicMock()
        mock_definition.version = 1
        # Mock metadata attributes
        mock_definition.origin = None
        mock_definition.repo_path = None
        mock_definition.commit_sha = None
        mock_service.create_workflow_definition.return_value = mock_definition
        mock_service.session = AsyncMock()

        with patch(
            "tracecat.workflow.management.definitions.WorkflowDefinitionsService.with_session"
        ) as mock_service_ctx:
            mock_service_ctx.return_value.__aenter__.return_value = mock_service
            mock_service_ctx.return_value.__aexit__.return_value = None

            await upsert_workflow_definitions(
                sources=sources,
                fetch_yaml=mock_fetch_yaml,
                commit_sha="meta-commit-123",
                workspace_id=uuid.uuid4(),
            )

        # Verify metadata was set (if the definition supports it)
        # Note: This test assumes the hasattr checks pass
        if hasattr(mock_definition, "origin"):
            assert mock_definition.origin == "git"
        if hasattr(mock_definition, "repo_path"):
            assert mock_definition.repo_path == "workflows/meta-test.yml"
        if hasattr(mock_definition, "commit_sha"):
            assert mock_definition.commit_sha == "meta-commit-123"

    @pytest.mark.anyio
    async def test_upsert_workflow_definitions_fetch_error(self):
        """Test error handling when fetch_yaml fails."""
        sources = [
            WorkflowSource(
                path="workflows/nonexistent.yml",
                sha="abc123",
                workflow_id=WorkflowUUID.new_uuid4(),
            ),
        ]

        async def mock_fetch_yaml_error(path: str, _sha: str) -> str:
            raise FileNotFoundError(f"File not found: {path}")

        mock_service = AsyncMock()

        with patch(
            "tracecat.workflow.management.definitions.WorkflowDefinitionsService.with_session"
        ) as mock_service_ctx:
            mock_service_ctx.return_value.__aenter__.return_value = mock_service
            mock_service_ctx.return_value.__aexit__.return_value = None

            with pytest.raises(FileNotFoundError):
                await upsert_workflow_definitions(
                    sources=sources,
                    fetch_yaml=mock_fetch_yaml_error,
                    commit_sha="commit123",
                    workspace_id=uuid.uuid4(),
                )

        # Verify service was not called due to error
        mock_service.create_workflow_definition.assert_not_called()
        mock_service.session.commit.assert_not_called()

    @pytest.mark.anyio
    async def test_upsert_workflow_definitions_invalid_yaml(self):
        """Test error handling with invalid YAML."""
        sources = [
            WorkflowSource(
                path="workflows/invalid.yml",
                sha="abc123",
                workflow_id=WorkflowUUID.new_uuid4(),
            ),
        ]

        async def mock_fetch_invalid_yaml(_path: str, _sha: str) -> str:
            return "invalid: yaml: content: ["  # Invalid YAML

        mock_service = AsyncMock()

        with patch(
            "tracecat.workflow.management.definitions.WorkflowDefinitionsService.with_session"
        ) as mock_service_ctx:
            mock_service_ctx.return_value.__aenter__.return_value = mock_service
            mock_service_ctx.return_value.__aexit__.return_value = None

            with pytest.raises(Exception, match=".*"):  # YAML parsing error
                await upsert_workflow_definitions(
                    sources=sources,
                    fetch_yaml=mock_fetch_invalid_yaml,
                    commit_sha="commit123",
                    workspace_id=uuid.uuid4(),
                )

    @pytest.mark.anyio
    async def test_upsert_workflow_definitions_empty_sources(self):
        """Test upsert with empty sources list."""

        async def mock_fetch_yaml(_path: str, _sha: str) -> str:
            return ""  # Should not be called

        mock_service = AsyncMock()
        mock_service.session = AsyncMock()

        with patch(
            "tracecat.workflow.management.definitions.WorkflowDefinitionsService.with_session"
        ) as mock_service_ctx:
            mock_service_ctx.return_value.__aenter__.return_value = mock_service
            mock_service_ctx.return_value.__aexit__.return_value = None

            await upsert_workflow_definitions(
                sources=[],  # Empty sources
                fetch_yaml=mock_fetch_yaml,
                commit_sha="commit123",
                workspace_id=uuid.uuid4(),
            )

        # Should still commit (empty transaction)
        mock_service.session.commit.assert_called_once()
        mock_service.create_workflow_definition.assert_not_called()
