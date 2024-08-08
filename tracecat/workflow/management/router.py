from datetime import datetime
from typing import Annotated

import orjson
import yaml
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy.exc import NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config, identifiers, validation
from tracecat.auth.credentials import authenticate_user
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import Webhook, Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.dsl.graph import RFGraph
from tracecat.logging import logger
from tracecat.types.api import (
    ActionResponse,
    CommitWorkflowResponse,
    UDFArgsValidationResponse,
    UpsertWebhookParams,
    WebhookResponse,
)
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.models import (
    UpdateWorkflowParams,
    WorkflowMetadataResponse,
    WorkflowResponse,
)

router = APIRouter(prefix="/workflows")


@router.get("", tags=["workflows"])
async def list_workflows(
    role: Annotated[Role, Depends(authenticate_user)],
    library: bool = False,
    session: AsyncSession = Depends(get_async_session),
) -> list[WorkflowMetadataResponse]:
    """
    List workflows.

    If `library` is True, it will list workflows from the library. If `library` is False, it will list workflows owned by the user.
    """
    query_user_id = role.user_id if not library else config.TRACECAT__DEFAULT_USER_ID
    statement = select(Workflow).where(Workflow.owner_id == query_user_id)
    results = await session.exec(statement)
    workflows = results.all()
    workflow_metadata = [
        WorkflowMetadataResponse(
            id=workflow.id,
            title=workflow.title,
            description=workflow.description,
            status=workflow.status,
            icon_url=workflow.icon_url,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            version=workflow.version,
        )
        for workflow in workflows
    ]
    return workflow_metadata


@router.post("", status_code=status.HTTP_201_CREATED, tags=["workflows"])
async def create_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    title: str | None = Form(None),
    description: str | None = Form(None),
    file: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_async_session),
) -> WorkflowMetadataResponse:
    """Create a new Workflow.

    Optionally, you can provide a YAML file to create a workflow.
    You can also provide a title and description to create a blank workflow."""

    if file:
        try:
            data = await file.read()
            if file.content_type in ("application/yaml", "text/yaml"):
                dsl_data = yaml.safe_load(data)
            elif file.content_type == "application/json":
                dsl_data = orjson.loads(data)
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid file type {file.content_type}. Only YAML and JSON files are supported.",
                )
        except (yaml.YAMLError, orjson.JSONDecodeError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Error parsing file: {str(e)}",
            ) from e

        service = WorkflowsManagementService(session, role)
        result = await service.create_workflow_from_dsl(
            dsl_data, skip_secret_validation=True
        )
        if result.errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "status": "failure",
                    "message": f"Workflow definition construction failed with {len(result.errors)} errors",
                    "errors": [e.model_dump() for e in result.errors],
                },
            )
        workflow = result.workflow
    else:
        now = datetime.now().strftime("%b %d, %Y, %H:%M:%S")
        title = title or now
        description = description or f"New workflow created {now}"

        workflow = Workflow(title=title, description=description, owner_id=role.user_id)
        # When we create a workflow, we automatically create a webhook
        # Add the Workflow to the session first to generate an ID
        session.add(workflow)
        await session.flush()  # Flush to generate workflow.id
        await session.refresh(workflow)

        # Create and associate Webhook with the Workflow
        webhook = Webhook(
            owner_id=role.user_id,
            workflow_id=workflow.id,
        )
        session.add(webhook)
        workflow.webhook = webhook

        graph = RFGraph.with_defaults(workflow)
        workflow.object = graph.model_dump(by_alias=True, mode="json")
        workflow.entrypoint = graph.entrypoint.id if graph.entrypoint else None
        session.add(workflow)
        await session.commit()
        await session.refresh(workflow)

    return WorkflowMetadataResponse(
        id=workflow.id,
        title=workflow.title,
        description=workflow.description,
        status=workflow.status,
        icon_url=workflow.icon_url,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        version=workflow.version,
    )


@router.get("/{workflow_id}", tags=["workflows"])
async def get_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> WorkflowResponse:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    # Get Workflow given workflow_id
    statement = select(Workflow).where(
        Workflow.owner_id == role.user_id, Workflow.id == workflow_id
    )
    result = await session.exec(statement)
    try:
        workflow = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    actions = workflow.actions or []
    actions_responses = {
        action.id: ActionResponse(**action.model_dump()) for action in actions
    }
    # Add webhook/schedules
    return WorkflowResponse(
        **workflow.model_dump(),
        actions=actions_responses,
        webhook=WebhookResponse(**workflow.webhook.model_dump()),
        schedules=workflow.schedules,
    )


@router.patch(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
async def update_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    params: UpdateWorkflowParams,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Update a workflow."""
    statement = select(Workflow).where(
        Workflow.owner_id == role.user_id,
        Workflow.id == workflow_id,
    )
    result = await session.exec(statement)
    try:
        workflow = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    for key, value in params.model_dump(exclude_unset=True).items():
        # Safe because params has been validated
        setattr(workflow, key, value)

    session.add(workflow)
    await session.commit()


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
async def delete_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Delete a workflow."""

    statement = select(Workflow).where(
        Workflow.owner_id == role.user_id,
        Workflow.id == workflow_id,
    )
    result = await session.exec(statement)
    try:
        workflow = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    await session.delete(workflow)
    await session.commit()


@router.post("/{workflow_id}/commit", tags=["workflows"])
async def commit_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> CommitWorkflowResponse:
    """Commit a workflow.

    This deploys the workflow and updates its version. If a YAML file is provided, it will override the workflow in the database."""

    # XXX: This is actually the logical equivalent of creating a workflow definition (deployment)
    # Committing from YAML (i.e. attaching yaml) will override the workflow definition in the database

    with logger.contextualize(role=role):
        # Validate that our target workflow exists
        # Grab workflow and actions from tables
        statement = select(Workflow).where(
            Workflow.owner_id == role.user_id, Workflow.id == workflow_id
        )
        result = await session.exec(statement)
        try:
            workflow = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Could not find workflow",
            ) from e
        # Hydrate actions
        _ = workflow.actions

        # Perform Tiered Validation
        # Tier 1: DSLInput validation
        # Verify that the workflow DSL is structurally sound
        construction_errors = []
        try:
            # Convert the workflow into a WorkflowDefinition
            _ = workflow.actions
            # XXX: When we commit from the workflow, we have action IDs
            dsl = DSLInput.from_workflow(workflow)
        except* TracecatValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                UDFArgsValidationResponse.from_dsl_validation_error(e)
                for e in eg.exceptions
            )
        except* ValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                UDFArgsValidationResponse.from_pydantic_validation_error(e)
                for e in eg.exceptions
            )

        if construction_errors:
            return CommitWorkflowResponse(
                workflow_id=workflow_id,
                status="failure",
                message=f"Workflow definition construction failed with {len(construction_errors)} errors",
                errors=construction_errors,
            )

        # When we're here, we've verified that the workflow DSL is structurally sound
        # Now, we have to ensure that the arguments are sound

        if val_errors := await validation.validate_dsl(dsl):
            logger.warning("Validation errors", errors=val_errors)
            return CommitWorkflowResponse(
                workflow_id=workflow_id,
                status="failure",
                message=f"{len(val_errors)} validation error(s)",
                errors=[
                    UDFArgsValidationResponse.from_validation_result(val_res)
                    for val_res in val_errors
                ],
            )

        # Validation is complete. We can now construct the workflow definition
        # Phase 1: Create workflow definition
        # Workflow definition uses action.refs to refer to actions
        # We should only instantiate action refs at workflow    runtime
        service = WorkflowDefinitionsService(session, role=role)
        # Creating a workflow definition only uses refs
        defn = await service.create_workflow_definition(workflow_id, dsl)

        # Update Workflow
        # We don't need to backpropagate the graph to the workflow beacuse the workflow is the source of truth
        # We only need to update the workflow definition version
        workflow.version = defn.version

        session.add(workflow)
        session.add(defn)
        await session.commit()
        await session.refresh(workflow)
        await session.refresh(defn)

        return CommitWorkflowResponse(
            workflow_id=workflow_id,
            status="success",
            message="Workflow committed successfully.",
            metadata={"version": defn.version},
        )


# ----- Workflow Definitions ----- #


@router.get("/{workflow_id}/definition", tags=["workflows"])
async def list_workflow_definitions(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: identifiers.WorkflowID,
    session: AsyncSession = Depends(get_async_session),
) -> list[WorkflowDefinition]:
    """List all workflow definitions for a Workflow."""
    service = WorkflowDefinitionsService(session, role=role)
    return await service.list_workflow_defitinions(workflow_id=workflow_id)


@router.get("/{workflow_id}/definition", tags=["workflows"])
async def get_workflow_definition(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: identifiers.WorkflowID,
    version: int | None = None,
    session: AsyncSession = Depends(get_async_session),
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
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: identifiers.WorkflowID,
    session: AsyncSession = Depends(get_async_session),
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
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    params: UpsertWebhookParams,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Create a webhook for a workflow."""

    webhook = Webhook(
        owner_id=role.user_id,
        entrypoint_ref=params.entrypoint_ref,
        method=params.method or "POST",
        workflow_id=workflow_id,
    )
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)


@router.get("/{workflow_id}/webhook", tags=["triggers"])
async def get_webhook(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> WebhookResponse:
    """Get the webhook from a workflow."""
    statement = select(Webhook).where(
        Webhook.owner_id == role.user_id,
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
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    params: UpsertWebhookParams,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Update the webhook for a workflow. We currently supprt only one webhook per workflow."""
    result = await session.exec(
        select(Workflow).where(
            Workflow.owner_id == role.user_id, Workflow.id == workflow_id
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
