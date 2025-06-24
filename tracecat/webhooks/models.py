from typing import Any, Literal

from pydantic import BaseModel, Field

from tracecat.db.schemas import Resource
from tracecat.identifiers.workflow import WorkflowID

# API Models

type WebhookStatus = Literal["online", "offline"]
type WebhookMethod = Literal["GET", "POST"]
NDJSON_CONTENT_TYPES = (
    "application/x-ndjson",
    "application/jsonlines",
    "application/jsonl",
)


class WebhookRead(Resource):
    id: str
    secret: str
    status: WebhookStatus
    entrypoint_ref: str | None = None
    filters: dict[str, Any]
    methods: list[WebhookMethod] = Field(
        default_factory=list, description="Methods to allow"
    )
    workflow_id: WorkflowID
    url: str


class WebhookCreate(BaseModel):
    status: WebhookStatus = "offline"
    methods: list[WebhookMethod] = Field(
        default_factory=lambda: ["POST"], description="Methods to allow"
    )
    entrypoint_ref: str | None = None


class WebhookUpdate(BaseModel):
    status: WebhookStatus | None = None
    methods: list[WebhookMethod] | None = None
    entrypoint_ref: str | None = None
