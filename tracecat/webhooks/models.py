from datetime import datetime
from ipaddress import ip_network
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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
    allowlisted_cidrs: list[str] = Field(default_factory=list)
    filters: dict[str, Any]
    methods: list[WebhookMethod] = Field(
        default_factory=list, description="Methods to allow"
    )
    workflow_id: WorkflowID
    url: str
    api_key_suffix: str | None = None
    api_key_created_at: datetime | None = None
    api_key_last_used_at: datetime | None = None
    api_key_revoked_at: datetime | None = None
    has_active_api_key: bool = False


class WebhookCreate(BaseModel):
    status: WebhookStatus = "offline"
    methods: list[WebhookMethod] = Field(
        default_factory=lambda: ["POST"], description="Methods to allow"
    )
    entrypoint_ref: str | None = None
    allowlisted_cidrs: list[str] = Field(default_factory=list)

    @field_validator("allowlisted_cidrs")
    @classmethod
    def validate_allowlisted_cidrs(cls, value: list[str]) -> list[str]:
        return _normalize_cidrs(value)


class WebhookUpdate(BaseModel):
    status: WebhookStatus | None = None
    methods: list[WebhookMethod] | None = None
    entrypoint_ref: str | None = None
    allowlisted_cidrs: list[str] | None = None

    @field_validator("allowlisted_cidrs")
    @classmethod
    def validate_cidrs(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_cidrs(value)


class WebhookApiKeyResponse(BaseModel):
    api_key: str
    suffix: str
    created_at: datetime


def _normalize_cidrs(cidrs: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for cidr in cidrs:
        network = ip_network(cidr, strict=False)
        stringified = str(network)
        if stringified not in seen:
            seen.add(stringified)
            normalized.append(stringified)
    return normalized
