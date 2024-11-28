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
from typing import Annotated, Literal

from pydantic import UUID4, StringConstraints

from tracecat.identifiers import action, resource, schedules, workflow
from tracecat.identifiers.action import ActionID, ActionKey, ActionRef
from tracecat.identifiers.resource import id_factory
from tracecat.identifiers.schedules import ScheduleID
from tracecat.identifiers.workflow import (
    WorkflowExecutionID,
    WorkflowID,
    WorkflowRunID,
    WorkflowScheduleID,
)

UserID = UUID4
WorkspaceID = UUID4
OwnerID = uuid.UUID
"""Owner identifier type. This is UUID because we use UUID(0) for the organization.

Owners can be Workspaces or the Organization.
"""

SecretID = Annotated[str, StringConstraints(pattern=r"secret-[0-9a-f]{32}")]
SessionID = UUID4

InternalServiceID = Literal[
    "tracecat-runner",
    "tracecat-api",
    "tracecat-cli",
    "tracecat-schedule-runner",
    "tracecat-service",
    "tracecat-registry",
]

__all__ = [
    "ActionID",
    "ActionKey",
    "ActionRef",
    "WorkflowID",
    "WorkflowExecutionID",
    "WorkflowScheduleID",
    "WorkflowRunID",
    "ScheduleID",
    "UserID",
    "WorkspaceID",
    "SessionID",
    "id_factory",
    "action",
    "workflow",
    "schedules",
    "resource",
]
