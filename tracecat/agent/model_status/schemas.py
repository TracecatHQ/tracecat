"""API and stream contracts for model status and model errors."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.model_status.types import ModelErrorCode, ModelStatusState
from tracecat.runtime.errors import RuntimeErrorKind


class ModelErrorDetail(BaseModel):
    """Structured detail for a failed model request.

    Carried on the chat stream's error entry (``{"kind": "error", "error":
    <message>, "detail": <this>}``) by any backend, and returned by the model
    status check. Never holds secrets, provider messages, or request payloads.
    """

    model_config = ConfigDict(frozen=True)

    code: ModelErrorCode
    kind: RuntimeErrorKind | None = Field(
        default=None,
        description="The runtime classification, when the backend has one.",
    )
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)
    status_code: int | None = Field(default=None, ge=100, le=599)
    request_id: str | None = Field(
        default=None,
        max_length=128,
        description="The provider's request ID, for support.",
    )
    provider_error_code: str | None = Field(
        default=None,
        max_length=128,
        description="The provider's machine-readable error code or type.",
    )


class ModelStatusCheck(BaseModel):
    """Which model to check, by catalog ID or by provider and name."""

    catalog_id: uuid.UUID | None = Field(default=None)
    model_provider: str | None = Field(default=None, max_length=128)
    model_name: str | None = Field(default=None, max_length=500)
    preset: bool = Field(
        default=False,
        description="The chat runs the model through an agent preset.",
    )
    refresh: bool = Field(
        default=False,
        description="Re-check now unless the last check is very recent.",
    )


class ModelStatusRead(BaseModel):
    """Whether the selected model can currently be reached."""

    state: ModelStatusState
    provider: str
    model: str
    problem: ModelErrorDetail | None = None
    checked_at: datetime
