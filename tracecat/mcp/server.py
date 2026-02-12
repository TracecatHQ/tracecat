"""Standalone MCP server for Tracecat workflow management.

Exposes workflow operations to external MCP clients (Claude Desktop, Cursor, etc.).
Users authenticate via their existing Tracecat OIDC login.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from typing import Any

import yaml
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.mcp.auth import (
    create_mcp_auth,
    get_email_from_token,
    list_user_organizations,
    list_user_workspaces,
    resolve_role,
)

auth = create_mcp_auth()

mcp = FastMCP(
    "tracecat-workflows",
    auth=auth,
    instructions=(
        "Tracecat workflow management server. "
        "Use list_workspaces to discover available workspaces, then pass "
        "workspace_id to workflow tools. All workflow operations require a workspace_id."
    ),
)


def _json(obj: Any) -> str:
    """Serialize to JSON string."""
    return json.dumps(obj, default=str)


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workspaces() -> str:
    """List all workspaces accessible to the authenticated user.

    Returns a JSON array of workspace objects with id, name, and role.
    """
    try:
        email = get_email_from_token()
        workspaces = await list_user_workspaces(email)
        return _json(workspaces)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workspaces", error=str(e))
        raise ToolError(f"Failed to list workspaces: {e}") from None


@mcp.tool()
async def list_organizations() -> str:
    """List all organizations the authenticated user belongs to.

    Returns a JSON array of organization objects with id, name, and role.
    """
    try:
        email = get_email_from_token()
        orgs = await list_user_organizations(email)
        return _json(orgs)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list organizations", error=str(e))
        raise ToolError(f"Failed to list organizations: {e}") from None


# ---------------------------------------------------------------------------
# Workflow CRUD tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_workflow(
    workspace_id: str,
    title: str,
    description: str = "",
    definition_yaml: str | None = None,
) -> str:
    """Create a new workflow in a workspace.

    If definition_yaml is provided, creates a fully-defined workflow from YAML.
    Otherwise creates a blank workflow with just a title and description.

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        title: Workflow title (3-100 characters).
        description: Optional workflow description (up to 1000 characters).
        definition_yaml: Optional YAML string defining the full workflow (actions,
            triggers, entrypoint). When provided, title/description in the YAML
            take precedence. The YAML must follow the ExternalWorkflowDefinition
            format with a top-level 'definition' key containing title, description,
            entrypoint, actions, and optionally triggers.

    Returns JSON with the new workflow's id, title, description, and status.
    """
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.management.schemas import WorkflowCreate

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)

        if definition_yaml:
            # Parse YAML and create workflow from external definition
            try:
                external_defn_data = yaml.safe_load(definition_yaml)
            except yaml.YAMLError as e:
                raise ToolError(f"Invalid YAML: {e}") from e

            # If YAML has no top-level 'definition' key, wrap it
            if "definition" not in external_defn_data:
                external_defn_data = {"definition": external_defn_data}

            # Apply title/description overrides if not in the YAML
            defn = external_defn_data.get("definition", {})
            if "title" not in defn:
                defn["title"] = title
            if "description" not in defn and description:
                defn["description"] = description

            async with WorkflowsManagementService.with_session(role=role) as svc:
                workflow = await svc.create_workflow_from_external_definition(
                    external_defn_data
                )
                return _json(
                    {
                        "id": str(workflow.id),
                        "title": workflow.title,
                        "description": workflow.description,
                        "status": workflow.status,
                    }
                )
        else:
            async with WorkflowsManagementService.with_session(role=role) as svc:
                workflow = await svc.create_workflow(
                    WorkflowCreate(title=title, description=description or None)
                )
                return _json(
                    {
                        "id": str(workflow.id),
                        "title": workflow.title,
                        "description": workflow.description,
                        "status": workflow.status,
                    }
                )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create workflow", error=str(e))
        raise ToolError(f"Failed to create workflow: {e}") from None


@mcp.tool()
async def get_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Get details of a specific workflow including its full YAML definition.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID (short or full format).

    Returns JSON with workflow metadata (id, title, description, status, version)
    and a 'definition_yaml' field containing the full workflow definition in YAML
    format (actions, triggers, entrypoint, etc.). The YAML can be modified and
    used with create_workflow's definition_yaml parameter to create a new workflow.
    """
    from tracecat.workflow.management.management import WorkflowsManagementService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            # Build the DSL from current workflow state
            definition_yaml = ""
            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
                definition_yaml = yaml.dump(
                    {"definition": dsl.model_dump(mode="json", exclude_none=True)},
                    indent=2,
                    sort_keys=False,
                )
            except (ValidationError, Exception) as e:
                logger.warning(
                    "Could not build DSL for workflow",
                    workflow_id=workflow_id,
                    error=str(e),
                )

            return _json(
                {
                    "id": str(workflow.id),
                    "title": workflow.title,
                    "description": workflow.description,
                    "status": workflow.status,
                    "version": workflow.version,
                    "alias": workflow.alias,
                    "entrypoint": workflow.entrypoint,
                    "definition_yaml": definition_yaml,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get workflow", error=str(e))
        raise ToolError(f"Failed to get workflow: {e}") from None


@mcp.tool()
async def update_workflow(
    workspace_id: str,
    workflow_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    alias: str | None = None,
    error_handler: str | None = None,
) -> str:
    """Update a workflow's properties.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        title: New title (3-100 characters, optional).
        description: New description (optional).
        status: New status - "online" or "offline" (optional).
        alias: New alias for the workflow (optional).
        error_handler: Error handler workflow alias (optional).

    Returns a confirmation message.
    """
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.management.schemas import WorkflowUpdate

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        update_params = WorkflowUpdate(
            title=title,
            description=description,
            status=status,  # pyright: ignore[reportArgumentType]
            alias=alias,
            error_handler=error_handler,
        )

        async with WorkflowsManagementService.with_session(role=role) as svc:
            await svc.update_workflow(wf_id, update_params)
            return _json({"message": f"Workflow {workflow_id} updated successfully"})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update workflow", error=str(e))
        raise ToolError(f"Failed to update workflow: {e}") from None


# ---------------------------------------------------------------------------
# Validation and publishing tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def validate_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Validate a workflow's draft state.

    Checks that the workflow DSL is structurally sound and that arguments are valid.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with valid (bool) and any errors.
    """
    from tracecat.exceptions import TracecatValidationError
    from tracecat.validation.service import validate_dsl
    from tracecat.workflow.management.management import WorkflowsManagementService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            # Tier 1: Build DSL
            errors: list[dict[str, Any]] = []
            dsl: DSLInput | None = None
            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
            except TracecatValidationError as e:
                errors.append({"type": "dsl", "message": str(e)})
            except ValidationError as e:
                errors.append({"type": "dsl", "message": str(e)})

            if errors or dsl is None:
                return _json({"valid": False, "errors": errors})

            # Tier 2: Semantic validation
            val_results = await validate_dsl(session=svc.session, dsl=dsl, role=role)
            if val_results:
                for vr in val_results:
                    errors.append(
                        {
                            "type": str(vr.root.type),
                            "message": vr.root.msg,
                        }
                    )
                return _json({"valid": False, "errors": errors})

            return _json({"valid": True, "errors": []})
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to validate workflow", error=str(e))
        raise ToolError(f"Failed to validate workflow: {e}") from None


@mcp.tool()
async def publish_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Publish (commit) a workflow, creating a new versioned definition.

    This validates the workflow, freezes registry dependencies, and creates a
    new workflow definition version.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with workflow_id, status, message, version, and any errors.
    """
    from tracecat.exceptions import TracecatValidationError
    from tracecat.registry.lock.service import RegistryLockService
    from tracecat.validation.service import validate_dsl
    from tracecat.workflow.management.definitions import WorkflowDefinitionsService
    from tracecat.workflow.management.management import WorkflowsManagementService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            session = svc.session
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            # Tier 1: Build DSL
            construction_errors: list[dict[str, Any]] = []
            dsl: DSLInput | None = None
            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
            except TracecatValidationError as e:
                construction_errors.append(
                    {
                        "type": "dsl",
                        "status": "error",
                        "message": str(e),
                    }
                )
            except ValidationError as e:
                construction_errors.append(
                    {
                        "type": "dsl",
                        "status": "error",
                        "message": str(e),
                    }
                )

            if construction_errors:
                return _json(
                    {
                        "workflow_id": workflow_id,
                        "status": "failure",
                        "message": f"DSL construction failed with {len(construction_errors)} errors",
                        "errors": construction_errors,
                    }
                )

            if dsl is None:
                raise ToolError("DSL should be defined if no construction errors")

            # Tier 2: Semantic validation
            val_errors = await validate_dsl(session=session, dsl=dsl, role=role)
            if val_errors:
                return _json(
                    {
                        "workflow_id": workflow_id,
                        "status": "failure",
                        "message": f"{len(val_errors)} validation error(s)",
                        "errors": [
                            {
                                "type": str(vr.root.type),
                                "message": vr.root.msg,
                            }
                            for vr in val_errors
                        ],
                    }
                )

            # Phase 1: Resolve registry lock
            lock_service = RegistryLockService(session, role)
            action_names = {action.action for action in dsl.actions}
            registry_lock = await lock_service.resolve_lock_with_bindings(action_names)
            workflow.registry_lock = registry_lock.model_dump()

            # Phase 2: Create workflow definition
            defn_service = WorkflowDefinitionsService(session, role=role)
            defn = await defn_service.create_workflow_definition(
                wf_id,
                dsl,
                alias=workflow.alias,
                registry_lock=registry_lock,
                commit=False,
            )

            # Phase 3: Update workflow version
            workflow.version = defn.version
            session.add(workflow)
            session.add(defn)
            await session.commit()
            await session.refresh(workflow)
            await session.refresh(defn)

            return _json(
                {
                    "workflow_id": workflow_id,
                    "status": "success",
                    "message": "Workflow published successfully",
                    "version": defn.version,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to publish workflow", error=str(e))
        raise ToolError(f"Failed to publish workflow: {e}") from None


# ---------------------------------------------------------------------------
# Execution tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def run_draft_workflow(
    workspace_id: str,
    workflow_id: str,
    inputs: str | None = None,
    title: str | None = None,
    description: str | None = None,
) -> str:
    """Run a workflow from its current draft state (without publishing).

    Optionally update the workflow's title/description before running.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        inputs: Optional JSON string of trigger inputs.
        title: Optional new title to set before running.
        description: Optional new description to set before running.

    Returns JSON with workflow_id, execution_id, and a message.
    """
    from tracecat.exceptions import TracecatValidationError
    from tracecat.workflow.executions.service import WorkflowExecutionsService
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.management.schemas import WorkflowUpdate

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        # Optionally update workflow first
        if title or description:
            async with WorkflowsManagementService.with_session(role=role) as svc:
                await svc.update_workflow(
                    wf_id,
                    WorkflowUpdate(title=title, description=description),
                )

        # Build DSL from draft
        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")
            try:
                dsl_input = await svc.build_dsl_from_workflow(workflow)
            except (TracecatValidationError, ValidationError) as e:
                raise ToolError(f"Draft workflow has validation errors: {e}") from e

        # Dispatch execution
        payload = json.loads(inputs) if inputs else None
        exec_service = await WorkflowExecutionsService.connect(role=role)
        response = exec_service.create_draft_workflow_execution_nowait(
            dsl=dsl_input,
            wf_id=wf_id,
            payload=payload,
        )
        return _json(
            {
                "workflow_id": str(response["wf_id"]),
                "execution_id": str(response["wf_exec_id"]),
                "message": response["message"],
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to run draft workflow", error=str(e))
        raise ToolError(f"Failed to run draft workflow: {e}") from None


@mcp.tool()
async def run_published_workflow(
    workspace_id: str,
    workflow_id: str,
    inputs: str | None = None,
) -> str:
    """Run the latest published version of a workflow.

    The workflow must have been published (committed) at least once.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        inputs: Optional JSON string of trigger inputs.

    Returns JSON with workflow_id, execution_id, and a message.
    """
    from sqlalchemy import select

    from tracecat.db.engine import get_async_session_context_manager
    from tracecat.db.models import WorkflowDefinition
    from tracecat.registry.lock.types import RegistryLock
    from tracecat.workflow.executions.service import WorkflowExecutionsService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        # Fetch latest workflow definition scoped to the caller's workspace
        async with get_async_session_context_manager() as session:
            result = await session.execute(
                select(WorkflowDefinition)
                .where(
                    WorkflowDefinition.workflow_id == wf_id,
                    WorkflowDefinition.workspace_id == ws_id,
                )
                .order_by(WorkflowDefinition.version.desc())
            )
            defn = result.scalars().first()
            if not defn:
                raise ToolError(
                    f"No published definition found for workflow {workflow_id}. "
                    "Publish the workflow first using publish_workflow."
                )

            dsl_input = DSLInput(**defn.content)
            registry_lock = (
                RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None
            )

        # Dispatch execution
        payload = json.loads(inputs) if inputs else None
        exec_service = await WorkflowExecutionsService.connect(role=role)
        response = exec_service.create_workflow_execution_nowait(
            dsl=dsl_input,
            wf_id=wf_id,
            payload=payload,
            registry_lock=registry_lock,
        )
        return _json(
            {
                "workflow_id": str(response["wf_id"]),
                "execution_id": str(response["wf_exec_id"]),
                "message": response["message"],
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to run published workflow", error=str(e))
        raise ToolError(f"Failed to run published workflow: {e}") from None


# ---------------------------------------------------------------------------
# Schedule tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workflow_schedules(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """List all schedules for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns a JSON array of schedule objects.
    """
    from tracecat.workflow.schedules.schemas import ScheduleRead
    from tracecat.workflow.schedules.service import WorkflowSchedulesService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowSchedulesService.with_session(role=role) as svc:
            schedules = await svc.list_schedules(workflow_id=wf_id)
            schedule_reads = ScheduleRead.list_adapter().validate_python(schedules)
            return _json([s.model_dump(mode="json") for s in schedule_reads])
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list schedules", error=str(e))
        raise ToolError(f"Failed to list schedules: {e}") from None


@mcp.tool()
async def create_schedule(
    workspace_id: str,
    workflow_id: str,
    cron: str | None = None,
    every: str | None = None,
    offset: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    inputs: str | None = None,
    schedule_status: str = "online",
) -> str:
    """Create a schedule for a workflow.

    The workflow must be published (committed) before creating a schedule.
    Provide either 'cron' or 'every' (ISO 8601 duration like "PT1H" for hourly).

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        cron: Cron expression (e.g. "0 9 * * 1-5" for weekdays at 9am).
        every: ISO 8601 duration (e.g. "PT1H" for every hour, "P1D" for daily).
        offset: ISO 8601 duration offset for the schedule.
        start_at: ISO 8601 datetime for when to start the schedule.
        end_at: ISO 8601 datetime for when to end the schedule.
        inputs: Optional JSON string of workflow trigger inputs.
        schedule_status: "online" or "offline" (default "online").

    Returns JSON with the created schedule details.
    """

    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.schedules.schemas import ScheduleCreate, ScheduleRead
    from tracecat.workflow.schedules.service import WorkflowSchedulesService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        # Verify workflow exists and is published
        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")
            if not workflow.version:
                raise ToolError(
                    "Workflow must be published before creating a schedule. "
                    "Use publish_workflow first."
                )

        # Parse inputs
        parsed_inputs = json.loads(inputs) if inputs else None
        parsed_every = _parse_iso8601_duration(every) if every else None
        parsed_offset = _parse_iso8601_duration(offset) if offset else None

        create_params = ScheduleCreate(
            workflow_id=workflow_id,
            cron=cron,
            every=parsed_every,
            offset=parsed_offset,
            start_at=start_at,  # pyright: ignore[reportArgumentType]
            end_at=end_at,  # pyright: ignore[reportArgumentType]
            inputs=parsed_inputs,
            status=schedule_status,  # pyright: ignore[reportArgumentType]
        )

        async with WorkflowSchedulesService.with_session(role=role) as svc:
            schedule = await svc.create_schedule(create_params)
            schedule_read = ScheduleRead.model_validate(schedule)
            return _json(schedule_read.model_dump(mode="json"))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create schedule", error=str(e))
        raise ToolError(f"Failed to create schedule: {e}") from None


def _parse_iso8601_duration(duration_str: str) -> timedelta:
    """Parse a simple ISO 8601 duration string into a timedelta.

    Supports formats like PT1H, PT30M, P1D, PT1H30M, P1DT12H, etc.
    """
    pattern = r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?"
    match = re.fullmatch(pattern, duration_str)
    if not match:
        raise ValueError(f"Invalid ISO 8601 duration: {duration_str}")

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
