"""HTTP shapes for agent approval decisions."""

from typing import Any, Literal

from pydantic import BaseModel


class ApproverNotAllowedErrorBody(BaseModel):
    code: Literal["approver_not_allowed"] = "approver_not_allowed"
    message: str


class ApproverNotAllowedResponse(BaseModel):
    """HTTP 403 body when the agent's approver policy refuses the decider."""

    detail: str
    error: ApproverNotAllowedErrorBody


APPROVER_NOT_ALLOWED_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ApproverNotAllowedResponse,
        "description": "The agent's approver policy refuses the decider",
    }
}
