from typing import Any, Literal

from pydantic import BaseModel, JsonValue

from tracecat.db.schemas import Resource

TriggerInputs = JsonValue


class WebhookResponse(Resource):
    id: str
    secret: str
    status: Literal["online", "offline"]
    entrypoint_ref: str | None = None
    filters: dict[str, Any]
    method: Literal["GET", "POST"]
    workflow_id: str
    url: str


class UpsertWebhookParams(BaseModel):
    status: Literal["online", "offline"] | None = None
    entrypoint_ref: str | None = None
    method: Literal["GET", "POST"] | None = None
