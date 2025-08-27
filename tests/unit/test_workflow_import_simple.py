"""Simplified tests for WorkflowImportService functionality."""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.dsl.common import DSLConfig, DSLEntrypoint, DSLInput
from tracecat.dsl.models import ActionStatement
from tracecat.types.auth import Role
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.models import (
    RemoteRegistry,
    RemoteWorkflowDefinition,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def import_service(
    session: AsyncSession, svc_role: Role
) -> WorkflowImportService:
    """Create a workflow import service instance for testing."""
    return WorkflowImportService(session=session, role=svc_role)


@pytest.fixture
def simple_dsl() -> DSLInput:
    """Create a simple DSL input for testing."""
    return DSLInput(
        title="Simple Test Workflow",
        description="A simple workflow for testing",
        entrypoint=DSLEntrypoint(ref="test_action"),
        actions=[
            ActionStatement(
                ref="test_action",
                action="core.transform.transform",
                args={"value": "test_value"},
                description="Simple transform action",
            ),
        ],
        config=DSLConfig(timeout=300),
    )


@pytest.fixture
def simple_remote_workflow(simple_dsl: DSLInput) -> RemoteWorkflowDefinition:
    """Create a simple remote workflow definition for testing."""
    return RemoteWorkflowDefinition(
        id="wf_simpleworkflow",
        registry=RemoteRegistry(base_version="0.1.0"),
        definition=simple_dsl,
    )


class TestWorkflowImportServiceSimple:
    """Simplified tests for WorkflowImportService."""

    @pytest.mark.anyio
    async def test_import_empty_list(self, import_service: WorkflowImportService):
        """Test importing an empty list of workflows."""
        result = await import_service.import_workflows_atomic(
            remote_workflows=[], commit_sha="abc123"
        )

        assert result.success is True
        assert result.workflows_found == 0
        assert result.workflows_imported == 0
        assert result.commit_sha == "abc123"
        assert len(result.diagnostics) == 0
        assert "No workflows found" in result.message

    @pytest.mark.anyio
    async def test_create_actions_from_dsl_simple(
        self, import_service: WorkflowImportService, simple_dsl: DSLInput
    ):
        """Test creating actions from DSL without database complications."""
        from tracecat.identifiers.workflow import WorkflowUUID

        workflow_id = WorkflowUUID.new("wf_testactions")
        actions = await import_service._create_actions_from_dsl(simple_dsl, workflow_id)

        assert len(actions) == 1
        action = actions[0]
        assert action.type == "core.transform.transform"
        assert action.description == "Simple transform action"
        assert action.workflow_id == workflow_id
        assert action.owner_id == import_service.workspace_id

        # Verify inputs are YAML serialized
        import yaml

        inputs = yaml.safe_load(action.inputs)
        assert inputs == {"value": "test_value"}

    @pytest.mark.anyio
    async def test_validation_basic(self):
        """Test basic validation without complex workflow creation."""
        # Test with invalid workflow ID pattern
        try:
            RemoteWorkflowDefinition(
                id="invalid-id-format",  # Should fail pattern validation
                registry=RemoteRegistry(base_version="0.1.0"),
                definition=DSLInput(
                    title="Test",
                    description="Test",
                    entrypoint=DSLEntrypoint(ref="action1"),
                    actions=[
                        ActionStatement(
                            ref="action1",
                            action="core.transform.transform",
                            args={"value": "test"},
                            description="Test action",
                        )
                    ],
                ),
            )
            raise AssertionError("Expected validation error")
        except Exception as e:
            assert "String should match pattern" in str(e)

    @pytest.mark.anyio
    async def test_import_service_initialization(
        self, import_service: WorkflowImportService
    ):
        """Test that the import service initializes correctly."""
        assert import_service is not None
        assert import_service.service_name == "workflow_import"
        assert import_service.wf_mgmt is not None
        assert import_service.session is not None
        assert import_service.role is not None
