"""Unit tests for tracecat.db.models._to_dict function.

Tests the _to_dict helper function that converts RecordModel instances to dictionaries,
ensuring it properly excludes ignored fields and unset values.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.db.models import (
    Action,
    Case,
    Secret,
    Webhook,
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

    async def test_to_dict_with_jsonb_fields(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict handles JSONB fields correctly."""
        # Create a workflow with JSONB fields
        expects_data = {"input": {"type": "string"}}
        config_data = {"timeout": 300}

        workflow = Workflow(
            workspace_id=svc_workspace.id,
            title="Test Workflow",
            description="Test description",
            status="offline",
            expects=expects_data,
            config=config_data,
        )
        session.add(workflow)
        await session.flush()

        result = _to_dict(workflow)

        # JSONB fields should be included
        assert "expects" in result
        assert result["expects"] == expects_data
        assert "config" in result
        assert result["config"] == config_data

    async def test_to_dict_with_enum_fields(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict handles enum fields correctly."""
        # Create a case with enum fields
        case = Case(
            workspace_id=svc_workspace.id,
            summary="Test Case",
            description="Test description",
            priority=CasePriority.HIGH,
            severity=CaseSeverity.CRITICAL,
            status=CaseStatus.IN_PROGRESS,
        )
        session.add(case)
        await session.flush()

        result = _to_dict(case)

        # Enum fields should be included
        assert "priority" in result
        assert result["priority"] == CasePriority.HIGH
        assert "severity" in result
        assert result["severity"] == CaseSeverity.CRITICAL
        assert "status" in result
        assert result["status"] == CaseStatus.IN_PROGRESS

    async def test_to_dict_with_binary_fields(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict handles binary fields correctly."""
        # Create a secret with encrypted_keys (LargeBinary)
        secret = Secret(
            workspace_id=svc_workspace.id,
            name="test_secret",
            type="custom",
            encrypted_keys=b"encrypted_data",
            environment="default",
        )
        session.add(secret)
        await session.flush()

        result = _to_dict(secret)

        # Binary field should be included
        assert "encrypted_keys" in result
        assert result["encrypted_keys"] == b"encrypted_data"
        assert isinstance(result["encrypted_keys"], bytes)

    async def test_to_dict_with_foreign_key_fields(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict includes foreign key column values."""
        # Create a webhook linked to a workflow
        workflow = Workflow(
            workspace_id=svc_workspace.id,
            title="Test Workflow",
            description="Test description",
            status="offline",
        )
        session.add(workflow)
        await session.flush()

        webhook = Webhook(
            workspace_id=svc_workspace.id,
            workflow_id=workflow.id,
            status="offline",
        )
        session.add(webhook)
        await session.flush()

        result = _to_dict(webhook)

        # Foreign key column should be included
        assert "workflow_id" in result
        assert result["workflow_id"] == workflow.id
        # But the relationship object should not
        assert "workflow" not in result

    async def test_to_dict_with_default_values(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict includes fields with default values."""
        # Create an Action with default values
        workflow = Workflow(
            workspace_id=svc_workspace.id,
            title="Test Workflow",
            description="Test description",
            status="offline",
        )
        session.add(workflow)
        await session.flush()

        action = Action(
            workspace_id=svc_workspace.id,
            workflow_id=workflow.id,
            type="core.http_request",
            title="Test Action",
            description="Test action description",
            # status defaults to "offline"
            # inputs defaults to ""
        )
        session.add(action)
        await session.flush()

        result = _to_dict(action)

        # Fields with defaults should be included
        assert "status" in result
        assert result["status"] == "offline"
        assert "inputs" in result
        assert result["inputs"] == ""
        assert "is_interactive" in result
        assert result["is_interactive"] is False

    async def test_to_dict_timestamps(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict includes timestamp fields from TimestampMixin."""
        var = WorkspaceVariable(
            workspace_id=svc_workspace.id,
            name="test_var",
            description="Test variable",
            values={},
            environment="default",
        )
        session.add(var)
        await session.flush()

        result = _to_dict(var)

        # Timestamp fields should be included
        assert "created_at" in result
        assert "updated_at" in result
        assert isinstance(result["created_at"], datetime)
        assert isinstance(result["updated_at"], datetime)

    async def test_to_dict_uuid_fields(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict handles UUID fields correctly."""
        var = WorkspaceVariable(
            workspace_id=svc_workspace.id,
            name="test_var",
            description="Test variable",
            values={},
            environment="default",
        )
        session.add(var)
        await session.flush()

        result = _to_dict(var)

        # UUID fields should be included
        assert "id" in result
        assert isinstance(result["id"], uuid.UUID)
        assert "workspace_id" in result
        assert isinstance(result["workspace_id"], uuid.UUID)
        assert result["workspace_id"] == svc_workspace.id

    async def test_to_dict_idempotent(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict returns the same result when called multiple times."""
        var = WorkspaceVariable(
            workspace_id=svc_workspace.id,
            name="test_var",
            description="Test variable",
            values={"key": "value"},
            environment="default",
        )
        session.add(var)
        await session.flush()

        result1 = _to_dict(var)
        result2 = _to_dict(var)

        # Both results should be equal
        assert result1 == result2
        # And should not be the same object (new dict each time)
        assert result1 is not result2

    async def test_to_dict_model_instance_method(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test RecordModel.to_dict() instance method calls _to_dict."""
        var = WorkspaceVariable(
            workspace_id=svc_workspace.id,
            name="test_var",
            description="Test variable",
            values={"key": "value"},
            environment="default",
        )
        session.add(var)
        await session.flush()

        # Test instance method
        instance_result = var.to_dict()
        # Test standalone function
        function_result = _to_dict(var)

        # Both should return the same result
        assert instance_result == function_result
        assert "surrogate_id" not in instance_result
        assert "name" in instance_result
        assert instance_result["name"] == "test_var"

    async def test_to_dict_empty_jsonb_default(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict handles empty JSONB defaults correctly."""
        workflow = Workflow(
            workspace_id=svc_workspace.id,
            title="Test Workflow",
            description="Test description",
            status="offline",
            # expects defaults to {}
            # config defaults to {}
        )
        session.add(workflow)
        await session.flush()

        result = _to_dict(workflow)

        # Empty dict defaults should be included
        assert "expects" in result
        assert result["expects"] == {}
        assert "config" in result
        assert result["config"] == {}

    async def test_to_dict_with_tags_field(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict handles JSONB tags field correctly."""
        secret = Secret(
            workspace_id=svc_workspace.id,
            name="test_secret",
            type="custom",
            encrypted_keys=b"encrypted_data",
            environment="default",
            tags={"environment": "production", "team": "security"},
        )
        session.add(secret)
        await session.flush()

        result = _to_dict(secret)

        # Tags should be included
        assert "tags" in result
        assert result["tags"] == {"environment": "production", "team": "security"}

    async def test_to_dict_with_null_tags(
        self, session: AsyncSession, svc_workspace: Workspace
    ) -> None:
        """Test _to_dict handles null tags field correctly."""
        secret = Secret(
            workspace_id=svc_workspace.id,
            name="test_secret",
            type="custom",
            encrypted_keys=b"encrypted_data",
            environment="default",
            tags=None,
        )
        session.add(secret)
        await session.flush()

        result = _to_dict(secret)

        # Null tags should be included
        assert "tags" in result
        assert result["tags"] is None
