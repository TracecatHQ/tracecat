"""API contracts for managed resources in a target workspace."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from tracecat.workspace_sync.enums import SyncResourceType


class ManagedResourceRead(BaseModel):
    """One managed resource, for the "Managed by Tracecat" badge."""

    resource_type: SyncResourceType
    local_id: uuid.UUID
    source_id: str
    deployed_tag: str = Field(description="Package tag the resource came from.")
    deployed_commit_sha: str
    deployed_at: datetime = Field(description="When the last rollout wrote it.")


class DetachedCopyRead(BaseModel):
    """A customer-owned copy made by detaching a managed resource."""

    resource_type: SyncResourceType
    local_id: uuid.UUID
    original_local_id: uuid.UUID
    original_name: str
    detached_at: datetime


class ManagedResourceDetach(BaseModel):
    """Detach a managed resource into an editable copy named ``new_name``."""

    resource_type: SyncResourceType
    local_id: uuid.UUID
    new_name: str = Field(min_length=1, max_length=100)
