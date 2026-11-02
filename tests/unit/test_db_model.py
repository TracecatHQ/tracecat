"""Unit tests for tracecat.db.models._to_dict function.

Tests the _to_dict helper function that converts RecordModel instances to dictionaries,
ensuring it properly excludes ignored fields and unset values.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    AgentSession,
    Workflow,
    Workspace,
    WorkspaceVariable,
    _to_dict,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
class TestToDict:
    """Test the _to_dict function with various RecordModel instances."""

    async def test_to_dict_basic_fields(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict returns basic fields and excludes ignored fields."""
        # Create a WorkspaceVariable instance
        var = WorkspaceVariable(
            workspace_id=svc_workspace.id,
            name="test_var",
            description="Test variable",
            values={"key": "value"},
            environment="default",
        )
        session.add(var)
        await session.flush()

        result = _to_dict(var)

        # Should include these fields
        assert "id" in result
        assert "workspace_id" in result
        assert "name" in result
        assert "description" in result
        assert "values" in result
        assert "environment" in result
        assert "created_at" in result
        assert "updated_at" in result

        # Should exclude surrogate_id (in __pydantic_ignore_fields__)
        assert "surrogate_id" not in result

        # Verify values
        assert result["name"] == "test_var"
        assert result["description"] == "Test variable"
        assert result["values"] == {"key": "value"}
        assert result["environment"] == "default"
        assert result["workspace_id"] == svc_workspace.id

    async def test_to_dict_excludes_relationships(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict excludes relationship fields (they're not columns)."""
        # Create a workflow with a workspace relationship
        workflow = Workflow(
            workspace_id=svc_workspace.id,
            title="Test Workflow",
            description="Test description",
            status="offline",
        )
        session.add(workflow)
        await session.flush()

        result = _to_dict(workflow)

        # Should not include relationship fields like 'workspace', 'actions', etc.
        assert "workspace" not in result
        assert "actions" not in result
        assert "definitions" not in result
        assert "webhook" not in result
        assert "schedules" not in result
        assert "tags" not in result

        # Should include column fields
        assert "id" in result
        assert "workspace_id" in result
        assert "title" in result
        assert "description" in result

    async def test_to_dict_with_nullable_fields(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict handles nullable fields correctly."""
        # Create a workflow with some null fields
        workflow = Workflow(
            workspace_id=svc_workspace.id,
            title="Test Workflow",
            description="Test description",
            status="offline",
            version=None,  # Nullable field
            entrypoint=None,  # Nullable field
            alias=None,  # Nullable field
        )
        session.add(workflow)
        await session.flush()

        result = _to_dict(workflow)

        # Nullable fields that are explicitly None should be included
        assert "version" in result
        assert result["version"] is None
        assert "entrypoint" in result
        assert result["entrypoint"] is None
        assert "alias" in result
        assert result["alias"] is None


@pytest.mark.anyio
class TestAgentSessionArtifacts:
    async def test_artifacts_list_mutation_marks_session_dirty(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        agent_session = AgentSession(
            workspace_id=svc_workspace.id,
            entity_type="workflow",
            entity_id=uuid.uuid4(),
            artifacts=[],
        )
        session.add(agent_session)
        await session.flush()

        agent_session.artifacts.append({"id": "artifact-1", "name": "report.md"})

        assert session.is_modified(agent_session, include_collections=True)
