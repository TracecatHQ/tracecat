import json
from typing import Literal

import orjson
import yaml
from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError
from slugify import slugify
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlmodel import select

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import Webhook, Workflow, WorkflowDefinition
from tracecat.dsl.models import DSLConfig
from tracecat.identifiers import WorkflowID
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionValidateResponse
from tracecat.tags.models import TagRead
from tracecat.types.exceptions import TracecatValidationError
from tracecat.validation.service import validate_dsl
from tracecat.webhooks.models import UpsertWebhookParams, WebhookResponse
from tracecat.workflow.actions.models import ActionRead
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.models import (
    ExternalWorkflowDefinition,
    WorkflowCommitResponse,
    WorkflowCreate,
    WorkflowRead,
    WorkflowReadMinimal,
    WorkflowUpdate,
)

router = APIRouter(prefix="/workflows")


@router.get("", tags=["workflows"])
async def list_workflows(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    filter_tags: list[str] | None = Query(
        default=None,
        description="Filter workflows by tags",
        alias="tag",
    ),
) -> list[WorkflowReadMinimal]:
    """List workflows."""
    service = WorkflowsManagementService(session, role=role)
    workflows = await service.list_workflows(tags=filter_tags)
    res = []
    for workflow in workflows:
        tags = [
            TagRead.model_validate(tag, from_attributes=True) for tag in workflow.tags
        ]
        res.append(
            WorkflowReadMinimal(
                id=workflow.id,
                title=workflow.title,
                description=workflow.description,
                status=workflow.status,
                icon_url=workflow.icon_url,
                created_at=workflow.created_at,
                updated_at=workflow.updated_at,
                version=workflow.version,
                tags=tags,
                alias=workflow.alias,
                error_handler=workflow.error_handler,
            )
        )
    return res


@router.post("", status_code=status.HTTP_201_CREATED, tags=["workflows"])
async def create_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    title: str | None = Form(None),
    description: str | None = Form(None),
    file: UploadFile | None = File(None),
) -> WorkflowReadMinimal:
    """Create a new Workflow.

    Optionally, you can provide a YAML file to create a workflow.
    You can also provide a title and description to create a blank workflow."""

    service = WorkflowsManagementService(session, role=role)
    if file:
        raw_data = await file.read()
        match file.content_type:
            case (
                "application/yaml"
                | "text/yaml"
                | "application/x-yaml"
                | "application/octet-stream"
            ):
                logger.info("Parsing YAML file", file=file.filename)
                try:
                    external_defn_data = yaml.safe_load(raw_data)
                except yaml.YAMLError as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Error parsing YAML file: {e!r}",
                    ) from e
            case "application/json":
                logger.info("Parsing JSON file", file=file.filename)
                try:
                    external_defn_data = orjson.loads(raw_data)
                except orjson.JSONDecodeError as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Error parsing JSON file: {e!r}",
                    ) from e
            case None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Content-Type header is required for file uploads.",
                )
            case _:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid file type {file.content_type}. Only YAML and JSON files are supported.",
                )

        logger.info("Importing workflow", external_defn_data=external_defn_data)
        try:
            workflow = await service.create_workflow_from_external_definition(
                external_defn_data
            )
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=json.dumps(
                    {
                        "status": "failure",
                        "message": "Error validating external workflow definition",
                        "errors": e.errors(),
                    },
                    indent=2,
                ),
            ) from e
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workflow already exists",
            ) from e
    else:
        workflow = await service.create_workflow(
            WorkflowCreate(title=title, description=description)
        )
    return WorkflowReadMinimal(
        id=workflow.id,
        title=workflow.title,
        description=workflow.description,
        status=workflow.status,
        icon_url=workflow.icon_url,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        version=workflow.version,
        error_handler=workflow.error_handler,
    )


@router.get("/{workflow_id}", tags=["workflows"])
async def get_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
) -> WorkflowRead:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    # Get Workflow given workflow_id
    service = WorkflowsManagementService(session, role=role)
    workflow = await service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
        )

    actions = workflow.actions or []
    actions_responses = {
        action.id: ActionRead(**action.model_dump()) for action in actions
    }
    # Add webhook/schedules
    return WorkflowRead(
        id=workflow.id,
        owner_id=workflow.owner_id,
        title=workflow.title,
        description=workflow.description,
        status=workflow.status,
        version=workflow.version,
        expects=workflow.expects,
        returns=workflow.returns,
        entrypoint=workflow.entrypoint,
        object=workflow.object,
        static_inputs=workflow.static_inputs,
        config=DSLConfig(**workflow.config),
        actions=actions_responses,
        webhook=WebhookResponse(**workflow.webhook.model_dump()),
        schedules=workflow.schedules or [],
        alias=workflow.alias,
        error_handler=workflow.error_handler,
    )


@router.patch(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
async def update_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    params: WorkflowUpdate,
) -> None:
    """Update a workflow."""
    service = WorkflowsManagementService(session, role=role)
    try:
        await service.update_workflow(workflow_id, params=params)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
async def delete_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
) -> None:
    """Delete a workflow."""

    service = WorkflowsManagementService(session, role=role)
    try:
        await service.delete_workflow(workflow_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e


@router.post("/{workflow_id}/commit", tags=["workflows"])
async def commit_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
) -> WorkflowCommitResponse:
    """Commit a workflow.

    This deploys the workflow and updates its version. If a YAML file is provided, it will override the workflow in the database."""

    # XXX: This is actually the logical equivalent of creating a workflow definition (deployment)
    # Committing from YAML (i.e. attaching yaml) will override the workflow definition in the database

    with logger.contextualize(role=role):
        mgmt_service = WorkflowsManagementService(session, role=role)
        workflow = await mgmt_service.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Could not find workflow"
            )

        # Perform Tiered Validation
        # Tier 1: DSLInput validation
        # Verify that the workflow DSL is structurally sound
        construction_errors = []
        try:
            # Convert the workflow into a WorkflowDefinition
            # XXX: When we commit from the workflow, we have action IDs
            dsl = await mgmt_service.build_dsl_from_workflow(workflow)
        except* TracecatValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                RegistryActionValidateResponse.from_dsl_validation_error(e)
                for e in eg.exceptions
            )
        except* ValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                RegistryActionValidateResponse.from_pydantic_validation_error(e)
                for e in eg.exceptions
            )

        if construction_errors:
            return WorkflowCommitResponse(
                workflow_id=workflow_id,
                status="failure",
                message=f"Workflow definition construction failed with {len(construction_errors)} errors",
                errors=construction_errors,
            )

        # When we're here, we've verified that the workflow DSL is structurally sound
        # Now, we have to ensure that the arguments are sound

        if val_errors := await validate_dsl(session=session, dsl=dsl):
            logger.warning("Validation errors", errors=val_errors)
            return WorkflowCommitResponse(
                workflow_id=workflow_id,
                status="failure",
                message=f"{len(val_errors)} validation error(s)",
                errors=[
                    RegistryActionValidateResponse.from_validation_result(val_res)
                    for val_res in val_errors
                ],
            )

        # Validation is complete. We can now construct the workflow definition
        # Phase 1: Create workflow definition
        # Workflow definition uses action.refs to refer to actions
        # We should only instantiate action refs at workflow    runtime
        service = WorkflowDefinitionsService(session, role=role)
        # Creating a workflow definition only uses refs
        defn = await service.create_workflow_definition(workflow_id, dsl, commit=False)

        # Update Workflow
        # We don't need to backpropagate the graph to the workflow beacuse the workflow is the source of truth
        # We only need to update the workflow definition version
        workflow.version = defn.version

        session.add(workflow)
        session.add(defn)
        await session.commit()
        await session.refresh(workflow)
        await session.refresh(defn)

        return WorkflowCommitResponse(
            workflow_id=workflow_id,
            status="success",
            message="Workflow committed successfully.",
            metadata={"version": defn.version},
        )


@router.get("/{workflow_id}/export", tags=["workflows"])
async def export_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    format: Literal["json", "yaml"] = Query(
        default="json", description="Export format: 'json' or 'yaml'"
    ),
    version: int | None = Query(
        default=None,
        description="Workflow definition version. If not provided, the latest version is exported.",
    ),
):
    """
    Export a workflow's current state and optionally its definitions and logs.

    Supported formats are JSON and CSV.
    """
    logger.info(
        "Exporting workflow", workflow_id=workflow_id, format=format, version=version
    )
    service = WorkflowDefinitionsService(session, role=role)
    defn = await service.get_definition_by_workflow_id(workflow_id, version=version)
    if not defn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow definition not found",
        )
    external_defn = ExternalWorkflowDefinition.from_database(defn)
    filename = f"{external_defn.workflow_id}__{slugify(external_defn.definition.title)}.{format}"
    if format == "json":
        return Response(
            content=external_defn.model_dump_json(indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    elif format == "yaml":
        return Response(
            content=yaml.dump(external_defn.model_dump(mode="json"), indent=2),
            media_type="application/yaml",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{format!r} is not a supported export format",
        )


# ----- Workflow Definitions ----- #


@router.get("/{workflow_id}/definition", tags=["workflows"])
async def list_workflow_definitions(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
) -> list[WorkflowDefinition]:
    """List all workflow definitions for a Workflow."""
    service = WorkflowDefinitionsService(session, role=role)
    return await service.list_workflow_defitinions(workflow_id=workflow_id)


@router.get("/{workflow_id}/definition", tags=["workflows"])
async def get_workflow_definition(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    version: int | None = None,
) -> WorkflowDefinition:
    """Get the latest version of a workflow definition."""
    service = WorkflowDefinitionsService(session, role=role)
    definition = await service.get_definition_by_workflow_id(
        workflow_id, version=version
    )
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    return definition


@router.post("/{workflow_id}/definition", tags=["workflows"])
async def create_workflow_definition(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
) -> WorkflowDefinition:
    """Get the latest version of a workflow definition."""
    service = WorkflowDefinitionsService(session, role=role)
    return await service.create_workflow_definition(workflow_id)


# ----- Workflow Webhooks ----- #


@router.post(
    "/{workflow_id}/webhook",
    status_code=status.HTTP_201_CREATED,
    tags=["triggers"],
)
async def create_webhook(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    params: UpsertWebhookParams,
) -> None:
    """Create a webhook for a workflow."""

    webhook = Webhook(
        owner_id=role.workspace_id,
        method=params.method or "POST",
        workflow_id=workflow_id,
    )  # type: ignore
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)


@router.get("/{workflow_id}/webhook", tags=["triggers"])
async def get_webhook(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
) -> WebhookResponse:
    """Get the webhook from a workflow."""
    statement = select(Webhook).where(
        Webhook.owner_id == role.workspace_id,
        Webhook.workflow_id == workflow_id,
    )
    result = await session.exec(statement)
    try:
        webhook = result.one()
        return WebhookResponse(**webhook.model_dump())
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e


@router.patch(
    "/{workflow_id}/webhook",
    tags=["triggers"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_webhook(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    params: UpsertWebhookParams,
) -> None:
    """Update the webhook for a workflow. We currently supprt only one webhook per workflow."""
    result = await session.exec(
        select(Workflow).where(
            Workflow.owner_id == role.workspace_id, Workflow.id == workflow_id
        )
    )
    try:
        workflow = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    webhook = workflow.webhook

    for key, value in params.model_dump(exclude_unset=True).items():
        # Safety: params have been validated
        setattr(webhook, key, value)

    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)
