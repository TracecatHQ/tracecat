from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address, ip_network
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from tracecat.core.schemas import Schema
from tracecat.identifiers import OwnerID, WorkflowID

# API Models

type WebhookStatus = Literal["online", "offline"]
type WebhookMethod = Literal["GET", "POST"]
NDJSON_CONTENT_TYPES = (
    "application/x-ndjson",
    "application/jsonlines",
    "application/jsonl",
)


class WebhookRead(Schema):
    id: str | None = None
    owner_id: OwnerID
    secret: str | None = None
    status: WebhookStatus | None = None
    entrypoint_ref: str | None = None
    allowlisted_cidrs: list[str] | None = Field(default=None)
    filters: dict[str, Any] | None = Field(default=None)
    methods: list[WebhookMethod] | None = Field(
        default=None, description="Methods to allow"
    )
    workflow_id: WorkflowID
    url: str | None = None
    api_key: WebhookApiKeyRead | None = None


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


class WebhookApiKeyRead(BaseModel):
    preview: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool = False

    @model_validator(mode="after")
    def compute_is_active(self) -> Self:
        self.is_active = self.revoked_at is None
        return self


class WebhookApiKeyGenerateResponse(BaseModel):
    api_key: str
    preview: str
    created_at: datetime


def _normalize_cidrs(cidrs: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for cidr in cidrs:
        network = None
        try:
            network = ip_network(cidr, strict=False)
        except ValueError:
            try:
                address = ip_address(cidr)
                if address.version != 4:
                    raise ValueError(f"Only IPv4 addresses are supported: {cidr}")
            except ValueError as second_error:
                raise ValueError(
                    f"Invalid IP allowlist entry: {cidr}"
                ) from second_error
            network = ip_network(f"{address}/{address.max_prefixlen}", strict=False)
        if network.version != 4:
            raise ValueError(f"Only IPv4 CIDR ranges are supported: {cidr}")
        stringified = str(network)
        if stringified not in seen:
            seen.add(stringified)
            normalized.append(stringified)
    return normalized
