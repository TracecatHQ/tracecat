import uuid
from itertools import batched
from typing import Annotated, Any, Literal, TypedDict

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from temporalio.service import RPCError

from tracecat import config
from tracecat.concurrency import cooperative
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.ee.interactions.enums import InteractionCategory
from tracecat.ee.interactions.schemas import InteractionInput
from tracecat.identifiers.workflow import AnyWorkflowIDPath, generate_exec_id
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage import blob
from tracecat.storage.collection import get_collection_page
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    InlineObject,
    StoredObject,
    StoredObjectValidator,
)
from tracecat.storage.utils import (
    cached_blob_download,
    compute_sha256,
    deserialize_object,
    serialize_object,
)
from tracecat.webhooks.dependencies import (
    DraftWorkflowDep,
    PayloadDep,
    ValidWorkflowDefinitionDep,
    parse_content_type,
    parse_interaction_payload,
    validate_incoming_webhook,
)
from tracecat.webhooks.schemas import NDJSON_CONTENT_TYPES
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.schemas import (
    ReceiveInteractionResponse,
    WorkflowExecutionCreateResponse,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(
    prefix="/webhooks/{workflow_id}/{secret}",
    tags=["public"],
    dependencies=[Depends(validate_incoming_webhook)],
)


class OktaVerificationResponse(TypedDict):
    verification: str


class WebhookStoredObjectInlineResponse(TypedDict):
    kind: Literal["value"]
    value: Any


class WebhookStoredObjectDownloadResponse(TypedDict):
    kind: Literal["download_file", "download_export"]
    download_url: str
    expires_in_seconds: int
    content_type: str
    size_bytes: int


type WaitResultOutput = (
    WebhookStoredObjectInlineResponse | WebhookStoredObjectDownloadResponse | Any
)


type WebhookResponse = (
    WorkflowExecutionCreateResponse | OktaVerificationResponse | Response
)


async def _to_external_download_response(
    external: ExternalObject,
) -> WebhookStoredObjectDownloadResponse:
    ref = external.ref
    expiry = config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY
    download_url = await blob.generate_presigned_download_url(
        key=ref.key,
        bucket=ref.bucket,
        expiry=expiry,
        force_download=True,
        override_content_type="application/octet-stream",
    )
    return WebhookStoredObjectDownloadResponse(
        kind="download_file",
        download_url=download_url,
        expires_in_seconds=expiry,
        content_type=ref.content_type,
        size_bytes=ref.size_bytes,
    )


async def _to_collection_download_response(
    collection: CollectionObject,
) -> WebhookStoredObjectDownloadResponse:
    materialized = await _materialize_collection_values_for_wait(collection)
    serialized = serialize_object(materialized)
    prefix = collection.manifest_ref.key.removesuffix("/manifest.json")
    export_key = f"{prefix}/downloads/{uuid.uuid4().hex}.json"

    await blob.upload_file(
        content=serialized,
        key=export_key,
        bucket=collection.manifest_ref.bucket,
        content_type="application/json",
    )

    expiry = config.TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY
    download_url = await blob.generate_presigned_download_url(
        key=export_key,
        bucket=collection.manifest_ref.bucket,
        expiry=expiry,
        force_download=True,
        override_content_type="application/json",
    )

    return WebhookStoredObjectDownloadResponse(
        kind="download_export",
        download_url=download_url,
        expires_in_seconds=expiry,
        content_type="application/json",
        size_bytes=len(serialized),
    )


async def _retrieve_external_value(external: ExternalObject) -> Any:
    ref = external.ref
    content = await cached_blob_download(
        sha256=ref.sha256,
        bucket=ref.bucket,
        key=ref.key,
    )

    actual_sha256 = compute_sha256(content)
    if actual_sha256 != ref.sha256:
        raise ValueError(
            f"Integrity check failed for {ref.key}: "
            f"expected {ref.sha256}, got {actual_sha256}"
        )
    return deserialize_object(content)


async def _resolve_stored_object_value(stored: StoredObject) -> Any:
    match stored:
        case InlineObject(data=data):
            return data
        case ExternalObject() as external:
            return await _retrieve_external_value(external)
        case CollectionObject() as collection:
            return await _materialize_collection_values_for_wait(collection)
        case _:
            raise TypeError(f"Expected StoredObject, got {type(stored).__name__}")


async def _materialize_collection_values_for_wait(
    collection: CollectionObject,
) -> Any:
    if collection.index is not None:
        index = collection.index
        if index < 0:
            index += collection.count
        if index < 0 or index >= collection.count:
            raise IndexError(
                f"Collection index {index} out of range [0, {collection.count})"
            )

        items = await get_collection_page(collection, offset=index, limit=1)
        if not items:
            raise IndexError(
                f"Collection index {index} out of range [0, {collection.count})"
            )
        item = items[0]
        if collection.element_kind == "value":
            return item
        stored = StoredObjectValidator.validate_python(item)
        return await _resolve_stored_object_value(stored)

    items = await get_collection_page(collection)
    if collection.element_kind == "value":
        return items

    values: list[Any] = []
    for item in items:
        stored = StoredObjectValidator.validate_python(item)
        values.append(await _resolve_stored_object_value(stored))
    return values


async def _normalize_wait_result(
    value: StoredObject, *, unwrap: bool = False
) -> WaitResultOutput:
    """Normalize /wait response values for StoredObject variants.

    - InlineObject: returns wrapped value envelope by default
      or the inline value directly when `unwrap=True`
    - ExternalObject: returns download envelope
    - CollectionObject: materializes and returns download envelope
    """
    match value:
        case InlineObject(data=data):
            if unwrap:
                return data
            return WebhookStoredObjectInlineResponse(
                kind="value",
                value=data,
            )
        case ExternalObject() as external:
            return await _to_external_download_response(external)
        case CollectionObject() as collection:
            return await _to_collection_download_response(collection)
        case _:
            raise ValueError(f"Expected StoredObject, got {type(value).__name__}")


# NOTE: Need to set response_model to None to avoid FastAPI trying to parse the response as JSON
# We need empty status 200 responses for slash command APIs (e.g. Slack)
# POST is the primary method for webhook triggering
@router.post("", response_model=None)
async def incoming_webhook_post(
    *,
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
    echo: bool = Query(default=False, description="Echo back to the caller"),
    empty_echo: bool = Query(
        default=False,
        description="Return an empty response. Assumes `echo` to be `True`.",
    ),
    wait: bool = Query(
        default=False,
        description="Wait for workflow completion and return the workflow result.",
    ),
    unwrap: bool = Query(
        default=False,
        description=("When waiting for completion, return inline outputs directly."),
    ),
    vendor: str | None = Query(
        default=None,
        description="Vendor specific webhook verification. Supported vendors: `okta`.",
    ),
    request: Request,
    content_type: Annotated[str | None, Header(alias="content-type")] = None,
) -> WebhookResponse:
    """Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    return await _incoming_webhook(
        workflow_id=workflow_id,
        defn=defn,
        payload=payload,
        echo=echo,
        empty_echo=empty_echo,
        wait=wait,
        unwrap=unwrap,
        vendor=vendor,
        request=request,
        content_type=content_type,
    )


# GET is secondary, mainly for webhook verification challenges (e.g., Okta)
@router.get("", response_model=None)
async def incoming_webhook_get(
    *,
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
    echo: bool = Query(default=False, description="Echo back to the caller"),
    empty_echo: bool = Query(
        default=False,
        description="Return an empty response. Assumes `echo` to be `True`.",
    ),
    wait: bool = Query(
        default=False,
        description="Wait for workflow completion and return the workflow result.",
    ),
    unwrap: bool = Query(
        default=False,
        description=("When waiting for completion, return inline outputs directly."),
    ),
    vendor: str | None = Query(
        default=None,
        description="Vendor specific webhook verification. Supported vendors: `okta`.",
    ),
    request: Request,
    content_type: Annotated[str | None, Header(alias="content-type")] = None,
) -> WebhookResponse:
    """Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    return await _incoming_webhook(
        workflow_id=workflow_id,
        defn=defn,
        payload=payload,
        echo=echo,
        empty_echo=empty_echo,
        wait=wait,
        unwrap=unwrap,
        vendor=vendor,
        request=request,
        content_type=content_type,
    )


async def _incoming_webhook(
    *,
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
    echo: bool,
    empty_echo: bool,
    wait: bool,
    unwrap: bool,
    vendor: str | None,
    request: Request,
    content_type: str | None,
) -> WebhookResponse:
    logger.info("Webhook hit", path=workflow_id, role=ctx_role.get())
    logger.trace("Webhook payload", payload=payload)

    dsl_input = DSLInput(**defn.content)

    service = await WorkflowExecutionsService.connect()
    # If this was a ndjson, automatically batch the requests
    # This is a workaround for the fact that Temporal doesn't support batching
    # of webhook requests
    mime_type = parse_content_type(content_type)[0] if content_type else ""
    if wait:
        if mime_type in NDJSON_CONTENT_TYPES and isinstance(payload, list):
            raise HTTPException(
                status_code=400,
                detail="`wait=true` is not supported with NDJSON payloads.",
            )
        response = await service.create_workflow_execution(
            dsl=dsl_input,
            wf_id=workflow_id,
            payload=payload,
            trigger_type=TriggerType.WEBHOOK,
            registry_lock=RegistryLock.model_validate(defn.registry_lock)
            if defn.registry_lock
            else None,
        )
    elif mime_type in NDJSON_CONTENT_TYPES and isinstance(payload, list):
        one_response = None
        # Slow release to avoid overwhelming the system
        async for p in cooperative(batched(payload, 8), delay=2):
            one_response = await service.create_workflow_execution_wait_for_start(
                dsl=dsl_input,
                wf_id=workflow_id,
                payload=p,
                trigger_type=TriggerType.WEBHOOK,
                registry_lock=RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None,
            )
        # Currently just return the last response's wf_exec_id
        response = WorkflowExecutionCreateResponse(
            message="Workflow execution created",
            wf_id=workflow_id,
            wf_exec_id=one_response["wf_exec_id"]
            if one_response
            else generate_exec_id(workflow_id),  # This should never happen
        )

    else:
        response = await service.create_workflow_execution_wait_for_start(
            dsl=dsl_input,
            wf_id=workflow_id,
            payload=payload,
            trigger_type=TriggerType.WEBHOOK,
            registry_lock=RegistryLock.model_validate(defn.registry_lock)
            if defn.registry_lock
            else None,
        )

    # Response handling
    if wait:
        response = await _normalize_wait_result(response["result"], unwrap=unwrap)

    if echo:
        if empty_echo:
            return Response(status_code=200)
        if not isinstance(response, dict):
            response = {"result": response}
        try:
            response["payload"] = await request.json()
        except Exception as e:
            logger.warning(
                "Failed to decode request payload body during echo", error=str(e)
            )
            response["payload"] = None

    if vendor is not None:
        if vendor == "okta":
            # https://developer.okta.com/docs/concepts/event-hooks/#one-time-verification-request
            challenge = request.headers.get("x-okta-verification-challenge")
            if challenge:
                return OktaVerificationResponse(verification=challenge)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported vendor: {vendor}. Expected: `okta`.",
            )

    return response


@router.post("/wait")
async def incoming_webhook_wait(
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
    unwrap: bool = Query(
        default=False,
        description=("Return inline outputs directly."),
    ),
) -> WaitResultOutput:
    """Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    logger.info("Webhook hit", path=workflow_id, role=ctx_role.get())
    logger.trace("Webhook payload", payload=payload)

    dsl_input = DSLInput(**defn.content)

    service = await WorkflowExecutionsService.connect()
    response = await service.create_workflow_execution(
        dsl=dsl_input,
        wf_id=workflow_id,
        payload=payload,
        trigger_type=TriggerType.WEBHOOK,
        registry_lock=RegistryLock.model_validate(defn.registry_lock)
        if defn.registry_lock
        else None,
    )

    return await _normalize_wait_result(response["result"], unwrap=unwrap)


@router.post("/draft", response_model=None)
async def incoming_webhook_draft(
    workflow_id: AnyWorkflowIDPath,
    draft_ctx: DraftWorkflowDep,
    payload: PayloadDep,
) -> WorkflowExecutionCreateResponse:
    """Draft webhook endpoint to trigger a workflow execution using the draft workflow graph.

    This endpoint runs the current (uncommitted) workflow graph rather than the committed definition.
    Child workflows using aliases will resolve to the latest draft aliases, not committed aliases.
    """
    logger.info("Draft webhook hit", path=workflow_id, role=ctx_role.get())
    logger.trace("Draft webhook payload", payload=payload)

    service = await WorkflowExecutionsService.connect()
    response = await service.create_draft_workflow_execution_wait_for_start(
        dsl=draft_ctx.dsl,
        wf_id=workflow_id,
        payload=payload,
        trigger_type=TriggerType.WEBHOOK,
        registry_lock=RegistryLock.model_validate(draft_ctx.registry_lock)
        if draft_ctx.registry_lock
        else None,
    )
    return response


@router.post("/interactions/{category}")
async def receive_interaction(
    workflow_id: AnyWorkflowIDPath,
    input: Annotated[InteractionInput, Depends(parse_interaction_payload)],
    category: InteractionCategory,
) -> ReceiveInteractionResponse:
    """Process incoming workflow interactions from external services.

    Args:
        workflow_id: ID of the workflow to interact with
        payload: Raw interaction payload from the external service
        category: Category of interaction (e.g., slack)

    Returns:
        Response confirming the interaction was processed

    Raises:
        HTTPException: If payload is invalid or workflow interaction fails
    """
    logger.info(
        "Received interaction",
        workflow_id=workflow_id,
        category=category,
        role=ctx_role.get(),
    )
    logger.trace("Interaction input", input=input)

    try:
        # Get temporal client and workflow handle
        client = await get_temporal_client()
        handle = client.get_workflow_handle_for(DSLWorkflow.run, input.execution_id)

        # Convert to internal interaction format
        # Execute workflow interaction handler
        result = await handle.execute_update(DSLWorkflow.interaction_handler, input)
        logger.info("Interaction processed", result=result)

        return ReceiveInteractionResponse(
            message="Interaction processed successfully",
        )

    except RPCError as e:
        if "workflow not found" in str(e).lower():
            logger.error("Workflow not found", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
            ) from e
        raise
    except Exception as e:
        logger.error("Failed to process interaction", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process interaction: {str(e)}",
        ) from e
