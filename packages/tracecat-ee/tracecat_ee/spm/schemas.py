"""Schemas for AI SPM endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.pagination import CursorPaginatedResponse
from tracecat_ee.spm.types import (
    SpmAssetClass,
    SpmAssetType,
    SpmControlCheck,
    SpmEndpointPlatform,
    SpmEndpointStatus,
    SpmEnforcementAction,
    SpmEnforcementTaskStatus,
    SpmFindingDecisionType,
    SpmFindingStatus,
    SpmHarness,
    SpmSeverity,
    SpmSyncTaskResultStatus,
)


class SpmControlRead(Schema):
    """Static SPM control manifest."""

    id: str
    revision: str
    title: str
    description: str
    harness: SpmHarness
    asset_class: SpmAssetClass
    asset_type: SpmAssetType
    severity: SpmSeverity
    check: SpmControlCheck
    action: SpmEnforcementAction


class SpmEndpointRead(Schema):
    """SPM endpoint row."""

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    harness: SpmHarness
    platform: SpmEndpointPlatform
    status: SpmEndpointStatus
    hostname: str | None = None
    os_user: str | None = None
    home_path: str | None = None
    endpoint_version: str | None = None
    client_metadata: dict[str, Any] = Field(default_factory=dict)
    enrolled_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_sync_error: str | None = None
    created_at: datetime
    updated_at: datetime


class SpmEndpointCreate(Schema):
    """Operator request to create an endpoint enrollment."""

    name: str = Field(min_length=1, max_length=255)
    harness: SpmHarness = Field(default=SpmHarness.CLAUDE_CODE)
    platform: SpmEndpointPlatform = Field(default=SpmEndpointPlatform.MACOS)
    hostname: str | None = Field(default=None, max_length=255)
    os_user: str | None = Field(default=None, max_length=255)
    home_path: str | None = Field(default=None, max_length=500)
    endpoint_version: str | None = Field(default=None, max_length=64)
    client_metadata: dict[str, Any] = Field(default_factory=dict)


class SpmEndpointCreateResponse(Schema):
    """Endpoint create response with one-time enrollment token."""

    endpoint: SpmEndpointRead
    enrollment_token: str


class SpmAssetRead(Schema):
    """Deduplicated SPM asset row."""

    id: uuid.UUID
    organization_id: uuid.UUID
    harness: SpmHarness
    asset_class: SpmAssetClass
    asset_type: SpmAssetType
    identity_key: str
    display_name: str
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="asset_metadata",
    )
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class SpmAssetSightingRead(Schema):
    """Endpoint-scoped observation for an asset."""

    id: uuid.UUID
    organization_id: uuid.UUID
    endpoint_id: uuid.UUID
    asset_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_state: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class SpmFindingRead(Schema):
    """Current-state finding row."""

    id: uuid.UUID
    organization_id: uuid.UUID
    endpoint_id: uuid.UUID
    asset_id: uuid.UUID
    asset_sighting_id: uuid.UUID | None = None
    control_id: str
    control_revision: str | None = None
    harness: SpmHarness
    asset_class: SpmAssetClass
    asset_type: SpmAssetType
    severity: SpmSeverity
    status: SpmFindingStatus
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    enrichment: dict[str, Any] = Field(default_factory=dict)
    recommended_action: SpmEnforcementAction | None = None
    recommended_payload: dict[str, Any] = Field(default_factory=dict)
    opened_at: datetime
    closed_at: datetime | None = None
    last_decision_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SpmFindingDecisionCreate(Schema):
    """Operator decision request."""

    decision: SpmFindingDecisionType
    reason: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)


class SpmFindingDecisionRead(Schema):
    """Recorded decision row."""

    id: uuid.UUID
    organization_id: uuid.UUID
    finding_id: uuid.UUID
    endpoint_id: uuid.UUID | None = None
    decision: SpmFindingDecisionType
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    decided_by_user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class SpmEnforcementTaskRead(Schema):
    """Task queued for local endpoint reconciliation."""

    id: uuid.UUID
    organization_id: uuid.UUID
    endpoint_id: uuid.UUID
    finding_id: uuid.UUID | None = None
    action: SpmEnforcementAction
    payload: dict[str, Any] = Field(default_factory=dict)
    status: SpmEnforcementTaskStatus
    requested_by_user_id: uuid.UUID | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class SpmSyncAuthKind(Schema):
    """Placeholder schema kept for forward compatibility."""

    type: str


class SpmSyncAssetUpsert(Schema):
    """Asset observation submitted by an endpoint."""

    harness: SpmHarness
    asset_class: SpmAssetClass
    asset_type: SpmAssetType
    identity_key: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=255)
    content_hash: str | None = Field(default=None, max_length=64)
    workspace_id: uuid.UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_state: dict[str, Any] = Field(default_factory=dict)


class SpmSyncTaskResult(Schema):
    """Task execution result reported during sync."""

    task_id: uuid.UUID
    status: SpmSyncTaskResultStatus
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=4000)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SpmEndpointSyncRequest(Schema):
    """Private endpoint sync payload."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    endpoint_version: str | None = Field(default=None, max_length=64)
    hostname: str | None = Field(default=None, max_length=255)
    os_user: str | None = Field(default=None, max_length=255)
    home_path: str | None = Field(default=None, max_length=500)
    status: SpmEndpointStatus = Field(default=SpmEndpointStatus.ACTIVE)
    client_metadata: dict[str, Any] = Field(default_factory=dict)
    assets: list[SpmSyncAssetUpsert] = Field(default_factory=list)
    task_results: list[SpmSyncTaskResult] = Field(default_factory=list)


class SpmEndpointSyncResponse(Schema):
    """Private endpoint sync response."""

    endpoint: SpmEndpointRead
    endpoint_secret: str | None = None
    tasks: list[SpmEnforcementTaskRead] = Field(default_factory=list)


SpmEndpointListResponse = CursorPaginatedResponse[SpmEndpointRead]
SpmAssetListResponse = CursorPaginatedResponse[SpmAssetRead]
SpmFindingListResponse = CursorPaginatedResponse[SpmFindingRead]
