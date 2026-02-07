"""Simplified tests for WorkflowImportService functionality."""

from unittest.mock import AsyncMock, patch

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.dsl.common import DSLConfig, DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import (
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
        """Test creating actions from DSL."""
        # Create workflow first (actions require existing workflow due to FK constraint)
        workflow_id = WorkflowUUID.new("wf_testactions")
        base_dsl = DSLInput(
            title="Base Workflow",
            description="Workflow for testing actions",
            entrypoint=DSLEntrypoint(ref="placeholder"),
            actions=[
                ActionStatement(
                    ref="placeholder",
                    action="core.transform.transform",
                    args={"value": "placeholder"},
                    description="Placeholder",
                )
            ],
            config=DSLConfig(timeout=300),
        )
        with patch(
            "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
            new=AsyncMock(
                return_value=RegistryLock(
                    origins={"tracecat_registry": "test"},
                    actions={"core.transform.transform": "tracecat_registry"},
                )
            ),
        ):
            workflow = await import_service.wf_mgmt.create_db_workflow_from_dsl(
                base_dsl, workflow_id=workflow_id, commit=False
            )
        # Remove placeholder actions
        await import_service.session.refresh(workflow, ["actions"])
        for action in workflow.actions:
            await import_service.session.delete(action)
        await import_service.session.flush()

        # Now test creating actions from DSL
        actions = await import_service.wf_mgmt.create_actions_from_dsl(
            simple_dsl, workflow_id
        )

        assert len(actions) == 1
        action = actions[0]
        assert action.type == "core.transform.transform"
        assert action.description == "Simple transform action"
        assert action.workflow_id == workflow_id
        assert action.workspace_id == import_service.workspace_id

        # Verify inputs are YAML serialized
        inputs = yaml.safe_load(action.inputs)
        assert inputs == {"value": "test_value"}

    @pytest.mark.anyio
    async def test_validation_basic(self):
        """Test basic validation without complex workflow creation."""
        # Test with invalid workflow ID pattern
        try:
            RemoteWorkflowDefinition(
                id="invalid-id-format",  # Should fail pattern validation
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
