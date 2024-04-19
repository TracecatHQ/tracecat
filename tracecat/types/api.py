from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, RootModel

from tracecat.db import ActionRun, WorkflowRun
from tracecat.types.actions import ActionType
from tracecat.types.secrets import SecretKeyValue

# TODO: Consistent API design
# Action and Workflow create / update params
# should be the same as the metadata responses

RunStatus = Literal["pending", "running", "failure", "success", "canceled"]


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
    icon_url: str | None


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    action_runs: list[ActionRun] = []

    @classmethod
    def from_orm(cls, run: WorkflowRun) -> WorkflowRunResponse:
        return cls(**run.model_dump(), action_runs=run.action_runs)


class ActionRunResponse(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    action_id: str
    workflow_run_id: str
    status: str
    error_msg: str | None = None
    result: dict[str, Any] | None = None

    @classmethod
    def from_orm(cls, run: ActionRun) -> ActionRunResponse:
        dict_result = None if run.result is None else json.loads(run.result)
        return cls(**run.model_dump(exclude={"result"}), result=dict_result)


class ActionRunEventParams(BaseModel):
    id: str  # This is deterministically defined in the runner
    owner_id: str
    created_at: datetime
    updated_at: datetime
    status: RunStatus
    workflow_run_id: str
    error_msg: str | None = None
    result: str | None = None  # JSON-serialized String


class WorkflowRunEventParams(BaseModel):
    id: str
    owner_id: str
    created_at: datetime
    updated_at: datetime
    status: RunStatus


class CreateWorkflowParams(BaseModel):
    title: str
    description: str


class UpdateWorkflowParams(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    object: str | None = None


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


class WebhookResponse(BaseModel):
    id: str
    secret: str
    action_id: str
    workflow_id: str
    url: str


class GetWebhookParams(BaseModel):
    webhook_id: str | None = None
    path: str | None = None


class AuthenticateWebhookResponse(BaseModel):
    status: Literal["Authorized", "Unauthorized"]
    owner_id: str | None = None
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
    # Secret types
    # ------------
    # - Custom: Arbitrary user-defined types
    # - Token: A token, e.g. API Key, JWT Token (TBC)
    # - OAuth2: OAuth2 Client Credentials (TBC)
    type: Literal["custom"]  # Support other types later
    name: str
    description: str | None = None
    keys: list[SecretKeyValue]
    tags: dict[str, str] | None = None


UpdateSecretParams = CreateSecretParams


class SearchSecretsParams(BaseModel):
    names: list[str]


class Tag(BaseModel):
    tag: str
    value: str


class Suppression(BaseModel):
    condition: str
    result: str  # Should evaluate to 'true' or 'false'


class ListModel[T](RootModel[list[T]]):
    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, i: int):
        return self.root[i]


class TagList(ListModel[Tag]):
    pass


class SuppressionList(ListModel[Suppression]):
    pass


class CaseParams(BaseModel):
    # SQLModel defaults
    id: str
    owner_id: str
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    # Case related fields
    workflow_id: str
    title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    context: dict[str, str] | str | None = None
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ]
    suppression: SuppressionList
    tags: TagList


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


class TriggerWorkflowRunParams(BaseModel):
    action_key: str
    payload: dict[str, Any]


class StartWorkflowParams(BaseModel):
    entrypoint_key: str
    entrypoint_payload: dict[str, Any]


class StartWorkflowResponse(BaseModel):
    status: str
    message: str
    id: str


class CopyWorkflowParams(BaseModel):
    owner_id: str


class SecretResponse(BaseModel):
    id: str
    type: Literal["custom"]  # Support other types later
    name: str
    description: str | None = None
    keys: list[SecretKeyValue]
