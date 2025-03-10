from typing import Any, Literal

from pydantic import BaseModel

from tracecat.db.schemas import Resource
from tracecat.identifiers.workflow import WorkflowID

# API Models

type WebhookStatus = Literal["online", "offline"]
type WebhookMethod = Literal["GET", "POST"]


class WebhookRead(Resource):
    id: str
    secret: str
    status: WebhookStatus
    entrypoint_ref: str | None = None
    filters: dict[str, Any]
    method: WebhookMethod
    workflow_id: WorkflowID
    url: str


class WebhookCreate(BaseModel):
    status: WebhookStatus = "offline"
    method: WebhookMethod = "POST"
    entrypoint_ref: str | None = None


class WebhookUpdate(BaseModel):
    status: WebhookStatus | None = None
    method: WebhookMethod | None = None
    entrypoint_ref: str | None = None
