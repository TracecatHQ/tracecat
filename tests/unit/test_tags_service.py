from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.exc import NoResultFound
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Workflow, Workspace
from tracecat.identifiers.workflow import WorkflowID
from tracecat.tags.models import TagCreate, TagUpdate
from tracecat.tags.service import TagsService
from tracecat.types.auth import Role
from tracecat.workflow.tags.service import WorkflowTagsService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def tags_service(session: AsyncSession, svc_role: Role) -> TagsService:
    """Create a tags service instance for testing."""
    return TagsService(session=session, role=svc_role)


@pytest.fixture
async def workflow_tags_service(
    session: AsyncSession, svc_role: Role
) -> WorkflowTagsService:
    """Create a workflow tags service instance for testing."""
    return WorkflowTagsService(session=session, role=svc_role)


@pytest.fixture
def tag_create_params() -> TagCreate:
    """Sample tag creation parameters."""
    return TagCreate(
        name="test-tag",
        color="#FF0000",
    )


@pytest.fixture
async def workflow_id(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[WorkflowID, None]:
    """Create a test workflow in the database and return its ID."""
    # Create a test workflow
    workflow = Workflow(
        title="test-workflow",
        owner_id=svc_workspace.id,
        description="Test workflow for tags testing",
        status="active",
        entrypoint=None,
        returns=None,
        object=None,
    )  # type: ignore
    session.add(workflow)
    await session.commit()
    try:
        yield workflow.id
    finally:
        # Clean up the workflow after tests
        await session.delete(workflow)
        await session.commit()


@pytest.mark.anyio
class TestTagsService:
    async def test_create_and_get_tag(
        self, tags_service: TagsService, tag_create_params: TagCreate
    ) -> None:
        """Test creating and retrieving a tag."""
        # Create tag
        created_tag = await tags_service.create_tag(tag_create_params)
        assert created_tag.name == tag_create_params.name
        assert created_tag.color == tag_create_params.color
        assert created_tag.owner_id == tags_service.role.workspace_id

        # Retrieve tag
        retrieved_tag = await tags_service.get_tag(created_tag.id)
        assert retrieved_tag.id == created_tag.id
        assert retrieved_tag.name == tag_create_params.name
        assert retrieved_tag.color == tag_create_params.color

    async def test_list_tags(
        self, tags_service: TagsService, tag_create_params: TagCreate
    ) -> None:
        """Test listing tags."""
        # Create multiple tags
        tag1 = await tags_service.create_tag(tag_create_params)
        tag2 = await tags_service.create_tag(
            TagCreate(name="test-tag-2", color="#00FF00")
        )

        # List all tags
        tags = await tags_service.list_tags()
        assert len(tags) >= 2
        tag_ids = {tag.id for tag in tags}
        assert tag1.id in tag_ids
        assert tag2.id in tag_ids

    async def test_update_tag(
        self, tags_service: TagsService, tag_create_params: TagCreate
    ) -> None:
        """Test updating a tag."""
        # Create initial tag
        created_tag = await tags_service.create_tag(tag_create_params)

        # Update parameters
        update_params = TagUpdate(
            name="updated-tag",
            color="#0000FF",
        )

        # Update tag
        updated_tag = await tags_service.update_tag(created_tag, update_params)
        assert updated_tag.name == update_params.name
        assert updated_tag.color == update_params.color

        # Verify updates persisted
        retrieved_tag = await tags_service.get_tag(created_tag.id)
        assert retrieved_tag.name == update_params.name
        assert retrieved_tag.color == update_params.color

    async def test_delete_tag(
        self, tags_service: TagsService, tag_create_params: TagCreate
    ) -> None:
        """Test deleting a tag."""
        # Create tag
        created_tag = await tags_service.create_tag(tag_create_params)

        # Delete tag
        await tags_service.delete_tag(created_tag)

        # Verify deletion
        with pytest.raises(NoResultFound):
            await tags_service.get_tag(created_tag.id)


@pytest.mark.anyio
class TestWorkflowTagsService:
    async def test_add_and_get_workflow_tag(
        self,
        workflow_tags_service: WorkflowTagsService,
        tags_service: TagsService,
        tag_create_params: TagCreate,
        workflow_id: WorkflowID,
    ) -> None:
        """Test adding and retrieving a workflow tag."""
        # Create a tag first
        tag = await tags_service.create_tag(tag_create_params)

        # Add tag to workflow
        wf_tag = await workflow_tags_service.add_workflow_tag(workflow_id, tag.id)
        assert wf_tag.workflow_id == workflow_id
        assert wf_tag.tag_id == tag.id

        # Retrieve workflow tag
        retrieved_wf_tag = await workflow_tags_service.get_workflow_tag(
            workflow_id, tag.id
        )
        assert retrieved_wf_tag.workflow_id == workflow_id
        assert retrieved_wf_tag.tag_id == tag.id

    async def test_list_tags_for_workflow(
        self,
        workflow_tags_service: WorkflowTagsService,
        tags_service: TagsService,
        tag_create_params: TagCreate,
        workflow_id: WorkflowID,
    ) -> None:
        """Test listing tags for a workflow."""
        # Create and add multiple tags
        tag1 = await tags_service.create_tag(tag_create_params)
        tag2 = await tags_service.create_tag(
            TagCreate(name="test-tag-2", color="#00FF00")
        )

        await workflow_tags_service.add_workflow_tag(workflow_id, tag1.id)
        await workflow_tags_service.add_workflow_tag(workflow_id, tag2.id)

        # List tags for workflow
        workflow_tags = await workflow_tags_service.list_tags_for_workflow(workflow_id)
        assert len(workflow_tags) == 2
        tag_ids = {tag.id for tag in workflow_tags}
        assert tag1.id in tag_ids
        assert tag2.id in tag_ids

    async def test_remove_workflow_tag(
        self,
        workflow_tags_service: WorkflowTagsService,
        tags_service: TagsService,
        tag_create_params: TagCreate,
        workflow_id: WorkflowID,
    ) -> None:
        """Test removing a tag from a workflow."""
        # Create a tag and add to workflow
        tag = await tags_service.create_tag(tag_create_params)
        wf_tag = await workflow_tags_service.add_workflow_tag(workflow_id, tag.id)

        # Remove tag from workflow
        await workflow_tags_service.remove_workflow_tag(wf_tag)

        # Verify tag was removed
        workflow_tags = await workflow_tags_service.list_tags_for_workflow(workflow_id)
        assert len(workflow_tags) == 0
