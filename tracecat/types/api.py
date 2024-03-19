from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from tracecat.types.actions import ActionType

# TODO: Consistent API design
# Action and Workflow create / update params
# should be the same as the metadata responses


class ActionResponse(BaseModel):
    id: str
    type: ActionType
    title: str
    description: str
    status: str
    inputs: dict[str, Any] | None
    key: str  # Computed field


class WorkflowResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    actions: dict[str, ActionResponse]
    object: dict[str, Any] | None  # React Flow object
    owner_id: str


class ActionMetadataResponse(BaseModel):
    id: str
    workflow_id: str
    type: ActionType
    title: str
    description: str
    status: str
    key: str


class WorkflowMetadataResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    status: str


class WorkflowRunMetadataResponse(BaseModel):
    id: str
    workflow_id: str
    status: str


class CreateWorkflowParams(BaseModel):
    title: str
    description: str


class UpdateWorkflowParams(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    object: str | None = None


class UpdateWorkflowRunParams(BaseModel):
    status: str | None = None


class CreateActionParams(BaseModel):
    workflow_id: str
    type: str
    title: str


class UpdateActionParams(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    inputs: str | None = None


class CreateWebhookParams(BaseModel):
    action_id: str
    workflow_id: str


class WebhookMetadataResponse(BaseModel):
    id: str
    action_id: str
    workflow_id: str
    secret: str


class WebhookResponse(BaseModel):
    id: str
    secret: str
    action_id: str
    workflow_id: str


class GetWebhookParams(BaseModel):
    webhook_id: str | None = None
    path: str | None = None


class AuthenticateWebhookResponse(BaseModel):
    status: Literal["Authorized", "Unauthorized"]
    action_key: str | None = None
    action_id: str | None = None
    webhook_id: str | None = None
    workflow_id: str | None = None


class Event(BaseModel):
    published_at: datetime
    action_id: str
    action_run_id: str
    action_title: str
    action_type: str
    workflow_id: str
    workflow_title: str
    workflow_run_id: str
    data: dict[str, Any]


class EventSearchParams(BaseModel):
    workflow_id: str
    limit: int = 1000
    order_by: str = "pubished_at"
    workflow_run_id: str | None = None
    query: str | None = None
    group_by: list[str] | None = None
    agg: str | None = None


class CreateUserParams(BaseModel):
    tier: Literal["free", "pro", "enterprise"] = "free"  # "free" or "premium"
    settings: str | None = None  # JSON-serialized String of settings


UpdateUserParams = CreateUserParams


class CreateSecretParams(BaseModel):
    name: str
    value: str


UpdateSecretParams = CreateSecretParams


class SearchSecretsParams(BaseModel):
    names: list[str]


class CaseParams(BaseModel):
    title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    context: dict[str, str] | str | None = None
    action: str | None = None
    suppression: dict[str, bool] | None = None


class CaseActionParams(BaseModel):
    tag: str
    value: str
    user_id: str | None = None


class CaseContextParams(BaseModel):
    tag: str
    value: str
    user_id: str | None = None


class SearchWebhooksParams(BaseModel):
    action_id: str | None = None
    workflow_id: str | None = None
    limit: int = 100
    order_by: str = "created_at"
    query: str | None = None
    group_by: list[str] | None = None
    agg: str | None = None
