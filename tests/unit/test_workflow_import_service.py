"""Tests for WorkflowImportService functionality."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Schedule, Tag, Workflow, WorkflowDefinition, WorkflowTag
from tracecat.dsl.common import DSLConfig, DSLEntrypoint, DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import (
    RemoteWebhook,
    RemoteWorkflowDefinition,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
)

pytestmark = [
    pytest.mark.usefixtures("db"),
    pytest.mark.usefixtures("registry_version_with_manifest"),
]


@pytest.fixture
async def import_service(
    session: AsyncSession, svc_role: Role
) -> WorkflowImportService:
    """Create a workflow import service instance for testing."""
    return WorkflowImportService(session=session, role=svc_role)


@pytest.fixture
def sample_dsl() -> DSLInput:
    """Create a sample DSL input for testing."""
    return DSLInput(
        title="Test Import Workflow",
        description="A workflow for testing import functionality",
        entrypoint=DSLEntrypoint(ref="test_action"),
        actions=[
            ActionStatement(
                ref="test_action",
                action="core.transform.transform",
                args={"value": "test_value", "format": "json"},
                description="Transforms test data",
            ),
            ActionStatement(
                ref="second_action",
                action="core.http_request",
                args={"url": "https://example.com", "method": "GET"},
                description="Makes an HTTP request",
                depends_on=["test_action"],
            ),
        ],
        config=DSLConfig(timeout=300),
    )


@pytest.fixture
def remote_workflow_definition(sample_dsl: DSLInput) -> RemoteWorkflowDefinition:
    """Create a remote workflow definition for testing."""
    return RemoteWorkflowDefinition(
        id="wf_testworkflow001",
        alias="test-import-workflow",
        tags=[RemoteWorkflowTag(name="test"), RemoteWorkflowTag(name="import")],
        schedules=[
            RemoteWorkflowSchedule(
                status="online",
                cron="0 */6 * * *",
                timeout=300.0,
            )
        ],
        webhook=RemoteWebhook(methods=["POST", "PUT"], status="online"),
        definition=sample_dsl,
    )


class TestWorkflowImportService:
    """Test WorkflowImportService functionality."""

    @pytest.mark.anyio
    async def test_import_workflows_atomic_empty_list(
        self, import_service: WorkflowImportService
    ):
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
    async def test_import_single_new_workflow(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        session: AsyncSession,
    ):
        """Test importing a single new workflow with all components."""
        result = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="abc123",
        )

        assert result.success is True
        assert result.workflows_found == 1
        assert result.workflows_imported == 1
        assert result.commit_sha == "abc123"
        assert len(result.diagnostics) == 0

        # Verify the workflow was created
        wf_id = WorkflowUUID.new("wf_testworkflow001")
        stmt = select(Workflow).where(Workflow.id == wf_id)
        result = await session.execute(stmt)
        workflow = result.scalars().first()
        assert workflow is not None
        assert workflow.title == "Test Import Workflow"
        assert workflow.description == "A workflow for testing import functionality"
        assert workflow.alias == "test-import-workflow"
        assert workflow.status == "offline"

        # Verify actions were created
        assert workflow.actions is not None
        assert len(workflow.actions) == 2

        # Actions don't have titles in the schema, check by type instead
        action_types = {action.type for action in workflow.actions}
        assert "core.transform.transform" in action_types
        assert "core.http_request" in action_types

        # Verify workflow graph metadata was initialized
        assert workflow.trigger_position_x == 0.0
        assert workflow.trigger_position_y == 0.0

        # Verify webhook was created
        assert workflow.webhook is not None
        webhook = workflow.webhook
        assert webhook.methods == ["POST", "PUT"]
        assert webhook.status == "online"

        # Verify workflow definition was created
        stmt = select(WorkflowDefinition).where(WorkflowDefinition.workflow_id == wf_id)
        result = await session.execute(stmt)
        definition = result.scalars().first()
        assert definition is not None
        assert definition.version == 1

        # Verify schedule was created
        stmt = select(Schedule).where(Schedule.workflow_id == wf_id)
        result = await session.execute(stmt)
        schedule = result.scalars().first()
        assert schedule is not None
        assert schedule.cron == "0 */6 * * *"
        assert schedule.every is None
        assert schedule.timeout == 300.0
        assert schedule.status == "online"

        # Verify tags were created
        stmt = select(WorkflowTag).where(WorkflowTag.workflow_id == wf_id)
        result = await session.execute(stmt)
        workflow_tags = result.scalars().all()
        assert len(workflow_tags) == 2

        # Get the actual tag names
        tag_ids = [wt.tag_id for wt in workflow_tags]
        stmt = select(Tag).where(Tag.id.in_(tag_ids))
        result = await session.execute(stmt)
        tags = result.scalars().all()
        tag_names = {tag.name for tag in tags}
        assert tag_names == {"test", "import"}

    @pytest.mark.anyio
    async def test_import_workflow_overwrite_behavior(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        session: AsyncSession,
    ):
        """Test that existing workflows are overwritten on re-import."""
        # First import
        result1 = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="abc123",
        )
        assert result1.success is True
        assert result1.workflows_imported == 1

        # Second import with same workflow - should overwrite
        result2 = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="def456",
        )
        assert result2.success is True
        assert result2.workflows_imported == 1  # Still reports imported

        # Verify workflow still exists and wasn't duplicated
        wf_id = WorkflowUUID.new("wf_testworkflow001")
        stmt = select(Workflow).where(Workflow.id == wf_id)
        result = await session.execute(stmt)
        workflow = result.scalars().first()
        assert workflow is not None

    @pytest.mark.anyio
    async def test_import_workflow_overwrite_existing(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        sample_dsl: DSLInput,
        session: AsyncSession,
    ):
        """Test that existing workflows are overwritten with OVERWRITE strategy."""
        # First import
        await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="abc123",
        )

        # Modify the remote workflow
        modified_dsl = sample_dsl.model_copy()
        modified_dsl.title = "Updated Import Workflow"
        modified_dsl.description = "Updated description"

        # Add a third action
        modified_dsl.actions.append(
            ActionStatement(
                ref="third_action",
                action="core.transform.reshape",
                args={"data": "new_data"},
                description="A third action",
                depends_on=["second_action"],
            )
        )

        modified_remote = remote_workflow_definition.model_copy()
        modified_remote.definition = modified_dsl
        modified_remote.alias = "updated-workflow"

        # Second import - should overwrite existing workflow
        result = await import_service.import_workflows_atomic(
            remote_workflows=[modified_remote],
            commit_sha="def456",
        )
        assert result.success is True

        # Verify the workflow was updated
        wf_id = WorkflowUUID.new("wf_testworkflow001")
        stmt = select(Workflow).where(Workflow.id == wf_id)
        result = await session.execute(stmt)
        workflow = result.scalars().first()
        assert workflow is not None
        assert workflow.title == "Updated Import Workflow"
        assert workflow.description == "Updated description"
        assert workflow.alias == "updated-workflow"

        # Verify actions were updated (old ones deleted, new ones created)
        assert workflow.actions is not None
        assert len(workflow.actions) == 3  # Now has 3 actions

        action_types = {action.type for action in workflow.actions}
        assert "core.transform.transform" in action_types
        assert "core.http_request" in action_types
        assert "core.transform.reshape" in action_types

        # Verify workflow graph metadata still initialized
        assert workflow.trigger_position_x == 0.0
        assert workflow.trigger_position_y == 0.0

        # Verify a new workflow definition version was created
        stmt = (
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == wf_id)
            .order_by(WorkflowDefinition.version.desc())
        )
        result = await session.execute(stmt)
        definitions = result.scalars().all()
        assert len(definitions) == 2  # Original + updated
        assert definitions[0].version == 2  # Latest version

    @pytest.mark.anyio
    async def test_import_workflow_overwrite_default_behavior(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
    ):
        """Test that re-importing workflows uses overwrite behavior by default."""
        # First import
        result1 = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="abc123",
        )
        assert result1.success is True
        assert result1.workflows_imported == 1

        # Second import with same workflow - should succeed with overwrite behavior
        result2 = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="def456",
        )

        # Should succeed as import service uses overwrite behavior by default
        assert result2.success is True
        assert result2.workflows_imported == 1
        assert len(result2.diagnostics) == 0
        assert "Successfully imported 1 workflows" in result2.message

    @pytest.mark.anyio
    async def test_create_actions_from_dsl(
        self,
        import_service: WorkflowImportService,
        sample_dsl: DSLInput,
    ):
        """Test the create_actions_from_dsl helper method."""
        # Create workflow first (actions require existing workflow due to FK constraint)
        workflow_id = WorkflowUUID.new("wf_testworkflowactions")
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
        )
        workflow = await import_service.wf_mgmt.create_db_workflow_from_dsl(
            base_dsl, workflow_id=workflow_id, commit=False
        )
        # Remove the placeholder actions created by create_db_workflow_from_dsl
        await import_service.session.refresh(workflow, ["actions"])
        for action in workflow.actions:
            await import_service.session.delete(action)
        await import_service.session.flush()

        # Now test creating actions from DSL
        actions = await import_service.wf_mgmt.create_actions_from_dsl(
            sample_dsl, workflow_id
        )

        assert len(actions) == 2

        # Verify first action
        action1 = actions[0]
        assert action1.description == "Transforms test data"
        assert action1.type == "core.transform.transform"
        assert action1.workflow_id == workflow_id
        assert action1.workspace_id == import_service.workspace_id

        # Verify inputs are YAML serialized
        import yaml

        inputs1 = yaml.safe_load(action1.inputs)
        assert inputs1 == {"value": "test_value", "format": "json"}

        # Verify control flow is properly set
        import json

        control_flow1 = (
            json.loads(action1.control_flow)
            if isinstance(action1.control_flow, str)
            else action1.control_flow
        )
        assert isinstance(control_flow1, dict)

        # Verify second action
        action2 = actions[1]
        assert action2.type == "core.http_request"
        inputs2 = yaml.safe_load(action2.inputs)
        assert inputs2 == {"url": "https://example.com", "method": "GET"}

    @pytest.mark.anyio
    async def test_schedule_handling_improvements(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        session: AsyncSession,
    ):
        """Test improved schedule data handling during workflow updates."""
        # First import with schedule
        result = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="abc123",
        )
        assert result.success is True

        # Verify original schedule exists
        wf_id = WorkflowUUID.new("wf_testworkflow001")

        stmt = select(Schedule).where(Schedule.workflow_id == wf_id)
        result = await session.execute(stmt)
        schedules = result.scalars().all()
        assert len(schedules) == 1
        original_schedule = schedules[0]
        assert original_schedule.cron == "0 */6 * * *"

        # Update workflow with different schedule
        updated_remote = remote_workflow_definition.model_copy()
        updated_remote.schedules = [
            RemoteWorkflowSchedule(
                status="offline",
                cron="0 0 * * *",  # Daily instead of every 6 hours
                timeout=600.0,  # 10 minutes
            ),
            RemoteWorkflowSchedule(
                status="online",
                cron="0 12 * * *",  # Additional noon schedule
                timeout=300.0,
            ),
        ]

        # Update with OVERWRITE strategy
        result = await import_service.import_workflows_atomic(
            remote_workflows=[updated_remote],
            commit_sha="def456",
        )
        assert result.success is True

        # Verify old schedule was replaced with new ones
        stmt = select(Schedule).where(Schedule.workflow_id == wf_id)
        result = await session.execute(stmt)
        new_schedules = result.scalars().all()
        assert len(new_schedules) == 2

        # Verify schedule details
        cron_expressions = {s.cron for s in new_schedules}
        assert cron_expressions == {"0 0 * * *", "0 12 * * *"}

        # Verify statuses
        schedule_statuses = {s.status for s in new_schedules}
        assert "online" in schedule_statuses

    @pytest.mark.anyio
    async def test_schedule_handling_empty_schedules(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        session: AsyncSession,
    ):
        """Test schedule handling when workflow has no schedules."""
        # Remove schedules from remote workflow
        no_schedule_remote = remote_workflow_definition.model_copy()
        no_schedule_remote.schedules = None

        result = await import_service.import_workflows_atomic(
            remote_workflows=[no_schedule_remote],
            commit_sha="abc123",
        )
        assert result.success is True

        # Verify no schedules were created
        wf_id = WorkflowUUID.new("wf_testworkflow001")

        stmt = select(Schedule).where(Schedule.workflow_id == wf_id)
        result = await session.execute(stmt)
        schedules = result.scalars().all()
        assert len(schedules) == 0

    @pytest.mark.anyio
    async def test_import_validation_error(self):
        """Test that validation errors are properly reported."""
        # Create an invalid remote workflow - workflow ID doesn't follow pattern
        try:
            RemoteWorkflowDefinition(
                id="invalid-workflow",  # This should fail validation due to pattern mismatch
                definition=DSLInput(
                    title="Valid Title",
                    description="Valid description",
                    entrypoint=DSLEntrypoint(ref="test_action"),
                    actions=[
                        ActionStatement(
                            ref="test_action",
                            action="core.transform.transform",
                            args={"value": "test"},
                            description="Test action",
                        )
                    ],
                ),
            )
            # If we get here, the validation didn't work as expected
            raise AssertionError("Expected validation error for invalid workflow ID")
        except Exception as e:
            # This is expected - the ID pattern validation should fail
            assert "String should match pattern" in str(e)

    @pytest.mark.anyio
    async def test_import_transaction_rollback_on_failure(
        self, import_service: WorkflowImportService, session: AsyncSession
    ):
        """Test that failed imports rollback all changes atomically."""
        # Create one valid workflow and one with cross-workflow validation error
        valid_dsl = DSLInput(
            title="Valid Workflow",
            description="This should not be imported",
            entrypoint=DSLEntrypoint(ref="valid_action"),
            actions=[
                ActionStatement(
                    ref="valid_action",
                    action="core.transform.transform",
                    args={"value": "valid_data"},
                    description="Valid action",
                )
            ],
        )

        invalid_dsl = DSLInput(
            title="Invalid Workflow",
            description="References missing workflow",
            entrypoint=DSLEntrypoint(ref="invalid_action"),
            actions=[
                ActionStatement(
                    ref="invalid_action",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_alias": "missing-workflow"},
                    description="Invalid action",
                )
            ],
        )

        valid_remote = RemoteWorkflowDefinition(
            id="wf_valid001",
            alias="valid-workflow",
            definition=valid_dsl,
        )

        invalid_remote = RemoteWorkflowDefinition(
            id="wf_invalid001",
            alias="invalid-workflow",
            definition=invalid_dsl,
        )

        # Import should fail due to validation error
        result = await import_service.import_workflows_atomic(
            remote_workflows=[valid_remote, invalid_remote],
            commit_sha="abc123",
        )

        assert result.success is False
        assert result.workflows_imported == 0
        assert len(result.diagnostics) == 1

        # Verify NO workflows were imported (atomic rollback)
        stmt = select(Workflow).where(
            Workflow.workspace_id == import_service.workspace_id
        )
        result = await session.execute(stmt)
        workflows = result.scalars().all()
        assert len(workflows) == 0  # Nothing should be imported

    @pytest.mark.anyio
    async def test_import_multiple_workflows_atomic(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        session: AsyncSession,
    ):
        """Test that multiple workflows are imported atomically."""
        # Create a second remote workflow
        second_dsl = DSLInput(
            title="Second Workflow",
            description="Another test workflow",
            entrypoint=DSLEntrypoint(ref="action1"),
            actions=[
                ActionStatement(
                    ref="action1",
                    action="core.transform.transform",
                    args={"value": "second_value"},
                    description="Second action",
                )
            ],
        )

        second_remote = RemoteWorkflowDefinition(
            id="wf_testworkflow002",
            definition=second_dsl,
        )

        # Import both workflows
        result = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition, second_remote],
            commit_sha="abc123",
        )

        assert result.success is True
        assert result.workflows_found == 2
        assert result.workflows_imported == 2

        # Verify both workflows exist
        wf1_id = WorkflowUUID.new("wf_testworkflow001")
        wf2_id = WorkflowUUID.new("wf_testworkflow002")

        stmt1 = select(Workflow).where(Workflow.id == wf1_id)
        result1 = await session.execute(stmt1)
        workflow1 = result1.scalars().first()
        stmt2 = select(Workflow).where(Workflow.id == wf2_id)
        result2 = await session.execute(stmt2)
        workflow2 = result2.scalars().first()

        assert workflow1 is not None
        assert workflow2 is not None
        assert workflow1.title == "Test Import Workflow"
        assert workflow2.title == "Second Workflow"

    @pytest.mark.anyio
    async def test_tag_creation_and_reuse(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        session: AsyncSession,
    ):
        """Test that tags are created once and reused across workflows."""
        # Import first workflow with tags
        await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="abc123",
        )

        # Create second workflow with overlapping tags
        second_remote = remote_workflow_definition.model_copy()
        second_remote.id = "wf_testworkflow002"
        second_remote.alias = (
            "second-workflow"  # Different alias to avoid unique constraint violation
        )
        second_remote.definition = second_remote.definition.model_copy()
        second_remote.definition.title = "Second Workflow"
        second_remote.tags = [
            RemoteWorkflowTag(name="test"),  # Reuse existing tag
            RemoteWorkflowTag(name="second"),  # New tag
        ]

        await import_service.import_workflows_atomic(
            remote_workflows=[second_remote],
            commit_sha="def456",
        )

        # Verify tags in database
        stmt = select(Tag).where(Tag.workspace_id == import_service.workspace_id)
        result = await session.execute(stmt)
        tags = result.scalars().all()

        tag_names = {tag.name for tag in tags}
        assert tag_names == {"test", "import", "second"}  # 3 unique tags total

        # Verify "test" tag is shared between workflows
        test_tags = [tag for tag in tags if tag.name == "test"]
        assert len(test_tags) == 1  # Only one "test" tag should exist

        # Verify both workflows use the same "test" tag
        stmt = select(WorkflowTag).where(WorkflowTag.tag_id == test_tags[0].id)
        result = await session.execute(stmt)
        workflow_tags = result.scalars().all()
        assert len(workflow_tags) == 2  # Both workflows should use this tag

    @pytest.mark.anyio
    async def test_cross_workflow_integrity_valid_alias_in_batch(
        self, import_service: WorkflowImportService
    ):
        """Test cross-workflow validation with valid alias reference within import batch."""
        # Create parent workflow that references child workflow by alias
        parent_dsl = DSLInput(
            title="Parent Workflow",
            description="Calls child workflow",
            entrypoint=DSLEntrypoint(ref="call_child"),
            actions=[
                ActionStatement(
                    ref="call_child",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_alias": "child-workflow"},
                    description="Call child workflow",
                )
            ],
        )

        # Create child workflow with matching alias
        child_dsl = DSLInput(
            title="Child Workflow",
            description="Child workflow to be called",
            entrypoint=DSLEntrypoint(ref="child_action"),
            actions=[
                ActionStatement(
                    ref="child_action",
                    action="core.transform.transform",
                    args={"value": "child_data"},
                    description="Child action",
                )
            ],
        )

        parent_remote = RemoteWorkflowDefinition(
            id="wf_parent001",
            alias="parent-workflow",
            definition=parent_dsl,
        )

        child_remote = RemoteWorkflowDefinition(
            id="wf_child001",
            alias="child-workflow",  # This alias matches the reference
            definition=child_dsl,
        )

        # Import both workflows - should succeed due to alias resolution within batch
        result = await import_service.import_workflows_atomic(
            remote_workflows=[parent_remote, child_remote],
            commit_sha="abc123",
        )

        assert result.success is True
        assert result.workflows_imported == 2
        assert len(result.diagnostics) == 0

    @pytest.mark.anyio
    async def test_cross_workflow_integrity_invalid_alias(
        self, import_service: WorkflowImportService
    ):
        """Test cross-workflow validation with invalid alias reference."""
        # Create workflow that references non-existent alias
        parent_dsl = DSLInput(
            title="Parent Workflow",
            description="Calls non-existent child",
            entrypoint=DSLEntrypoint(ref="call_missing"),
            actions=[
                ActionStatement(
                    ref="call_missing",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_alias": "non-existent-workflow"},
                    description="Call missing workflow",
                )
            ],
        )

        parent_remote = RemoteWorkflowDefinition(
            id="wf_parent001",
            alias="parent-workflow",
            definition=parent_dsl,
        )

        # Import should fail due to invalid alias reference
        result = await import_service.import_workflows_atomic(
            remote_workflows=[parent_remote],
            commit_sha="abc123",
        )

        assert result.success is False
        assert result.workflows_imported == 0
        assert len(result.diagnostics) == 1

        diagnostic = result.diagnostics[0]
        assert diagnostic.error_type == "validation"
        assert "non-existent-workflow" in diagnostic.message
        assert "Unknown workflow alias" in diagnostic.message
        assert diagnostic.details["workflow_alias"] == "non-existent-workflow"

    @pytest.mark.anyio
    async def test_cross_workflow_integrity_valid_existing_alias(
        self, import_service: WorkflowImportService
    ):
        """Test cross-workflow validation with valid alias from existing workflow."""
        # First, create an existing workflow in the database
        existing_dsl = DSLInput(
            title="Existing Workflow",
            description="Pre-existing workflow",
            entrypoint=DSLEntrypoint(ref="existing_action"),
            actions=[
                ActionStatement(
                    ref="existing_action",
                    action="core.transform.transform",
                    args={"value": "existing_data"},
                    description="Existing action",
                )
            ],
        )

        existing_remote = RemoteWorkflowDefinition(
            id="wf_existing001",
            alias="existing-workflow",
            definition=existing_dsl,
        )

        # Import the existing workflow first
        await import_service.import_workflows_atomic(
            remote_workflows=[existing_remote],
            commit_sha="setup123",
        )

        # Now create a new workflow that references the existing one
        new_dsl = DSLInput(
            title="New Workflow",
            description="References existing workflow",
            entrypoint=DSLEntrypoint(ref="call_existing"),
            actions=[
                ActionStatement(
                    ref="call_existing",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_alias": "existing-workflow"},
                    description="Call existing workflow",
                )
            ],
        )

        new_remote = RemoteWorkflowDefinition(
            id="wf_new001",
            alias="new-workflow",
            definition=new_dsl,
        )

        # Import new workflow - should succeed due to existing alias
        result = await import_service.import_workflows_atomic(
            remote_workflows=[new_remote],
            commit_sha="abc123",
        )

        assert result.success is True
        assert result.workflows_imported == 1
        assert len(result.diagnostics) == 0

    @pytest.mark.anyio
    async def test_cross_workflow_integrity_workflow_id_skipped(
        self, import_service: WorkflowImportService
    ):
        """Test that workflows using workflow_id instead of alias are not validated."""
        # Create workflow that uses workflow_id (should skip validation)
        parent_dsl = DSLInput(
            title="Parent Workflow",
            description="Uses workflow_id reference",
            entrypoint=DSLEntrypoint(ref="call_by_id"),
            actions=[
                ActionStatement(
                    ref="call_by_id",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_id": "wf_someworkflow123"},  # Uses ID, not alias
                    description="Call by workflow ID",
                )
            ],
        )

        parent_remote = RemoteWorkflowDefinition(
            id="wf_parent001",
            alias="parent-workflow",
            definition=parent_dsl,
        )

        # Import should succeed because we skip validation for workflow_id
        result = await import_service.import_workflows_atomic(
            remote_workflows=[parent_remote],
            commit_sha="abc123",
        )

        assert result.success is True
        assert result.workflows_imported == 1
        assert len(result.diagnostics) == 0

    @pytest.mark.anyio
    async def test_cross_workflow_integrity_multiple_references(
        self, import_service: WorkflowImportService
    ):
        """Test validation with multiple child workflow references."""
        # Create workflow with multiple child workflow calls
        parent_dsl = DSLInput(
            title="Multi-Child Workflow",
            description="Calls multiple child workflows",
            entrypoint=DSLEntrypoint(ref="call_first"),
            actions=[
                ActionStatement(
                    ref="call_first",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_alias": "child-one"},
                    description="Call first child",
                ),
                ActionStatement(
                    ref="call_second",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_alias": "child-two"},
                    description="Call second child",
                    depends_on=["call_first"],
                ),
                ActionStatement(
                    ref="call_missing",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={"workflow_alias": "missing-child"},  # This will fail
                    description="Call missing child",
                    depends_on=["call_second"],
                ),
            ],
        )

        # Create only two child workflows (missing the third)
        child_one_dsl = DSLInput(
            title="Child One",
            description="First child",
            entrypoint=DSLEntrypoint(ref="action1"),
            actions=[
                ActionStatement(
                    ref="action1",
                    action="core.transform.transform",
                    args={"value": "child1_data"},
                    description="Child 1 action",
                )
            ],
        )

        child_two_dsl = DSLInput(
            title="Child Two",
            description="Second child",
            entrypoint=DSLEntrypoint(ref="action2"),
            actions=[
                ActionStatement(
                    ref="action2",
                    action="core.transform.transform",
                    args={"value": "child2_data"},
                    description="Child 2 action",
                )
            ],
        )

        parent_remote = RemoteWorkflowDefinition(
            id="wf_parent001",
            alias="parent-workflow",
            definition=parent_dsl,
        )

        child_one_remote = RemoteWorkflowDefinition(
            id="wf_child001",
            alias="child-one",
            definition=child_one_dsl,
        )

        child_two_remote = RemoteWorkflowDefinition(
            id="wf_child002",
            alias="child-two",
            definition=child_two_dsl,
        )

        # Import should fail due to missing third child
        result = await import_service.import_workflows_atomic(
            remote_workflows=[parent_remote, child_one_remote, child_two_remote],
            commit_sha="abc123",
        )

        assert result.success is False
        assert result.workflows_imported == 0
        assert len(result.diagnostics) == 1

        diagnostic = result.diagnostics[0]
        assert diagnostic.error_type == "validation"
        assert "missing-child" in diagnostic.message
        assert diagnostic.details["workflow_alias"] == "missing-child"
        assert diagnostic.details["action_ref"] == "call_missing"

    @pytest.mark.anyio
    async def test_cross_workflow_integrity_validation_exception_handling(
        self, import_service: WorkflowImportService
    ):
        """Test that exceptions during cross-workflow validation are properly handled."""
        # Create a workflow with malformed args (not a dict)
        parent_dsl = DSLInput(
            title="Malformed Workflow",
            description="Has malformed action args",
            entrypoint=DSLEntrypoint(ref="malformed_action"),
            actions=[
                ActionStatement(
                    ref="malformed_action",
                    action=PlatformAction.CHILD_WORKFLOW_EXECUTE,
                    args={},  # Empty dict to test edge case
                    description="Malformed action",
                )
            ],
        )

        parent_remote = RemoteWorkflowDefinition(
            id="wf_malformed001",
            alias="malformed-workflow",
            definition=parent_dsl,
        )

        # Import should succeed despite malformed args (validation skips non-dict args)
        result = await import_service.import_workflows_atomic(
            remote_workflows=[parent_remote],
            commit_sha="abc123",
        )

        # Should succeed because validation gracefully handles non-dict args
        assert result.success is True
        assert result.workflows_imported == 1
        assert len(result.diagnostics) == 0
