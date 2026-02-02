"""Tracecat identifiers module.


Thinking
--------
- Consistent and meaningful identifiers are important for traceability and debugging.
- The identifiers should be unique and easy to generate.
- The identifiers should be human-readable and easy to understand.


Resource Classes
----------------
All ideantifiers should be prefixed with a short **resource class identifier** to avoid conflicts.
We use:
- `act` for actions
- `wf` for workflow runs
- `user` for users
- `org` for organizations
- `secret` for secrets

etc.
Try to keep identifier segments under 5 characters.


Convention
----------
All resource IDs are constructed by their resource class prefix followed by a dash and a unique hex string.
e.g. act-77932a0b140a4465a1a25a5c95edcfb8

Resource classes may have alternative identifiers to their IDs.
An example would be action references and keys:
- Action references: used as a convenient way to refer to an action in a workflow,
- Action keys: used as an alternative unique identifier for an action.

Resources may have related or hierarchical identifiers that depend on other resources.
For example, a workflow run ID may depend on the workflow ID:
e.g. wf-77932a0b140a4465a1a25a5c95edcfb8:run-b140a425a577932a0c95edcfb8465a1a

"""

import uuid
from typing import Literal

from tracecat.identifiers import action, resource, schedules, secret, workflow
from tracecat.identifiers.action import ActionID, ActionKey, ActionRef, ActionUUID
from tracecat.identifiers.resource import id_factory
from tracecat.identifiers.schedules import (
    ScheduleUUID,
    schedule_id_to_temporal,
)
from tracecat.identifiers.secret import SecretID, SecretUUID
from tracecat.identifiers.workflow import (
    WorkflowExecutionID,
    WorkflowExecutionSuffixID,
    WorkflowID,
    WorkflowRunID,
    WorkflowUUID,
)

UserID = uuid.UUID
WorkspaceID = uuid.UUID
OrganizationID = uuid.UUID
"""Organization identifier type. Uses a sentinel UUID for the default organization."""

OwnerID = uuid.UUID
"""Generic owner identifier for Ownership model. Can be UserID, WorkspaceID, or OrganizationID."""

# SecretID is now imported from tracecat/identifiers/secret.py

WebhookID = uuid.UUID
"""A unique ID for a webhook. Uses native UUID format."""

WorkflowDefinitionID = uuid.UUID
"""A unique ID for a workflow definition. Now uses native UUID format."""

VariableID = uuid.UUID
SessionID = uuid.UUID
WorkflowTagID = uuid.UUID
TagID = WorkflowTagID
CaseTagID = uuid.UUID
TableID = uuid.UUID
TableColumnID = uuid.UUID
TableRowID = uuid.UUID
InvitationID = uuid.UUID

InternalServiceID = Literal[
    "tracecat-api",
    "tracecat-bootstrap",
    "tracecat-cli",
    "tracecat-executor",
    "tracecat-agent-executor",
    "tracecat-case-triggers",
    "tracecat-llm-gateway",
    "tracecat-mcp",
    "tracecat-runner",
    "tracecat-schedule-runner",
    "tracecat-service",
    "tracecat-ui",
]

__all__ = [
    "ActionID",
    "ActionKey",
    "ActionRef",
    "ActionUUID",
    "WorkflowID",
    "WorkflowUUID",
    "WorkflowExecutionID",
    "WorkflowExecutionSuffixID",
    "WorkflowRunID",
    "ScheduleUUID",
    "schedule_id_to_temporal",
    "SecretID",
    "SecretUUID",
    "WebhookID",
    "WorkflowDefinitionID",
    "UserID",
    "WorkspaceID",
    "OrganizationID",
    "OwnerID",
    "TagID",
    "WorkflowTagID",
    "CaseTagID",
    "SessionID",
    "VariableID",
    "InvitationID",
    "id_factory",
    "action",
    "workflow",
    "schedules",
    "secret",
    "resource",
]
