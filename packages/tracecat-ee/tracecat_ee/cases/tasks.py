from typing import Annotated, Any, cast
from uuid import UUID

from tracecat_registry import config, registry, types
from tracecat_registry.context import get_context
from typing_extensions import Doc


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
    default_trigger_values: Annotated[
        dict[str, Any] | None,
        Doc("The default trigger values for the task."),
    ] = None,
) -> types.CaseTaskRead:
    """Create a new task for a case."""

    # Validate that workflow_id is provided if default_trigger_values is set
    if default_trigger_values and not workflow_id:
        raise ValueError(
            "workflow_id is required when default_trigger_values is provided"
        )

    if config.flags.registry_client:
        return await get_context().cases.create_task(
            case_id=case_id,
            title=title,
            description=description,
            priority=priority,
            status=status,
            assignee_id=assignee_id,
            workflow_id=workflow_id,
            default_trigger_values=default_trigger_values,
        )

    from tracecat.cases.enums import CasePriority, CaseTaskStatus
    from tracecat.cases.schemas import (
        CaseTaskCreate,
    )
    from tracecat.cases.schemas import (
        CaseTaskRead as CaseTaskReadSchema,
    )
    from tracecat.cases.service import CaseTasksService

    priority_enum: CasePriority = CasePriority.UNKNOWN
    status_enum: CaseTaskStatus = CaseTaskStatus.TODO
    if priority:
        priority_enum = CasePriority(priority)
    if status:
        status_enum = CaseTaskStatus(status)

    async with CaseTasksService.with_session() as service:
        task = await service.create_task(
            case_id=UUID(case_id),
            params=CaseTaskCreate(
                title=title,
                description=description,
                priority=priority_enum,
                status=status_enum,
                assignee_id=UUID(assignee_id) if assignee_id else None,
                workflow_id=workflow_id or None,
                default_trigger_values=default_trigger_values,
            ),
        )

    return cast(
        types.CaseTaskRead,
        CaseTaskReadSchema.model_validate(task, from_attributes=True).model_dump(
            mode="json"
        ),
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
) -> types.CaseTaskRead:
    """Get a specific case task by ID."""
    if config.flags.registry_client:
        return await get_context().cases.get_task(task_id)

    from tracecat.cases.schemas import CaseTaskRead as CaseTaskReadSchema
    from tracecat.cases.service import CaseTasksService

    async with CaseTasksService.with_session() as service:
        task = await service.get_task(UUID(task_id))

    return cast(
        types.CaseTaskRead,
        CaseTaskReadSchema.model_validate(task, from_attributes=True).model_dump(
            mode="json"
        ),
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
) -> list[types.CaseTaskRead]:
    """List all tasks for a case."""
    if config.flags.registry_client:
        return await get_context().cases.list_tasks(case_id)

    from tracecat.cases.schemas import CaseTaskRead as CaseTaskReadSchema
    from tracecat.cases.service import CaseTasksService

    async with CaseTasksService.with_session() as service:
        tasks = await service.list_tasks(UUID(case_id))

    return cast(
        list[types.CaseTaskRead],
        [
            CaseTaskReadSchema.model_validate(task, from_attributes=True).model_dump(
                mode="json"
            )
            for task in tasks
        ],
    )


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
    default_trigger_values: Annotated[
        dict[str, Any] | None,
        Doc("The default trigger values for the task."),
    ] = None,
) -> types.CaseTaskRead:
    """Update an existing case task."""
    if config.flags.registry_client:
        if default_trigger_values and workflow_id is None:
            existing_task = await get_context().cases.get_task(task_id)
            effective_workflow_id = existing_task.get("workflow_id")
            if not effective_workflow_id:
                raise ValueError(
                    "workflow_id is required when default_trigger_values is provided. "
                    "Please set a workflow_id in this update or ensure the task already has one."
                )

        update_params: dict[str, Any] = {}
        if title is not None:
            update_params["title"] = title
        if description is not None:
            update_params["description"] = description
        if priority is not None:
            update_params["priority"] = priority
        if status is not None:
            update_params["status"] = status
        if assignee_id is not None:
            update_params["assignee_id"] = assignee_id
        if workflow_id is not None:
            update_params["workflow_id"] = workflow_id
        update_params["default_trigger_values"] = default_trigger_values

        return await get_context().cases.update_task(task_id=task_id, **update_params)

    from tracecat.cases.enums import CasePriority, CaseTaskStatus
    from tracecat.cases.schemas import (
        CaseTaskRead as CaseTaskReadSchema,
    )
    from tracecat.cases.schemas import (
        CaseTaskUpdate,
    )
    from tracecat.cases.service import CaseTasksService

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
    if workflow_id is not None:
        params["workflow_id"] = workflow_id
    params["default_trigger_values"] = default_trigger_values

    async with CaseTasksService.with_session() as service:
        existing_task = await service.get_task(UUID(task_id))
        effective_workflow_id = (
            workflow_id if workflow_id is not None else existing_task.workflow_id
        )

        if default_trigger_values and not effective_workflow_id:
            raise ValueError(
                "workflow_id is required when default_trigger_values is provided. "
                "Please set a workflow_id in this update or ensure the task already has one."
            )

        task = await service.update_task(UUID(task_id), CaseTaskUpdate(**params))

    return cast(
        types.CaseTaskRead,
        CaseTaskReadSchema.model_validate(task, from_attributes=True).model_dump(
            mode="json"
        ),
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
    if config.flags.registry_client:
        await get_context().cases.delete_task(task_id)
        return

    from tracecat.cases.service import CaseTasksService

    async with CaseTasksService.with_session() as service:
        await service.delete_task(UUID(task_id))
