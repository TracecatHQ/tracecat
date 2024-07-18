import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

import orjson
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.params import Body
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import Engine, delete, or_
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from sqlmodel import Session, select

from tracecat import config, identifiers, validation
from tracecat.auth.credentials import (
    TemporaryRole,
    authenticate_service,
    authenticate_user,
    authenticate_user_or_service,
)
from tracecat.contexts import ctx_role
from tracecat.db.engine import clone_workflow, get_engine
from tracecat.db.schemas import (
    Action,
    Case,
    CaseAction,
    CaseContext,
    CaseEvent,
    Schedule,
    Secret,
    UDFSpec,
    User,
    Webhook,
    Workflow,
    WorkflowDefinition,
)
from tracecat.dsl import schedules
from tracecat.dsl.common import DSLInput

# TODO: Clean up API params / response "zoo"
# lots of repetition and inconsistency
from tracecat.dsl.graph import RFGraph
from tracecat.dsl.validation import validate_trigger_inputs
from tracecat.logging import logger
from tracecat.middleware import RequestLoggingMiddleware
from tracecat.parse import parse_child_webhook
from tracecat.secrets.service import SecretsService
from tracecat.types.api import (
    ActionMetadataResponse,
    ActionResponse,
    CaseActionParams,
    CaseContextParams,
    CaseEventParams,
    CaseParams,
    CaseResponse,
    CommitWorkflowResponse,
    CopyWorkflowParams,
    CreateActionParams,
    CreateScheduleParams,
    CreateSecretParams,
    CreateWorkflowParams,
    SearchScheduleParams,
    SearchSecretsParams,
    SecretResponse,
    ServiceCallbackAction,
    UDFArgsValidationResponse,
    UpdateActionParams,
    UpdateScheduleParams,
    UpdateSecretParams,
    UpdateUserParams,
    UpdateWorkflowParams,
    UpsertWebhookParams,
    WebhookResponse,
    WorkflowMetadataResponse,
    WorkflowResponse,
)
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatException, TracecatValidationError
from tracecat.workflow.models import (
    CreateWorkflowExecutionParams,
    CreateWorkflowExecutionResponse,
    EventHistoryResponse,
    WorkflowExecutionResponse,
)
from tracecat.workflow.service import WorkflowExecutionsService

engine: Engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = get_engine()
    yield


def custom_generate_unique_id(route: APIRoute):
    logger.info("Generating unique ID for route", tags=route.tags, name=route.name)
    if route.tags:
        return f"{route.tags[0]}-{route.name}"
    return route.name


def create_app(**kwargs) -> FastAPI:
    global logger
    if config.TRACECAT__APP_ENV == "production":
        cors_origins_kwargs = {"allow_origins": config.TRACECAT__ALLOW_ORIGINS}
    elif config.TRACECAT__APP_ENV == "staging":
        cors_origins_kwargs = {"allow_origins": config.TRACECAT__ALLOW_ORIGINS}
    else:
        cors_origins_kwargs = {"allow_origins": "*"}
    app = FastAPI(
        title="Tracecat API",
        description=(
            "Tracecat is the security automation platform built for builders."
            " You can operate Tracecat in headless mode by using the API to create, manage, and run workflows."
        ),
        summary="Tracecat API",
        version="0.1.0",
        terms_of_service="https://docs.google.com/document/d/e/2PACX-1vQvDe3SoVAPoQc51MgfGCP71IqFYX_rMVEde8zC4qmBCec5f8PLKQRdxa6tsUABT8gWAR9J-EVs2CrQ/pub",
        contact={"name": "Tracecat Founders", "email": "founders@tracecat.com"},
        license_info={
            "name": "AGPL-3.0",
            "url": "https://www.gnu.org/licenses/agpl-3.0.html",
        },
        openapi_tags=[
            {"name": "public", "description": "Public facing endpoints"},
            {"name": "workflows", "description": "Workflow management"},
            {"name": "actions", "description": "Action management"},
            {"name": "triggers", "description": "Workflow triggers"},
            {"name": "secrets", "description": "Secret management"},
            {"name": "udfs", "description": "User-defined functions"},
            {"name": "events", "description": "Event management"},
            {"name": "cases", "description": "Case management"},
        ],
        generate_unique_id_function=custom_generate_unique_id,
        **kwargs,
    )
    app.logger = logger
    app.add_middleware(
        CORSMiddleware,
        **cors_origins_kwargs,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    logger.warning(
        "App started", env=config.TRACECAT__APP_ENV, origins=cors_origins_kwargs
    )
    return app


app = create_app(lifespan=lifespan, default_response_class=ORJSONResponse)


# ----- Utility ----- #


# Catch-all exception handler to prevent stack traces from leaking
@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unexpected error",
        exc=exc,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "An unexpected error occurred. Please try again later."},
    )


@app.exception_handler(TracecatValidationError)
@app.exception_handler(TracecatException)
async def tracecat_exception_handler(request: Request, exc: TracecatException):
    """Generic exception handler for Tracecat exceptions.

    We can customize exceptions to expose only what should be user facing.
    """
    msg = str(exc)
    logger.error(
        msg,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"type": type(exc).__name__, "message": msg, "detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Improves visiblity of 422 errors."""
    exc_str = f"{exc}".replace("\n", " ").replace("   ", " ")
    logger.error(f"{request}: {exc_str}")
    return ORJSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=exc_str
    )


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the API."}


@app.get("/health")
def check_health() -> dict[str, str]:
    return {"message": "Hello world. I am the API. This is the health endpoint."}


# ----- Dependencies ----- #
def get_session():
    with Session(engine) as session:
        yield session


# ----- Trigger handlers ----- #


def validate_incoming_webhook(
    webhook_id: str,
    secret: str,
    request: Request,
    *,
    validate_method: bool = True,
) -> WorkflowDefinition:
    """Validate incoming webhook request.

    NOte: The webhook ID here is the workflow ID.
    """
    with Session(engine) as session:
        result = session.exec(select(Webhook).where(Webhook.workflow_id == webhook_id))
        try:
            # One webhook per workflow
            webhook = result.one()
        except NoResultFound as e:
            logger.opt(exception=e).error("Webhook does not exist", error=e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized webhook request.",
            ) from e

        if secret != webhook.secret:
            logger.error("Secret does not match")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized webhook request",
            )

        # If we're here, the webhook has been validated
        if webhook.status == "offline":
            logger.error("Webhook is offline")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook is offline",
            )

        if validate_method and webhook.method.lower() != request.method.lower():
            logger.error("Method does not match")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request method not allowed",
            )

        # Reaching here means the webhook is online and connected to an entrypoint

        # Match the webhook id with the workflow id and get the latest version
        # of the workflow defitniion.
        result = session.exec(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == webhook_id)
            .order_by(WorkflowDefinition.version.desc())
        )
        try:
            defn = result.first()
            if not defn:
                raise NoResultFound("No workflow definition found for workflow ID")
        except NoResultFound as e:
            # No workflow associated with the webhook
            logger.opt(exception=e).error("Invalid workflow ID", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid workflow ID"
            ) from e

        # Check if the workflow is active

        if defn.workflow.status == "offline":
            logger.error("Workflow is offline")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workflow is offline",
            )

        # If we are here, all checks have passed
        return WorkflowDefinition.model_validate(defn)


async def handle_incoming_webhook(
    request: Request, path: str, secret: str, validate_method: bool = True
) -> WorkflowDefinition:
    """Handle an incoming webhook request and set the Role context."""
    # TODO(perf): Replace this when we get async sessions
    with logger.contextualize(webhook_id=path):
        defn = await asyncio.to_thread(
            validate_incoming_webhook,
            webhook_id=path,
            secret=secret,
            request=request,
            validate_method=validate_method,
        )
    ctx_role.set(
        Role(type="service", user_id=defn.owner_id, service_id="tracecat-runner")
    )
    return defn


@app.post("/webhooks/{path}/{secret}", tags=["public"])
async def incoming_webhook(
    defn: Annotated[WorkflowDefinition, Depends(handle_incoming_webhook)],
    path: str,
    payload: dict[str, Any] | None = None,
    x_tracecat_enable_runtime_tests: Annotated[str | None, Header()] = None,
) -> CreateWorkflowExecutionResponse:
    """
    Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    logger.info(
        "Webhook hit",
        path=path,
        payload=payload,
        role=ctx_role.get(),
    )

    dsl_input = DSLInput(**defn.content)

    enable_runtime_tests = (x_tracecat_enable_runtime_tests or "false").lower() in (
        "1",
        "true",
    )

    service = await WorkflowExecutionsService.connect()
    response = service.create_workflow_execution(
        dsl=dsl_input,
        wf_id=path,
        payload=payload,
        enable_runtime_tests=enable_runtime_tests,
    )
    return response


async def handle_service_callback(
    request: Request, service: str
) -> ServiceCallbackAction | None:
    if service == "slack":
        # if (
        #     request.headers["user-agent"]
        #     != "Slackbot 1.0 (+https://api.slack.com/robots)"
        # ):
        #     raise HTTPException(
        #         status_code=status.HTTP_400_BAD_REQUEST,
        #         detail="Invalid User-Agent",
        #     )
        # NOTE: This coroutine can only be consumed once!
        form_data = await request.form()
        json_payload = form_data.get("payload")
        payload = orjson.loads(json_payload)
        # Extract out the webhook
        if (actions := payload.get("actions")) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack callback: Invalid payload",
            )
        logger.info("Received slack action", actions=actions)
        if len(actions) < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack callback: No actions",
            )
        action = actions[0]
        match action:
            case {
                "type": "static_select",
                "action_id": url,
                "selected_option": {"value": kv_params},
            }:
                # e.g.
                # {
                #     "type": "static_select",
                #     "action_id": "...",
                #     "block_id": "nMMIK",
                #     "selected_option": {
                #         "text": {
                #             "type": "plain_text",
                #             "text": "True Positive",
                #             "emoji": True,
                #         },
                #         "value": '["closed", "true_positive"]',
                #     },
                #     "action_ts": "1719281272.742854",
                # }

                logger.info("Matched static select action", action=action)
                child_wh = parse_child_webhook(url, [kv_params])

                if not child_wh:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid child webhook URL",
                    )
                return ServiceCallbackAction(
                    action="webhook",
                    payload=child_wh["payload"],
                    metadata={"path": child_wh["path"], "secret": child_wh["secret"]},
                )

            case {"type": action_type}:
                logger.error(
                    "Invalid action type", action_type=action_type, action=action
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid action type {action_type}",
                )
            case _:
                logger.error("Invalid action", action=action)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid action",
                )
    return None


@app.post("/callback/{service}", tags=["public"])
async def webhook_callback(
    request: Request,
    service: str,
    next_action: Annotated[
        ServiceCallbackAction | None, Depends(handle_service_callback)
    ],
) -> dict[str, str]:
    """Receive a callback from an external service.

    This can be used to trigger a workflow from an external service, or perform some other actions.
    """

    match next_action:
        case ServiceCallbackAction(
            action="webhook",
            payload=payload,
            metadata={"path": path, "secret": secret},
        ):
            # Don't validate method because callback is always POST
            defn = await handle_incoming_webhook(
                request, path, secret, validate_method=False
            )
            logger.info(
                "Received Webhook in callback",
                service=service,
                path=path,
                payload=payload,
                role=ctx_role.get(),
            )

            # Fetch the DSL from the workflow object
            dsl_input = DSLInput(**defn.content)

            wf_exec_service = await WorkflowExecutionsService.connect()
            response = wf_exec_service.create_workflow_execution(
                dsl=dsl_input,
                wf_id=path,
                payload=payload,
            )
            return {
                "status": "ok",
                "message": "Webhook callback processed",
                "service": service,
                "details": response,
            }

        case None:
            logger.info("No next action", service=service)
            return {"status": "ok", "message": "No action taken", "service": service}
        case _:
            logger.error("Unsupported next action", next_action=next_action)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported next action in webhook callback for {service!r} service",
            )


# ----- Workflows ----- #


@app.get("/workflows", tags=["workflows"])
def list_workflows(
    role: Annotated[Role, Depends(authenticate_user)],
    library: bool = False,
) -> list[WorkflowMetadataResponse]:
    """
    List workflows.

    If `library` is True, it will list workflows from the library. If `library` is False, it will list workflows owned by the user.
    """
    query_user_id = role.user_id if not library else "tracecat"
    with Session(engine) as session:
        statement = select(Workflow).where(Workflow.owner_id == query_user_id)
        results = session.exec(statement)
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


@app.post("/workflows", status_code=status.HTTP_201_CREATED, tags=["workflows"])
def create_workflow(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    params: CreateWorkflowParams,
) -> WorkflowMetadataResponse:
    """Create new Workflow with title and description."""

    now = datetime.now().strftime("%b %d, %Y, %H:%M:%S")
    title = now if params.title is None else params.title
    # Create the message
    description = (
        f"New workflow created {now}"
        if params.description is None
        else params.description
    )

    with Session(engine) as session:
        workflow = Workflow(
            title=title,
            description=description,
            owner_id=role.user_id,
        )
        # When we create a workflow, we automatically create a webhook
        webhook = Webhook(
            owner_id=role.user_id,
            workflow_id=workflow.id,
        )
        graph = RFGraph.with_defaults(workflow, webhook)
        workflow.object = graph.model_dump(by_alias=True)
        session.add(workflow)
        session.add(webhook)
        session.commit()
        session.refresh(workflow)
        session.refresh(webhook)

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


@app.get("/workflows/{workflow_id}", tags=["workflows"])
def get_workflow(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    workflow_id: str,
) -> WorkflowResponse:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    with Session(engine) as session:
        # Get Workflow given workflow_id
        statement = select(Workflow).where(
            Workflow.owner_id == role.user_id,
            Workflow.id == workflow_id,
        )
        result = session.exec(statement)
        try:
            workflow = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

        actions_responses = {
            action.id: ActionResponse(**action.model_dump())
            for action in workflow.actions or []
        }
        # Add webhook/schedules
        return WorkflowResponse(
            **workflow.model_dump(),
            actions=actions_responses,
            webhook=WebhookResponse(**workflow.webhook.model_dump()),
            schedules=workflow.schedules,
        )


@app.patch(
    "/workflows/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
def update_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    params: UpdateWorkflowParams,
) -> None:
    """Update a workflow."""
    with Session(engine) as session:
        statement = select(Workflow).where(
            Workflow.owner_id == role.user_id,
            Workflow.id == workflow_id,
        )
        result = session.exec(statement)
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
        session.commit()


@app.delete(
    "/workflows/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
def delete_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
) -> None:
    """Delete a workflow."""

    with Session(engine) as session:
        statement = select(Workflow).where(
            Workflow.owner_id == role.user_id,
            Workflow.id == workflow_id,
        )
        result = session.exec(statement)
        try:
            workflow = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        session.delete(workflow)
        session.commit()


@app.post(
    "/workflows/{workflow_id}/copy",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
def copy_workflow(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    workflow_id: str,
    params: Annotated[CopyWorkflowParams | None, Body(...)] = None,
) -> None:
    """Copy a workflow. Not intended for users."""
    if role.type == "user" and params is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Users can only clone tracecat workflows",
        )
    # Users cannot pass owner_id, and defaults to 'tracecat'
    owner_id = params.owner_id if params else "tracecat"
    with Session(engine) as session:
        statement = select(Workflow).where(
            Workflow.owner_id == owner_id,
            Workflow.id == workflow_id,
        )
        result = session.exec(statement)
        try:
            workflow = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

        # Assign it a new workflow ID and owner ID
        new_workflow = clone_workflow(workflow, session, role.user_id)
        session.commit()
        session.refresh(new_workflow)


@app.post("/workflows/{workflow_id}/commit", tags=["workflows"])
async def commit_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    yaml_file: UploadFile = File(None),
    session: Session = Depends(get_session),
) -> ORJSONResponse:
    """Commit a workflow.

    This deploys the workflow and updates its version. If a YAML file is provided, it will override the workflow in the database."""

    # Committing from YAML (i.e. attaching yaml) will override the workflow definition in the database

    with logger.contextualize(role=role):
        try:
            # Validate that our target workflow exists
            # Grab workflow and actions from tables
            statement = select(Workflow).where(
                Workflow.owner_id == role.user_id,
                Workflow.id == workflow_id,
            )
            result = session.exec(statement)
            try:
                workflow = result.one()
            except NoResultFound as e:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
                ) from e
            # Hydrate actions
            _ = workflow.actions

            # Perform Tiered Validation
            # Tier 1: DSLInput validation
            # Verify that the workflow DSL is structurally sound
            construction_errors = []
            try:
                if yaml_file:
                    # Uploaded YAML file overrides the workflow in the database
                    dsl = DSLInput.from_yaml(yaml_file.file)
                    logger.info("Commiting workflow from yaml file")
                else:
                    # Convert the workflow into a WorkflowDefinition
                    dsl = DSLInput.from_workflow(workflow)
                    logger.info("Commiting workflow from database")
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
                    metadata={"filename": yaml_file.filename} if yaml_file else None,
                ).to_orjson(status.HTTP_400_BAD_REQUEST)

            # When we're here, we've verified that the workflow DSL is structurally sound
            # Now, we have to ensure that the arguments are sound

            if val_errors := await validation.validate_dsl(session=session, dsl=dsl):
                logger.warning("Validation errors", errors=val_errors)
                return CommitWorkflowResponse(
                    workflow_id=workflow_id,
                    status="failure",
                    message=f"{len(val_errors)} validation error(s)",
                    errors=[
                        UDFArgsValidationResponse.from_validation_result(val_res)
                        for val_res in val_errors
                    ],
                    metadata={"filename": yaml_file.filename} if yaml_file else None,
                ).to_orjson(status.HTTP_400_BAD_REQUEST)

            # Phase 1: Commit
            defn = _create_wf_definition(session, role, workflow_id, dsl)
            # Phase 2: Backpropagate
            new_graph = dsl.to_graph(workflow)

            # Replace Actions
            del_stmt = delete(Action).where(
                Action.workflow_id == workflow_id, Action.owner_id == role.user_id
            )
            session.exec(del_stmt)
            session.flush()  # Ensure deletions are flushed
            session.refresh(workflow)

            for act_stmt in dsl.actions:
                new_action = Action(
                    id=identifiers.action.key(workflow_id, act_stmt.ref),
                    owner_id=role.user_id,
                    workflow_id=workflow_id,
                    type=act_stmt.action,
                    inputs=act_stmt.args,
                    title=act_stmt.title,
                    description=act_stmt.description,
                )
                session.add(new_action)

            # Update Workflow
            workflow.object = new_graph.model_dump(by_alias=True)
            workflow.version = defn.version
            workflow.title = dsl.title
            workflow.description = dsl.description
            workflow.entrypoint = (
                new_graph.entrypoint.id if new_graph.entrypoint else None
            )

            session.add(workflow)
            session.add(defn)
            session.commit()
            session.refresh(workflow)
            session.refresh(defn)

            return CommitWorkflowResponse(
                workflow_id=workflow_id,
                status="success",
                message="Workflow committed successfully.",
                metadata={"version": defn.version},
            ).to_orjson(status.HTTP_200_OK)

        except SQLAlchemyError as e:
            session.rollback()
            logger.opt(exception=e).error("Error committing workflow", error=e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while committing the workflow.",
            ) from e


def _create_wf_definition(
    session: Session, role: Role, workflow_id: str, dsl: DSLInput
) -> WorkflowDefinition:
    statement = (
        select(WorkflowDefinition)
        .where(
            WorkflowDefinition.owner_id == role.user_id,
            WorkflowDefinition.workflow_id == workflow_id,
        )
        .order_by(WorkflowDefinition.version.desc())
    )
    result = session.exec(statement)
    latest_defn = result.first()

    version = latest_defn.version + 1 if latest_defn else 1
    defn = WorkflowDefinition(
        owner_id=role.user_id,
        workflow_id=workflow_id,
        content=dsl.model_dump(),
        version=version,
    )
    return defn


# ----- Workflow Definitions ----- #


@app.get("/workflows/{workflow_id}/definition", tags=["workflows"])
async def list_workflow_definitions(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    workflow_id: str,
) -> list[WorkflowDefinition]:
    """List all workflow definitions for a Workflow."""
    with Session(engine) as session:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == role.user_id,
        )
        if workflow_id:
            statement = statement.where(WorkflowDefinition.workflow_id == workflow_id)
        result = session.exec(statement)
        return result.all()


@app.get("/workflows/{workflow_id}/definition", tags=["workflows"])
def get_workflow_definition(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    workflow_id: str,
    version: int | None = None,
) -> WorkflowDefinition:
    """Get the latest version of a workflow definition."""
    with Session(engine) as session:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == role.user_id,
            WorkflowDefinition.workflow_id == workflow_id,
        )
        if version:
            statement = statement.where(WorkflowDefinition.version == version)
        else:
            # Get the latest version
            statement = statement.order_by(WorkflowDefinition.version.desc())

        result = session.exec(statement)
        try:
            defn = result.first()
            if not defn:
                raise NoResultFound
            return defn
        except NoResultFound as e:
            logger.opt(exception=e).error("Workflow definition does not exist", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid workflow ID"
            ) from e


# ----- Workflow Executions ----- #


@app.get("/workflow-executions", tags=["workflow-executions"])
async def list_workflow_executions(
    role: Annotated[Role, Depends(authenticate_user)],
    # Filters
    workflow_id: identifiers.WorkflowID | None = Query(None),
) -> list[WorkflowExecutionResponse]:
    """List all workflow executions."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        if workflow_id:
            executions = await service.list_executions_by_workflow_id(workflow_id)
        else:
            executions = await service.list_executions()
        return [
            WorkflowExecutionResponse.from_dataclass(execution)
            for execution in executions
        ]


@app.get("/workflow-executions/{execution_id}", tags=["workflow-executions"])
async def get_workflow_execution(
    role: Annotated[Role, Depends(authenticate_user)],
    execution_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID,
) -> WorkflowExecutionResponse:
    """Get a workflow execution."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        execution = await service.get_execution(execution_id)
        return WorkflowExecutionResponse.from_dataclass(execution)


@app.get("/workflow-executions/{execution_id}/history", tags=["workflow-executions"])
async def list_workflow_execution_event_history(
    role: Annotated[Role, Depends(authenticate_user)],
    execution_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID,
) -> list[EventHistoryResponse]:
    """Get a workflow execution."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        events = await service.list_workflow_execution_event_history(execution_id)
        return events


@app.post("/workflow-executions", tags=["workflow-executions"])
async def create_workflow_execution(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CreateWorkflowExecutionParams,
    session: Session = Depends(get_session),
) -> CreateWorkflowExecutionResponse:
    """Create and schedule a workflow execution."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        # Get the dslinput from the workflow definition
        try:
            result = session.exec(
                select(WorkflowDefinition)
                .where(WorkflowDefinition.workflow_id == params.workflow_id)
                .order_by(WorkflowDefinition.version.desc())
            )
            defn = result.first()
            if not defn:
                raise NoResultFound("No workflow definition found for workflow ID")
        except NoResultFound as e:
            # No workflow associated with the webhook
            logger.opt(exception=e).error("Invalid workflow ID", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid workflow ID"
            ) from e
        dsl_input = DSLInput(**defn.content)
        try:
            response = service.create_workflow_execution(
                dsl=dsl_input,
                wf_id=params.workflow_id,
                payload=params.inputs,
                enable_runtime_tests=params.enable_runtime_tests,
            )
            return response
        except TracecatValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "TracecatValidationError",
                    "message": str(e),
                    "detail": e.detail,
                },
            ) from e


# ----- Workflow Webhooks ----- #


@app.post(
    "/workflows/{workflow_id}/webhook",
    status_code=status.HTTP_201_CREATED,
    tags=["triggers"],
)
def create_webhook(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    workflow_id: str,
    params: UpsertWebhookParams,
) -> None:
    """Create a webhook for a workflow."""

    webhook = Webhook(
        owner_id=role.user_id,
        entrypoint_ref=params.entrypoint_ref,
        method=params.method or "POST",
        workflow_id=workflow_id,
    )
    with Session(engine) as session:
        session.add(webhook)
        session.commit()
        session.refresh(webhook)


@app.get("/workflows/{workflow_id}/webhook", tags=["triggers"])
def get_webhook(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
) -> WebhookResponse:
    """Get the webhook from a workflow."""
    with Session(engine) as session:
        statement = select(Webhook).where(
            Webhook.owner_id == role.user_id,
            Webhook.workflow_id == workflow_id,
        )
        result = session.exec(statement)
        try:
            webhook = result.one()
            return WebhookResponse(**webhook.model_dump())
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e


@app.patch(
    "/workflows/{workflow_id}/webhook",
    tags=["triggers"],
    status_code=status.HTTP_204_NO_CONTENT,
)
def update_webhook(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    params: UpsertWebhookParams,
) -> None:
    """Update the webhook for a workflow. We currently supprt only one webhook per workflow."""
    with Session(engine) as session:
        result = session.exec(
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
        session.commit()
        session.refresh(webhook)


# ----- Workflow Schedules ----- #


@app.get("/schedules", tags=["schedules"])
async def list_schedules(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: identifiers.WorkflowID | None = None,
) -> list[Schedule]:
    """List all schedules for a workflow."""
    with Session(engine) as session:
        statement = select(Schedule).where(Schedule.owner_id == role.user_id)
        if workflow_id:
            statement = statement.where(Schedule.workflow_id == workflow_id)
        result = session.exec(statement)
        try:
            return result.all()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e


@app.post("/schedules", tags=["schedules"])
async def create_schedule(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CreateScheduleParams,
) -> Schedule:
    """Create a schedule for a workflow."""

    with Session(engine) as session, logger.contextualize(role=role):
        result = session.exec(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == params.workflow_id)
            .order_by(WorkflowDefinition.version.desc())
        )
        try:
            if not (defn_data := result.first()):
                raise NoResultFound("No workflow definition found for workflow ID")
        except NoResultFound as e:
            logger.opt(exception=e).error("Invalid workflow ID", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid workflow ID"
            ) from e

        schedule = Schedule(
            owner_id=role.user_id, **params.model_dump(exclude_unset=True)
        )
        session.refresh(defn_data)
        defn = WorkflowDefinition.model_validate(defn_data)
        dsl = DSLInput(**defn.content)
        if params.inputs:
            dsl.trigger_inputs = params.inputs

        try:
            # Set the role for the schedule as the tracecat-runner
            with TemporaryRole(
                type="service",
                user_id=defn.owner_id,
                service_id="tracecat-schedule-runner",
            ) as sch_role:
                handle = await schedules.create_schedule(
                    workflow_id=params.workflow_id,
                    schedule_id=schedule.id,
                    dsl=dsl,
                    every=params.every,
                    offset=params.offset,
                    start_at=params.start_at,
                    end_at=params.end_at,
                )
                logger.info(
                    "Created schedule",
                    handle_id=handle.id,
                    workflow_id=params.workflow_id,
                    schedule_id=schedule.id,
                    sch_role=sch_role,
                )

            session.add(schedule)
            session.commit()
            session.refresh(schedule)
            return schedule
        except Exception as e:
            session.rollback()
            logger.opt(exception=e).error("Error creating schedule", error=e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error creating schedule",
            ) from e


@app.get("/schedules/{schedule_id}", tags=["schedules"])
def get_schedule(
    role: Annotated[Role, Depends(authenticate_user)],
    schedule_id: identifiers.ScheduleID,
) -> Schedule:
    """Get a schedule from a workflow."""
    with Session(engine) as session:
        statement = select(Schedule).where(
            Schedule.owner_id == role.user_id, Schedule.id == schedule_id
        )
        result = session.exec(statement)
        try:
            return result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e


@app.post("/schedules/{schedule_id}", tags=["schedules"])
async def update_schedule(
    role: Annotated[Role, Depends(authenticate_user)],
    schedule_id: identifiers.ScheduleID,
    params: UpdateScheduleParams,
) -> Schedule:
    """Update a schedule from a workflow. You cannot update the Workflow Definition, but you can update other fields."""
    with Session(engine) as session:
        statement = select(Schedule).where(
            Schedule.owner_id == role.user_id, Schedule.id == schedule_id
        )
        result = session.exec(statement)
        try:
            schedule = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

        try:
            # (1) Synchronize with Temporal
            await schedules.update_schedule(schedule_id, params)

            # (2) Update the schedule
            for key, value in params.model_dump(exclude_unset=True).items():
                # Safety: params have been validated
                setattr(schedule, key, value)

            session.add(schedule)
            session.commit()
            session.refresh(schedule)
            return schedule
        except Exception as e:
            session.rollback()
            logger.opt(exception=e).error("Error creating schedule", error=e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error creating schedule",
            ) from e


@app.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["schedules"],
)
async def delete_schedule(
    role: Annotated[Role, Depends(authenticate_user)],
    schedule_id: identifiers.ScheduleID,
) -> None:
    """Delete a schedule from a workflow."""
    with Session(engine) as session:
        statement = select(Schedule).where(
            Schedule.owner_id == role.user_id, Schedule.id == schedule_id
        )
        result = session.exec(statement)
        schedule = result.one_or_none()
        if not schedule:
            logger.warning(
                "Schedule not found, attempt to delete underlying Temporal schedule...",
                schedule_id=schedule_id,
            )

        try:
            # Delete the schedule from Temporal first
            await schedules.delete_schedule(schedule_id)

            # If successful, delete the schedule from the database
            if schedule:
                session.delete(schedule)
                session.commit()
            else:
                logger.warning(
                    "Schedule was already deleted from the database",
                    schedule_id=schedule_id,
                )
        except Exception as e:
            logger.error("Error deleting schedule", error=e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error deleting schedule",
            ) from e


@app.get("/schedules/search", tags=["schedules"])
def search_schedules(
    role: Annotated[Role, Depends(authenticate_user)],
    params: SearchScheduleParams,
) -> list[Schedule]:
    """**[WORK IN PROGRESS]** Search for schedules."""
    with Session(engine) as session:
        statement = select(Schedule).where(Schedule.owner_id == role.user_id)
        results = session.exec(statement)
        schedules = results.all()
    return schedules


# ----- Actions ----- #


@app.get("/actions", tags=["actions"])
def list_actions(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
) -> list[ActionMetadataResponse]:
    """List all actions for a workflow."""
    with Session(engine) as session:
        statement = select(Action).where(
            Action.owner_id == role.user_id,
            Action.workflow_id == workflow_id,
        )
        results = session.exec(statement)
        actions = results.all()
    action_metadata = [
        ActionMetadataResponse(
            id=action.id,
            workflow_id=workflow_id,
            type=action.type,
            title=action.title,
            description=action.description,
            status=action.status,
            key=action.key,
        )
        for action in actions
    ]
    return action_metadata


@app.post("/actions", tags=["actions"])
def create_action(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CreateActionParams,
) -> ActionMetadataResponse:
    """Create a new action for a workflow."""
    with Session(engine) as session:
        action = Action(
            owner_id=role.user_id,
            workflow_id=params.workflow_id,
            type=params.type,
            title=params.title,
            description="",  # Default to empty string
        )
        # Check if a clashing action ref exists
        statement = select(Action).where(
            Action.owner_id == role.user_id,
            Action.workflow_id == action.workflow_id,
            Action.ref == action.ref,
        )
        if session.exec(statement).first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Action ref already exists in the workflow",
            )

        session.add(action)
        session.commit()
        session.refresh(action)

    action_metadata = ActionMetadataResponse(
        id=action.id,
        workflow_id=params.workflow_id,
        type=params.type,
        title=action.title,
        description=action.description,
        status=action.status,
        key=action.key,
    )
    return action_metadata


@app.get("/actions/{action_id}", tags=["actions"])
def get_action(
    role: Annotated[Role, Depends(authenticate_user)],
    action_id: str,
    workflow_id: str,
) -> ActionResponse:
    """Get an action."""
    with Session(engine) as session:
        statement = select(Action).where(
            Action.owner_id == role.user_id,
            Action.id == action_id,
            Action.workflow_id == workflow_id,
        )
        result = session.exec(statement)
        try:
            action = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

    return ActionResponse(
        id=action.id,
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        inputs=action.inputs,
        key=action.key,
    )


@app.post("/actions/{action_id}", tags=["actions"])
def update_action(
    role: Annotated[Role, Depends(authenticate_user)],
    action_id: str,
    params: UpdateActionParams,
) -> ActionResponse:
    """Update an action."""
    with Session(engine) as session:
        # Fetch the action by id
        statement = select(Action).where(
            Action.owner_id == role.user_id,
            Action.id == action_id,
        )
        result = session.exec(statement)
        try:
            action = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e

        if params.title is not None:
            action.title = params.title
        if params.description is not None:
            action.description = params.description
        if params.status is not None:
            action.status = params.status
        if params.inputs is not None:
            action.inputs = params.inputs

        session.add(action)
        session.commit()
        session.refresh(action)

    return ActionResponse(
        id=action.id,
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        inputs=action.inputs,
        key=action.key,
    )


@app.delete(
    "/actions/{action_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["actions"]
)
def delete_action(
    role: Annotated[Role, Depends(authenticate_user)],
    action_id: str,
) -> None:
    """Delete an action."""
    with Session(engine) as session:
        statement = select(Action).where(
            Action.owner_id == role.user_id,
            Action.id == action_id,
        )
        result = session.exec(statement)
        try:
            action = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        # If the user doesn't own this workflow, they can't delete the action
        session.delete(action)
        session.commit()


# ----- Case Management ----- #


@app.post(
    "/workflows/{workflow_id}/cases",
    status_code=status.HTTP_201_CREATED,
    tags=["cases"],
)
def create_case(
    role: Annotated[Role, Depends(authenticate_service)],  # M2M
    workflow_id: str,
    cases: list[CaseParams],
) -> CaseResponse:
    """Create a new case for a workflow."""
    with Session(engine) as session:
        case = Case(
            owner_id=role.user_id,
            workflow_id=workflow_id,
            **cases.model_dump(),
        )
        session.add(case)
        session.commit()
        session.refresh(case)

    return CaseResponse(
        id=case.id,
        owner_id=case.owner_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
        workflow_id=case.workflow_id,
        case_title=case.case_title,
        payload=case.payload,
        malice=case.malice,
        status=case.status,
        priority=case.priority,
        action=case.action,
        context=case.context,
        tags=case.tags,
    )


@app.get("/workflows/{workflow_id}/cases", tags=["cases"])
def list_cases(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    limit: int = 100,
) -> list[CaseResponse]:
    """List all cases for a workflow."""
    with Session(engine) as session:
        query = select(Case).where(
            Case.owner_id == role.user_id, Case.workflow_id == workflow_id
        )
        cases = session.exec(query).limit(limit).all()

    return [
        CaseResponse(
            id=case.id,
            owner_id=case.owner_id,
            created_at=case.created_at,
            updated_at=case.updated_at,
            workflow_id=case.workflow_id,
            case_title=case.case_title,
            payload=case.payload,
            malice=case.malice,
            status=case.status,
            priority=case.priority,
            action=case.action,
            context=case.context,
            tags=case.tags,
        )
        for case in cases
    ]


@app.get("/workflows/{workflow_id}/cases/{case_id}", tags=["cases"])
def get_case(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
) -> CaseResponse:
    """Get a specific case for a workflow."""
    with Session(engine) as session:
        query = select(Case).where(
            Case.owner_id == role.user_id,
            Case.workflow_id == workflow_id,
            Case.id == case_id,
        )
        case = session.exec(query).one_or_none()
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            )
    return CaseResponse(
        id=case.id,
        owner_id=case.owner_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
        workflow_id=case.workflow_id,
        case_title=case.case_title,
        payload=case.payload,
        malice=case.malice,
        status=case.status,
        priority=case.priority,
        action=case.action,
        context=case.context,
        tags=case.tags,
    )


@app.post("/workflows/{workflow_id}/cases/{case_id}", tags=["cases"])
def update_case(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
    params: CaseParams,
) -> CaseResponse:
    """Update a specific case for a workflow."""
    with Session(engine) as session:
        query = select(Case).where(
            Case.owner_id == role.user_id,
            Case.workflow_id == workflow_id,
            Case.id == case_id,
        )
        case = session.exec(query).one_or_none()
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            )

        for key, value in params.model_dump(exclude_unset=True).items():
            # Safety: params have been validated
            setattr(case, key, value)

        session.add(case)
        session.commit()
        session.refresh(case)
    return CaseResponse(
        id=case.id,
        owner_id=case.owner_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
        workflow_id=case.workflow_id,
        case_title=case.case_title,
        payload=case.payload,
        malice=case.malice,
        status=case.status,
        priority=case.priority,
        action=case.action,
        context=case.context,
        tags=case.tags,
    )


@app.post(
    "/workflows/{workflow_id}/cases/{case_id}/events",
    status_code=status.HTTP_201_CREATED,
    tags=["cases"],
)
def create_case_event(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
    params: CaseEventParams,
) -> None:
    """Create a new Case Event."""
    case_event = CaseEvent(
        owner_id=role.user_id,
        case_id=case_id,
        workflow_id=workflow_id,
        initiator_role=role.type,
        **params.model_dump(),
    )
    with Session(engine) as session:
        session.add(case_event)
        session.commit()
        session.refresh(case_event)
        return case_event


@app.get("/workflows/{workflow_id}/cases/{case_id}/events", tags=["cases"])
def list_case_events(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
) -> list[CaseEvent]:
    """List all Case Events."""
    with Session(engine) as session:
        query = select(CaseEvent).where(
            CaseEvent.owner_id == role.user_id,
            CaseEvent.workflow_id == workflow_id,
            CaseEvent.case_id == case_id,
        )
        case_events = session.exec(query).all()
        return case_events


@app.get("/workflows/{workflow_id}/cases/{case_id}/events/{event_id}", tags=["cases"])
def get_case_event(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
    event_id: str,
):
    """Get a specific case event."""
    with Session(engine) as session:
        query = select(CaseEvent).where(
            CaseEvent.owner_id == role.user_id,
            CaseEvent.workflow_id == workflow_id,
            CaseEvent.case_id == case_id,
            CaseEvent.id == event_id,
        )
        case_event = session.exec(query).one_or_none()
        if case_event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            )
        return case_event


# ----- Available Case Actions ----- #


@app.get("/case-actions", tags=["cases"])
def list_case_actions(
    role: Annotated[Role, Depends(authenticate_user)],
) -> list[CaseAction]:
    """List all case actions."""
    with Session(engine) as session:
        statement = select(CaseAction).where(
            or_(
                CaseAction.owner_id == "tracecat",
                CaseAction.owner_id == role.user_id,
            )
        )
        actions = session.exec(statement).all()
    return actions


@app.post("/case-actions", tags=["cases"])
def create_case_action(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CaseActionParams,
) -> CaseAction:
    """Create a new case action."""
    case_action = CaseAction(owner_id=role.user_id, **params.model_dump())
    with Session(engine) as session:
        session.add(case_action)
        session.commit()
        session.refresh(case_action)
    return case_action


@app.delete("/case-actions/{case_action_id}", tags=["cases"])
def delete_case_action(
    role: Annotated[Role, Depends(authenticate_user)],
    case_action_id: str,
):
    """Delete a case action."""
    with Session(engine) as session:
        statement = select(CaseAction).where(
            CaseAction.owner_id == role.user_id,
            CaseAction.id == case_action_id,
        )
        result = session.exec(statement)
        try:
            action = result.one()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        session.delete(action)
        session.commit()


# ----- Available Context Labels ----- #


@app.get("/case-contexts", tags=["cases"])
def list_case_contexts(
    role: Annotated[Role, Depends(authenticate_user)],
) -> list[CaseContext]:
    """List all case contexts."""
    with Session(engine) as session:
        statement = select(CaseContext).where(
            or_(
                CaseContext.owner_id == "tracecat",
                CaseContext.owner_id == role.user_id,
            )
        )
        actions = session.exec(statement).all()
    return actions


@app.post("/case-contexts", tags=["cases"])
def create_case_context(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CaseContextParams,
) -> CaseContext:
    """Create a new case context."""
    case_context = CaseContext(owner_id=role.user_id, **params.model_dump())
    with Session(engine) as session:
        session.add(case_context)
        session.commit()
        session.refresh(case_context)
    return params


@app.delete("/case-contexts/{case_context_id}", tags=["cases"])
def delete_case_context(
    role: Annotated[Role, Depends(authenticate_user)],
    case_context_id: str,
):
    """Delete a case context."""
    with Session(engine) as session:
        statement = select(CaseContext).where(
            CaseContext.owner_id == role.user_id,
            CaseContext.id == case_context_id,
        )
        result = session.exec(statement)
        try:
            action = result.one()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            ) from e
        session.delete(action)
        session.delete(action)
        session.commit()
    pass


# ----- Users ----- #


@app.post("/users", status_code=status.HTTP_201_CREATED, tags=["users"])
def create_user(
    role: Annotated[Role, Depends(authenticate_user)],
) -> User:
    """Create new user."""

    with Session(engine) as session:
        # Check if user exists
        statement = select(User).where(User.id == role.user_id).limit(1)
        result = session.exec(statement)

        user = result.one_or_none()
        if user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="User already exists"
            )
        user = User(owner_id="tracecat", id=role.user_id)

        session.add(user)
        session.commit()
        session.refresh(user)
        return user


@app.get("/users", tags=["users"])
def get_user(
    role: Annotated[Role, Depends(authenticate_user)],
) -> User:
    """Get a user."""

    with Session(engine) as session:
        # Get user given user_id
        statement = select(User).where(User.id == role.user_id)
        result = session.exec(statement)
        try:
            user = result.one()
            return user
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            ) from e


@app.post("/users", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def update_user(
    role: Annotated[Role, Depends(authenticate_user)],
    params: UpdateUserParams,
) -> None:
    """Update a user."""

    with Session(engine) as session:
        statement = select(User).where(User.id == role.user_id)
        result = session.exec(statement)
        try:
            user = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            ) from e

        if params.tier is not None:
            user.tier = params.tier
        if params.settings is not None:
            user.settings = params.settings

        session.add(user)
        session.commit()


@app.delete("/users", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def delete_user(
    role: Annotated[Role, Depends(authenticate_user)],
) -> None:
    """Delete a user."""

    with Session(engine) as session:
        statement = select(User).where(User.id == role.user_id)
        result = session.exec(statement)
        try:
            user = result.one()
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            ) from e
        session.delete(user)
        session.commit()


# ----- Secrets ----- #


@app.get("/secrets", tags=["secrets"])
def list_secrets(
    role: Annotated[Role, Depends(authenticate_user)],
    session: Session = Depends(get_session),
) -> list[SecretResponse]:
    """List user secrets."""
    service = SecretsService(session, role)
    secrets = service.list_secrets()
    return [
        SecretResponse(
            id=secret.id,
            type=secret.type,
            name=secret.name,
            description=secret.description,
            keys=[kv.key for kv in service.decrypt_keys(secret.encrypted_keys)],
        )
        for secret in secrets
    ]


@app.get("/secrets/{secret_name}", tags=["secrets"])
def get_secret(
    # NOTE(auth): Worker service can also access secrets
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    secret_name: str,
) -> Secret:
    """Get a secret."""

    with Session(engine) as session:
        # Check if secret exists
        statement = (
            select(Secret)
            .where(Secret.owner_id == role.user_id, Secret.name == secret_name)
            .limit(1)
        )
        result = session.exec(statement)
        secret = result.one_or_none()
        if secret is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
            )
        # NOTE: IMPLICIT TYPE COERCION
        # Encrypted keys as bytes gets cast a string as to be JSON serializable
        return secret


@app.post("/secrets", status_code=status.HTTP_201_CREATED, tags=["secrets"])
def create_secret(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CreateSecretParams,
    session: Session = Depends(get_session),
) -> None:
    """Create a secret."""
    service = SecretsService(session, role)
    secret = service.get_secret_by_name(params.name)
    if secret is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        )

    new_secret = Secret(
        owner_id=role.user_id,
        name=params.name,
        type=params.type,
        description=params.description,
        tags=params.tags,
        encrypted_keys=service.encrypt_keys(params.keys),
    )
    service.create_secret(new_secret)


@app.post(
    "/secrets/{secret_name}", status_code=status.HTTP_201_CREATED, tags=["secrets"]
)
def update_secret(
    role: Annotated[Role, Depends(authenticate_user)],
    secret_name: str,
    params: UpdateSecretParams,
    session: Session = Depends(get_session),
) -> Secret:
    """Update a secret"""
    service = SecretsService(session, role)
    secret = service.get_secret_by_name(secret_name)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        )
    maybe_clashing_secret = service.get_secret_by_name(params.name)
    if maybe_clashing_secret is not None and maybe_clashing_secret.id != secret.id:
        name = maybe_clashing_secret.name
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Secret with name {name} already exists",
        )
    return service.update_secret(secret, params)


@app.delete(
    "/secrets/{secret_name}", status_code=status.HTTP_204_NO_CONTENT, tags=["secrets"]
)
def delete_secret(
    role: Annotated[Role, Depends(authenticate_user)],
    secret_name: str,
    session: Session = Depends(get_session),
) -> None:
    """Delete a secret."""
    service = SecretsService(session, role)
    secret = service.get_secret_by_name(secret_name)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        )
    service.delete_secret(secret)


@app.post("/secrets/search", tags=["secrets"])
def search_secrets(
    role: Annotated[Role, Depends(authenticate_user)],
    params: SearchSecretsParams,
) -> list[Secret]:
    """**[WORK IN PROGRESS]**   Get a secret by ID."""
    with Session(engine) as session:
        statement = (
            select(Secret)
            .where(Secret.owner_id == role.user_id)
            .filter(*[Secret.name == name for name in params.names])
        )
        result = session.exec(statement)
        secrets = result.all()
        return secrets


# ----- UDFs ----- #


@app.get("/udfs", tags=["udfs"])
def list_udfs(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    limit: int | None = None,
    ns: list[str] | None = Query(None),
) -> list[UDFSpec]:
    """List all user-defined function specifications for a user."""
    with Session(engine) as session:
        statement = select(UDFSpec).where(
            or_(
                UDFSpec.owner_id == "tracecat",
                UDFSpec.owner_id == role.user_id,
            )
        )
        if ns:
            ns_conds = [UDFSpec.key.startswith(n) for n in ns]
            statement = statement.where(or_(*ns_conds))
        if limit:
            statement = statement.limit(limit)
        result = session.exec(statement)
        udfs = result.all()
        return udfs


@app.get("/udfs/{udf_key}", tags=["udfs"])
def get_udf(
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    udf_key: str,
    namespace: str = Query(None),
) -> UDFSpec:
    """Get a user-defined function specification."""
    with Session(engine) as session:
        statement = select(UDFSpec).where(
            or_(
                UDFSpec.owner_id == "tracecat",
                UDFSpec.owner_id == role.user_id,
            ),
            UDFSpec.key == udf_key,
        )
        if namespace:
            statement = statement.where(UDFSpec.namespace == namespace)
        result = session.exec(statement)
        udf = result.one_or_none()
        if udf is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="udf not found"
            )
        return udf


@app.post("/udfs/{udf_key}", tags=["udfs"])
def create_udf(
    role: Annotated[Role, Depends(authenticate_user)],
    udf_key: str,
) -> UDFSpec:
    """Create a user-defined function specification."""
    _, platform, name = udf_key.split(".")
    with Session(engine) as session:
        statement = select(UDFSpec).where(
            UDFSpec.owner_id == role.user_id,
            UDFSpec.platform == platform,
            UDFSpec.name == name,
        )
        result = session.exec(statement)
        udf = result.one_or_none()
        if udf is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="udf not found"
            )
        return udf


@app.post("/udfs/{udf_key}/validate", tags=["udfs"])
def validate_udf_args(
    role: Annotated[Role, Depends(authenticate_user)],
    udf_key: str,
    args: dict[str, Any],
) -> UDFArgsValidationResponse:
    """Validate user-defined function's arguments."""
    try:
        result = validation.vadliate_udf_args(udf_key, args)
        if result.status == "error":
            logger.error(
                "Error validating UDF args",
                message=result.msg,
                details=result.detail,
            )
        return UDFArgsValidationResponse.from_validation_result(result)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"UDF {udf_key!r} not found"
        ) from e
    except Exception as e:
        logger.opt(exception=e).error("Error validating UDF args")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unexpected error validating UDF args",
        ) from e


@app.post("/validate-workflow")
async def validate_workflow(
    role: Annotated[Role, Depends(authenticate_user)],
    definition: UploadFile = File(...),
    payload: UploadFile = File(None),
    session: Session = Depends(get_session),
) -> list[UDFArgsValidationResponse]:
    """Validate a workflow.

    This deploys the workflow and updates its version. If a YAML file is provided, it will override the workflow in the database."""

    # Committing from YAML (i.e. attaching yaml) will override the workflow definition in the database
    with logger.contextualize(role=role):
        # Perform Tiered Validation
        # Tier 1: DSLInput validation
        # Verify that the workflow DSL is structurally sound
        construction_errors = []
        try:
            # Uploaded YAML file overrides the workflow in the database
            dsl = DSLInput.from_yaml(definition.file)
        except* TracecatValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                UDFArgsValidationResponse.from_dsl_validation_error(e).model_dump(
                    exclude_none=True
                )
                for e in eg.exceptions
            )
        except* ValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                UDFArgsValidationResponse.from_pydantic_validation_error(e).model_dump(
                    exclude_none=True
                )
                for e in eg.exceptions
            )

        if construction_errors:
            msg = f"Workflow definition construction failed with {len(construction_errors)} errors"
            logger.error(msg)
            return ORJSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "status": "failure",
                    "message": msg,
                    "errors": construction_errors,
                    "metadata": {"filename": definition.filename}
                    if definition
                    else None,
                },
            )

        # When we're here, we've verified that the workflow DSL is structurally sound
        # Now, we have to ensure that the arguments are sound

        if expr_errors := await validation.validate_dsl(session=session, dsl=dsl):
            msg = f"{len(expr_errors)} validation error(s)"
            logger.error(msg)
            return ORJSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "status": "failure",
                    "message": msg,
                    "errors": [
                        UDFArgsValidationResponse.from_validation_result(
                            val_res
                        ).model_dump(exclude_none=True)
                        for val_res in expr_errors
                    ],
                    "metadata": {"filename": definition.filename}
                    if definition
                    else None,
                },
            )

        # Check for input errors
        if payload:
            payload_data = orjson.loads(payload.file.read())
            payload_val_res = validate_trigger_inputs(dsl, payload_data)
            if payload_val_res.status == "error":
                msg = "Trigger input validation error"
                logger.error(msg)
                return ORJSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "status": "failure",
                        "message": msg,
                        "errors": [
                            UDFArgsValidationResponse.from_validation_result(
                                payload_val_res
                            ).model_dump(exclude_none=True)
                        ],
                        "metadata": {"filename": definition.filename},
                    },
                )
        return ORJSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "success", "message": "Workflow passed validation"},
        )
