"""API and stream contracts for model status and model errors."""

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
        description="Provider or Tracecat request ID, for support.",
    )
    provider_error_code: str | None = Field(
        default=None,
        max_length=128,
        description="The provider's machine-readable error code or type.",
    )


class ModelStatusRead(BaseModel):
    """Whether the selected model can currently be reached."""

    state: ModelStatusState
    provider: str
    model: str
    problem: ModelErrorDetail | None = None
    checked_at: datetime
