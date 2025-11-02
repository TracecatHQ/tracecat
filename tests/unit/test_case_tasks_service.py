import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus, CaseTaskStatus
from tracecat.cases.schemas import CaseCreate, CaseTaskCreate, CaseTaskUpdate
from tracecat.cases.service import CasesService, CaseTasksService
from tracecat.db.models import Case, Workflow, Workspace
from tracecat.identifiers import WorkflowUUID
from tracecat.types.exceptions import TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def case_tasks_service(session: AsyncSession, svc_role: Role) -> CaseTasksService:
    """Create a case tasks service instance for testing."""
    return CaseTasksService(session=session, role=svc_role)


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    """Create a cases service instance for testing."""
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def test_case(cases_service: CasesService) -> Case:
    """Create a test case to attach tasks to."""
    case = await cases_service.create_case(
        CaseCreate(
            summary="Test Case for Tasks",
            description="This is a test case for task testing",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    return case


@pytest.fixture
def task_create_params() -> CaseTaskCreate:
    """Sample task creation parameters."""
    return CaseTaskCreate(
        title="Test Task",
        description="This is a test task for unit testing",
        priority=CasePriority.MEDIUM,
        status=CaseTaskStatus.TODO,
    )


@pytest.fixture
async def test_workflow(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[Workflow, None]:
    """Create a test workflow in the database for workflow_id foreign key constraint."""
    workflow = Workflow(
        title="test-workflow-for-tasks",
        owner_id=svc_workspace.id,
        description="Test workflow for case tasks testing",
        status="active",
        entrypoint=None,
        returns=None,
        object=None,
    )
    session.add(workflow)
    await session.commit()
    try:
        yield workflow
    finally:
        # Clean up the workflow after tests
        await session.delete(workflow)
        await session.commit()


@pytest.mark.anyio
class TestCaseTasksService:
    async def test_create_and_get_task(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
        task_create_params: CaseTaskCreate,
    ) -> None:
        """Test creating and retrieving a task."""
        # Create task
        created_task = await case_tasks_service.create_task(
            test_case.id, task_create_params
        )
        assert created_task.title == task_create_params.title
        assert created_task.description == task_create_params.description
        assert created_task.priority == task_create_params.priority
        assert created_task.status == task_create_params.status
        assert created_task.case_id == test_case.id
        assert created_task.owner_id == case_tasks_service.workspace_id

        # Retrieve task
        retrieved_task = await case_tasks_service.get_task(created_task.id)
        assert retrieved_task is not None
        assert retrieved_task.id == created_task.id
        assert retrieved_task.title == task_create_params.title
        assert retrieved_task.description == task_create_params.description
        assert retrieved_task.priority == task_create_params.priority
        assert retrieved_task.status == task_create_params.status

    async def test_create_task_with_workflow_id(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
        task_create_params: CaseTaskCreate,
        test_workflow: Workflow,
    ) -> None:
        """Test creating a task with a workflow_id."""
        task_create_params.workflow_id = WorkflowUUID.new(test_workflow.id)

        created_task = await case_tasks_service.create_task(
            test_case.id, task_create_params
        )
        assert created_task.workflow_id == test_workflow.id

    async def test_list_tasks(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
        task_create_params: CaseTaskCreate,
    ) -> None:
        """Test listing tasks for a case."""
        # Create multiple tasks
        task1 = await case_tasks_service.create_task(test_case.id, task_create_params)

        task2 = await case_tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(
                title="Another Test Task",
                description="Another test task description",
                priority=CasePriority.HIGH,
                status=CaseTaskStatus.IN_PROGRESS,
            ),
        )

        tasks = await case_tasks_service.list_tasks(test_case.id)
        assert len(tasks) >= 2
        task_ids = {task.id for task in tasks}
        assert task1.id in task_ids
        assert task2.id in task_ids

    async def test_list_tasks_for_different_cases(
        self,
        case_tasks_service: CaseTasksService,
        cases_service: CasesService,
        test_case: Case,
    ) -> None:
        """Test that listing tasks only returns tasks for the specified case."""
        # Create a task for the test case
        task1 = await case_tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(
                title="Task for Case 1",
                description="Task description",
                priority=CasePriority.MEDIUM,
                status=CaseTaskStatus.TODO,
            ),
        )

        # Create another case
        case2 = await cases_service.create_case(
            CaseCreate(
                summary="Second Test Case",
                description="Another test case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )

        # Create a task for the second case
        task2 = await case_tasks_service.create_task(
            case2.id,
            CaseTaskCreate(
                title="Task for Case 2",
                description="Task description",
                priority=CasePriority.MEDIUM,
                status=CaseTaskStatus.TODO,
            ),
        )

        # List tasks for the first case
        tasks_case1 = await case_tasks_service.list_tasks(test_case.id)
        task_ids_case1 = {task.id for task in tasks_case1}
        assert task1.id in task_ids_case1
        assert task2.id not in task_ids_case1

        # List tasks for the second case
        tasks_case2 = await case_tasks_service.list_tasks(case2.id)
        task_ids_case2 = {task.id for task in tasks_case2}
        assert task2.id in task_ids_case2
        assert task1.id not in task_ids_case2

    async def test_update_task(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
        task_create_params: CaseTaskCreate,
    ) -> None:
        """Test updating a task."""
        # Create initial task
        created_task = await case_tasks_service.create_task(
            test_case.id, task_create_params
        )

        # Update parameters
        update_params = CaseTaskUpdate(
            title="Updated Task Title",
            status=CaseTaskStatus.IN_PROGRESS,
            priority=CasePriority.HIGH,
        )

        # Update task
        updated_task = await case_tasks_service.update_task(
            created_task.id, update_params
        )
        assert updated_task.title == update_params.title
        assert updated_task.status == update_params.status
        assert updated_task.priority == update_params.priority
        # Fields not included in the update should remain unchanged
        assert updated_task.description == task_create_params.description

        retrieved_task = await case_tasks_service.get_task(created_task.id)
        assert retrieved_task is not None
        assert retrieved_task.title == update_params.title
        assert retrieved_task.status == update_params.status
        assert retrieved_task.priority == update_params.priority

    async def test_update_task_status(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
    ) -> None:
        """Test updating a task status through different states."""
        # Create a task
        task = await case_tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(
                title="Task Status Test",
                description="Testing status transitions",
                priority=CasePriority.MEDIUM,
                status=CaseTaskStatus.TODO,
            ),
        )

        updated_task = await case_tasks_service.update_task(
            task.id, CaseTaskUpdate(status=CaseTaskStatus.IN_PROGRESS)
        )
        assert updated_task.status == CaseTaskStatus.IN_PROGRESS

        updated_task = await case_tasks_service.update_task(
            task.id, CaseTaskUpdate(status=CaseTaskStatus.BLOCKED)
        )
        assert updated_task.status == CaseTaskStatus.BLOCKED

        updated_task = await case_tasks_service.update_task(
            task.id, CaseTaskUpdate(status=CaseTaskStatus.COMPLETED)
        )
        assert updated_task.status == CaseTaskStatus.COMPLETED

    async def test_update_task_workflow_id(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
        task_create_params: CaseTaskCreate,
        test_workflow: Workflow,
    ) -> None:
        """Test updating a task's workflow_id."""
        task = await case_tasks_service.create_task(test_case.id, task_create_params)

        # Add workflow_id
        updated_task = await case_tasks_service.update_task(
            task.id, CaseTaskUpdate(workflow_id=WorkflowUUID.new(test_workflow.id))
        )
        assert updated_task.workflow_id == test_workflow.id

    async def test_delete_task(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
        task_create_params: CaseTaskCreate,
    ) -> None:
        """Test deleting a task."""
        created_task = await case_tasks_service.create_task(
            test_case.id, task_create_params
        )
        await case_tasks_service.delete_task(created_task.id)

        with pytest.raises(TracecatNotFoundError):
            await case_tasks_service.get_task(created_task.id)

    async def test_task_not_found_errors(
        self,
        case_tasks_service: CaseTasksService,
    ) -> None:
        """Test error handling for non-existent tasks."""
        non_existent_id = uuid.uuid4()

        with pytest.raises(TracecatNotFoundError):
            await case_tasks_service.get_task(non_existent_id)

        with pytest.raises(TracecatNotFoundError):
            await case_tasks_service.update_task(
                non_existent_id, CaseTaskUpdate(title="New")
            )

        with pytest.raises(TracecatNotFoundError):
            await case_tasks_service.delete_task(non_existent_id)

    async def test_create_task_with_all_fields(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
        test_workflow: Workflow,
    ) -> None:
        """Test creating a task with all fields populated (except assignee_id due to FK constraint)."""
        task = await case_tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(
                title="Complete Task",
                description="Task with all fields",
                priority=CasePriority.CRITICAL,
                status=CaseTaskStatus.IN_PROGRESS,
                workflow_id=WorkflowUUID.new(test_workflow.id),
            ),
        )

        assert task.title == "Complete Task"
        assert task.description == "Task with all fields"
        assert task.priority == CasePriority.CRITICAL
        assert task.status == CaseTaskStatus.IN_PROGRESS
        assert task.assignee_id is None  # No assignee set
        assert task.workflow_id == test_workflow.id
        assert task.case_id == test_case.id

    async def test_cascade_delete_with_case(
        self,
        case_tasks_service: CaseTasksService,
        cases_service: CasesService,
        test_case: Case,
        task_create_params: CaseTaskCreate,
    ) -> None:
        """Test that tasks are deleted when their parent case is deleted."""
        task = await case_tasks_service.create_task(test_case.id, task_create_params)
        await cases_service.delete_case(test_case)

        with pytest.raises(TracecatNotFoundError):
            await case_tasks_service.get_task(task.id)

    async def test_create_task_minimal_fields(
        self,
        case_tasks_service: CaseTasksService,
        test_case: Case,
    ) -> None:
        """Test creating a task with only required fields."""
        task = await case_tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(title="Minimal Task"),
        )

        assert task.title == "Minimal Task"
        assert task.description is None
        assert task.priority == CasePriority.UNKNOWN
        assert task.status == CaseTaskStatus.TODO
        assert task.assignee_id is None
        assert task.workflow_id is None
