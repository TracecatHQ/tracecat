from typing import Annotated, Any
from uuid import UUID

from tracecat_registry import registry
from typing_extensions import Doc

from tracecat.cases.enums import CasePriority, CaseTaskStatus
from tracecat.cases.schemas import CaseTaskCreate, CaseTaskRead, CaseTaskUpdate
from tracecat.cases.service import CaseTasksService
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.fields import WorkflowAlias
from tracecat.workflow.management.management import WorkflowsManagementService


@registry.register(
    default_title="Create case task",
    display_group="Cases",
    description="Create a new task for a case.",
    namespace="core.cases",
)
async def create_task(
    case_id: Annotated[
        str,
        Doc("The ID of the case to create a task for."),
    ],
    title: Annotated[
        str,
        Doc("The title of the task."),
    ],
    description: Annotated[
        str | None,
        Doc("The description of the task."),
    ] = None,
    priority: Annotated[
        str,
        Doc("The priority of the task (unknown, low, medium, high, critical)."),
    ] = "unknown",
    status: Annotated[
        str,
        Doc("The status of the task (todo, in_progress, blocked, completed)."),
    ] = "todo",
    assignee_id: Annotated[
        str | None,
        Doc("The ID of the user to assign the task to."),
    ] = None,
    workflow_id: Annotated[
        str | None,
        Doc("The ID of the workflow associated with this task."),
    ] = None,
    workflow_alias: Annotated[
        str | None,
        WorkflowAlias(),
        Doc("The alias of the workflow associated with this task."),
    ] = None,
) -> dict[str, Any]:
    """Create a new task for a case."""
    if workflow_id and workflow_alias:
        raise ValueError(
            "Cannot specify both 'workflow_id' and 'workflow_alias'. "
            "Please provide only one."
        )

    if priority:
        priority_enum = CasePriority(priority)
    if status:
        status_enum = CaseTaskStatus(status)

    resolved_workflow_id: str | None = None
    if workflow_alias:
        async with WorkflowsManagementService.with_session() as service:
            resolved = await service.resolve_workflow_alias(workflow_alias)
        if resolved is None:
            raise ValueError(f"Workflow alias '{workflow_alias}' was not found.")
        resolved_workflow_id = resolved
    elif workflow_id:
        resolved_workflow_id = workflow_id

    async with CaseTasksService.with_session() as service:
        task = await service.create_task(
            case_id=UUID(case_id),
            params=CaseTaskCreate(
                title=title,
                description=description,
                priority=priority_enum,
                status=status_enum,
                assignee_id=UUID(assignee_id) if assignee_id else None,
                workflow_id=WorkflowUUID.new(resolved_workflow_id).short()
                if resolved_workflow_id
                else None,
            ),
        )

    return CaseTaskRead.model_validate(task, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Get case task",
    display_group="Cases",
    description="Get details of a specific case task by ID.",
    namespace="core.cases",
)
async def get_task(
    task_id: Annotated[
        str,
        Doc("The ID of the task to retrieve."),
    ],
) -> dict[str, Any]:
    """Get a specific case task by ID."""
    async with CaseTasksService.with_session() as service:
        task = await service.get_task(UUID(task_id))

    return CaseTaskRead.model_validate(task, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="List case tasks",
    display_group="Cases",
    description="List all tasks for a specific case.",
    namespace="core.cases",
)
async def list_tasks(
    case_id: Annotated[
        str,
        Doc("The ID of the case to list tasks for."),
    ],
) -> list[dict[str, Any]]:
    """List all tasks for a case."""
    async with CaseTasksService.with_session() as service:
        tasks = await service.list_tasks(UUID(case_id))

    return [
        CaseTaskRead.model_validate(task, from_attributes=True).model_dump(mode="json")
        for task in tasks
    ]


@registry.register(
    default_title="Update case task",
    display_group="Cases",
    description="Update an existing case task.",
    namespace="core.cases",
)
async def update_task(
    task_id: Annotated[
        str,
        Doc("The ID of the task to update."),
    ],
    title: Annotated[
        str | None,
        Doc("The updated title of the task."),
    ] = None,
    description: Annotated[
        str | None,
        Doc("The updated description of the task."),
    ] = None,
    priority: Annotated[
        str | None,
        Doc("The updated priority of the task (unknown, low, medium, high, critical)."),
    ] = None,
    status: Annotated[
        str | None,
        Doc("The updated status of the task (todo, in_progress, blocked, completed)."),
    ] = None,
    assignee_id: Annotated[
        str | None,
        Doc("The ID of the user to assign the task to."),
    ] = None,
    workflow_id: Annotated[
        str | None,
        Doc("The ID of the workflow associated with this task."),
    ] = None,
    workflow_alias: Annotated[
        str | None,
        WorkflowAlias(),
        Doc("Alias of the workflow associated with this task."),
    ] = None,
) -> dict[str, Any]:
    """Update an existing case task."""
    if workflow_id is not None and workflow_alias is not None:
        raise ValueError(
            "Cannot specify both 'workflow_id' and 'workflow_alias'. "
            "Please provide only one."
        )

    params: dict[str, Any] = {}
    if title is not None:
        params["title"] = title
    if description is not None:
        params["description"] = description
    if priority is not None:
        params["priority"] = (
            priority if isinstance(priority, CasePriority) else CasePriority(priority)
        )
    if status is not None:
        params["status"] = (
            status if isinstance(status, CaseTaskStatus) else CaseTaskStatus(status)
        )
    if assignee_id is not None:
        params["assignee_id"] = UUID(assignee_id)
    if workflow_alias is not None:
        async with WorkflowsManagementService.with_session() as service:
            resolved = await service.resolve_workflow_alias(workflow_alias)
        if resolved is None:
            raise ValueError(f"Workflow alias '{workflow_alias}' was not found.")
        params["workflow_id"] = WorkflowUUID.new(resolved).short()
    elif workflow_id is not None:
        params["workflow_id"] = WorkflowUUID.new(workflow_id).short()

    async with CaseTasksService.with_session() as service:
        task = await service.update_task(UUID(task_id), CaseTaskUpdate(**params))

    return CaseTaskRead.model_validate(task, from_attributes=True).model_dump(
        mode="json"
    )


@registry.register(
    default_title="Delete case task",
    display_group="Cases",
    description="Delete a case task.",
    namespace="core.cases",
)
async def delete_task(
    task_id: Annotated[
        str,
        Doc("The ID of the task to delete."),
    ],
) -> None:
    """Delete a case task."""
    async with CaseTasksService.with_session() as service:
        await service.delete_task(UUID(task_id))
