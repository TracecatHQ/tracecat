"""Tests for WorkflowImportService functionality."""

from datetime import timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Schedule, Tag, Workflow, WorkflowDefinition, WorkflowTag
from tracecat.dsl.common import DSLConfig, DSLEntrypoint, DSLInput
from tracecat.dsl.models import ActionStatement
from tracecat.dsl.view import RFGraph
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import ConflictStrategy
from tracecat.types.auth import Role
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.models import (
    RemoteRegistry,
    RemoteWebhook,
    RemoteWorkflowDefinition,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
)

pytestmark = pytest.mark.usefixtures("db")


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
        inputs={"test_input": "default_value"},
        config=DSLConfig(timeout=300),
    )


@pytest.fixture
def remote_workflow_definition(sample_dsl: DSLInput) -> RemoteWorkflowDefinition:
    """Create a remote workflow definition for testing."""
    return RemoteWorkflowDefinition(
        id="wf_testworkflow001",
        registry=RemoteRegistry(
            base_version="0.1.0",
            repositories=["git+ssh://git@github.com/test/registry.git#main"],
        ),
        alias="test-import-workflow",
        tags=[RemoteWorkflowTag(name="test"), RemoteWorkflowTag(name="import")],
        schedules=[
            RemoteWorkflowSchedule(
                status="online",
                cron="0 */6 * * *",
                every=21600.0,  # 6 hours
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
            conflict_strategy=ConflictStrategy.SKIP,
        )

        assert result.success is True
        assert result.workflows_found == 1
        assert result.workflows_imported == 1
        assert result.commit_sha == "abc123"
        assert len(result.diagnostics) == 0

        # Verify the workflow was created
        wf_id = WorkflowUUID.new("wf_testworkflow001")
        workflow = await session.get(Workflow, wf_id)
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

        # Verify React Flow graph was generated
        assert workflow.object is not None
        rf_graph = RFGraph.model_validate(workflow.object)
        assert rf_graph.trigger is not None
        assert len(rf_graph.nodes) >= 3  # trigger + 2 actions
        assert len(rf_graph.edges) >= 2  # trigger -> first action, first -> second

        # Verify webhook was created
        assert workflow.webhook is not None
        webhook = workflow.webhook
        assert webhook.methods == ["POST", "PUT"]
        assert webhook.status == "online"

        # Verify workflow definition was created
        from sqlmodel import select

        stmt = select(WorkflowDefinition).where(WorkflowDefinition.workflow_id == wf_id)
        result = await session.exec(stmt)
        definition = result.first()
        assert definition is not None
        assert definition.version == 1

        # Verify schedule was created
        stmt = select(Schedule).where(Schedule.workflow_id == wf_id)
        result = await session.exec(stmt)
        schedule = result.first()
        assert schedule is not None
        assert schedule.cron == "0 */6 * * *"
        assert schedule.every == timedelta(seconds=21600)
        assert schedule.timeout == 300.0
        assert schedule.status == "online"

        # Verify tags were created
        stmt = select(WorkflowTag).where(WorkflowTag.workflow_id == wf_id)
        result = await session.exec(stmt)
        workflow_tags = result.all()
        assert len(workflow_tags) == 2

        # Get the actual tag names
        from sqlmodel import col

        tag_ids = [wt.tag_id for wt in workflow_tags]
        stmt = select(Tag).where(col(Tag.id).in_(tag_ids))
        result = await session.exec(stmt)
        tags = result.all()
        tag_names = {tag.name for tag in tags}
        assert tag_names == {"test", "import"}

    @pytest.mark.anyio
    async def test_import_workflow_skip_existing(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        session: AsyncSession,
    ):
        """Test that existing workflows are skipped with SKIP strategy."""
        # First import
        result1 = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="abc123",
            conflict_strategy=ConflictStrategy.SKIP,
        )
        assert result1.success is True
        assert result1.workflows_imported == 1

        # Second import with same workflow - should be skipped
        result2 = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="def456",
            conflict_strategy=ConflictStrategy.SKIP,
        )
        assert result2.success is True
        assert result2.workflows_imported == 1  # Still reports imported

        # Verify workflow still exists and wasn't duplicated
        wf_id = WorkflowUUID.new("wf_testworkflow001")
        workflow = await session.get(Workflow, wf_id)
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
            conflict_strategy=ConflictStrategy.SKIP,
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

        # Second import with OVERWRITE strategy
        result = await import_service.import_workflows_atomic(
            remote_workflows=[modified_remote],
            commit_sha="def456",
            conflict_strategy=ConflictStrategy.OVERWRITE,
        )
        assert result.success is True

        # Verify the workflow was updated
        wf_id = WorkflowUUID.new("wf_testworkflow001")
        workflow = await session.get(Workflow, wf_id)
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

        # Verify React Flow graph was regenerated
        assert workflow.object is not None
        rf_graph = RFGraph.model_validate(workflow.object)
        assert len(rf_graph.nodes) >= 4  # trigger + 3 actions

        # Verify a new workflow definition version was created
        from sqlmodel import col, select

        stmt = (
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == wf_id)
            .order_by(col(WorkflowDefinition.version).desc())
        )
        result = await session.exec(stmt)
        definitions = result.all()
        assert len(definitions) == 2  # Original + updated
        assert definitions[0].version == 2  # Latest version

    @pytest.mark.anyio
    async def test_import_workflow_rename_existing(
        self,
        import_service: WorkflowImportService,
        remote_workflow_definition: RemoteWorkflowDefinition,
        session: AsyncSession,
    ):
        """Test that existing workflows are renamed with RENAME strategy."""
        # First import
        await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="abc123",
            conflict_strategy=ConflictStrategy.SKIP,
        )

        # Second import with RENAME strategy
        result = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition],
            commit_sha="def456",
            conflict_strategy=ConflictStrategy.RENAME,
        )
        assert result.success is True

        # Verify both workflows exist
        from sqlmodel import select

        stmt = select(Workflow).where(Workflow.owner_id == import_service.workspace_id)
        result = await session.exec(stmt)
        workflows = result.all()
        assert len(workflows) == 2

        # One should have the original title, one should have a renamed title
        titles = {wf.title for wf in workflows}
        assert "Test Import Workflow" in titles
        assert "Test Import Workflow (1)" in titles

    @pytest.mark.anyio
    async def test_create_actions_from_dsl(
        self,
        import_service: WorkflowImportService,
        sample_dsl: DSLInput,
    ):
        """Test the _create_actions_from_dsl helper method."""
        workflow_id = WorkflowUUID.new("wf_testworkflowactions")

        actions = await import_service._create_actions_from_dsl(sample_dsl, workflow_id)

        assert len(actions) == 2

        # Verify first action
        action1 = actions[0]
        assert action1.description == "Transforms test data"
        assert action1.type == "core.transform.transform"
        assert action1.workflow_id == workflow_id
        assert action1.owner_id == import_service.workspace_id

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
    async def test_import_validation_error(self):
        """Test that validation errors are properly reported."""
        # Create an invalid remote workflow - workflow ID doesn't follow pattern
        try:
            RemoteWorkflowDefinition(
                id="invalid-workflow",  # This should fail validation due to pattern mismatch
                registry=RemoteRegistry(base_version="0.1.0"),
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
            registry=RemoteRegistry(base_version="0.1.0"),
            definition=second_dsl,
        )

        # Import both workflows
        result = await import_service.import_workflows_atomic(
            remote_workflows=[remote_workflow_definition, second_remote],
            commit_sha="abc123",
            conflict_strategy=ConflictStrategy.SKIP,
        )

        assert result.success is True
        assert result.workflows_found == 2
        assert result.workflows_imported == 2

        # Verify both workflows exist
        wf1_id = WorkflowUUID.new("wf_testworkflow001")
        wf2_id = WorkflowUUID.new("wf_testworkflow002")

        workflow1 = await session.get(Workflow, wf1_id)
        workflow2 = await session.get(Workflow, wf2_id)

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
            conflict_strategy=ConflictStrategy.SKIP,
        )

        # Create second workflow with overlapping tags
        second_remote = remote_workflow_definition.model_copy()
        second_remote.id = "wf_testworkflow002"
        second_remote.definition = second_remote.definition.model_copy()
        second_remote.definition.title = "Second Workflow"
        second_remote.tags = [
            RemoteWorkflowTag(name="test"),  # Reuse existing tag
            RemoteWorkflowTag(name="second"),  # New tag
        ]

        await import_service.import_workflows_atomic(
            remote_workflows=[second_remote],
            commit_sha="def456",
            conflict_strategy=ConflictStrategy.SKIP,
        )

        # Verify tags in database
        from sqlmodel import select

        stmt = select(Tag).where(Tag.owner_id == import_service.workspace_id)
        result = await session.exec(stmt)
        tags = result.all()

        tag_names = {tag.name for tag in tags}
        assert tag_names == {"test", "import", "second"}  # 3 unique tags total

        # Verify "test" tag is shared between workflows
        test_tags = [tag for tag in tags if tag.name == "test"]
        assert len(test_tags) == 1  # Only one "test" tag should exist

        # Verify both workflows use the same "test" tag
        stmt = select(WorkflowTag).where(WorkflowTag.tag_id == test_tags[0].id)
        result = await session.exec(stmt)
        workflow_tags = result.all()
        assert len(workflow_tags) == 2  # Both workflows should use this tag
