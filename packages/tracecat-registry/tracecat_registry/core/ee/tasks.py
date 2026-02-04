"""SDK-only case task UDFs.

These UDFs are always registered but route to internal endpoints that are
gated by entitlements on the server side. If the entitlement is not enabled,
the server will return 404.
"""

from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import registry, types
from tracecat_registry.context import get_context


@registry.register(
    default_title="Create case task",
    display_group="Cases",
    description="Create a new task for a case.",
    namespace="core.cases",
    required_entitlements=["case_tasks"],
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
    if default_trigger_values and not workflow_id:
        raise ValueError(
            "workflow_id is required when default_trigger_values is provided"
        )

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


@registry.register(
    default_title="Get case task",
    display_group="Cases",
    description="Get details of a specific case task by ID.",
    namespace="core.cases",
    required_entitlements=["case_tasks"],
)
async def get_task(
    task_id: Annotated[
        str,
        Doc("The ID of the task to retrieve."),
    ],
) -> types.CaseTaskRead:
    """Get a specific case task by ID."""
    return await get_context().cases.get_task(task_id)


@registry.register(
    default_title="List case tasks",
    display_group="Cases",
    description="List all tasks for a specific case.",
    namespace="core.cases",
    required_entitlements=["case_tasks"],
)
async def list_tasks(
    case_id: Annotated[
        str,
        Doc("The ID of the case to list tasks for."),
    ],
) -> list[types.CaseTaskRead]:
    """List all tasks for a case."""
    return await get_context().cases.list_tasks(case_id)


@registry.register(
    default_title="Update case task",
    display_group="Cases",
    description="Update an existing case task.",
    namespace="core.cases",
    required_entitlements=["case_tasks"],
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
    if default_trigger_values is not None:
        update_params["default_trigger_values"] = default_trigger_values

    return await get_context().cases.update_task(task_id=task_id, **update_params)


@registry.register(
    default_title="Delete case task",
    display_group="Cases",
    description="Delete a case task.",
    namespace="core.cases",
    required_entitlements=["case_tasks"],
)
async def delete_task(
    task_id: Annotated[
        str,
        Doc("The ID of the task to delete."),
    ],
) -> None:
    """Delete a case task."""
    await get_context().cases.delete_task(task_id)
