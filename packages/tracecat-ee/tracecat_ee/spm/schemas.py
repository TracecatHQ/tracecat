"""Schemas for AI SPM endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, model_validator

from tracecat import config
from tracecat.core.schemas import Schema
from tracecat.pagination import CursorPaginatedResponse
from tracecat_ee.spm.taxonomy import validate_inventory_binding
from tracecat_ee.spm.types import (
    SpmEndpointComplianceStatus,
    SpmEndpointPlatform,
    SpmEndpointStatus,
    SpmEnforcementAction,
    SpmEnforcementTaskStatus,
    SpmFindingDecisionType,
    SpmFindingStatus,
    SpmHarness,
    SpmInventoryItemType,
    SpmInventoryRelationshipType,
    SpmInventorySourceType,
    SpmResponseActionPreviewStatus,
    SpmSeverity,
    SpmSyncTaskResultStatus,
)


class SpmControlRead(Schema):
    """Static SPM control manifest."""

    id: uuid.UUID
    key: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    revision: str
    title: str
    description: str
    harness: SpmHarness
    item_type: SpmInventoryItemType
    source_types: list[SpmInventorySourceType] = Field(default_factory=list)
    severity: SpmSeverity
    action: SpmEnforcementAction


class SpmResponseActionRead(Schema):
    """Static response action catalog entry."""

    key: SpmEnforcementAction
    title: str
    description: str
    harness: SpmHarness
    item_types: list[SpmInventoryItemType]
    execution_mode: str
    preview_supported: bool
    target_surface: str
    payload_fields: list[str] = Field(default_factory=list)
    disruptive: bool = False


class SpmControlPolicy(Schema):
    """Endpoint policy materialized for control evaluation."""

    approved_mcp_servers: set[str] = Field(default_factory=set)
    approved_trusted_directories: set[str] = Field(default_factory=set)
    approved_additional_directories: set[str] = Field(default_factory=set)
    approved_hooks: set[str] = Field(default_factory=set)
    approved_skills: set[str] = Field(default_factory=set)
    approved_permission_config: Any = None
    approved_sandbox_config: Any = None

    @classmethod
    def from_client_metadata(cls, client_metadata: dict[str, Any]) -> SpmControlPolicy:
        raw_policy = client_metadata.get("spm_policy")
        if not isinstance(raw_policy, dict):
            return cls()

        return cls(
            approved_mcp_servers={
                identity
                for identity in (
                    cls._normalize_mcp_identity(item)
                    for item in raw_policy.get("approved_mcp_servers", [])
                )
                if identity is not None
            },
            approved_trusted_directories=set(
                cls._string_items(raw_policy.get("approved_trusted_directories", []))
            ),
            approved_additional_directories=set(
                cls._string_items(raw_policy.get("approved_additional_directories", []))
            ),
            approved_hooks=set(cls._string_items(raw_policy.get("approved_hooks", []))),
            approved_skills=set(
                cls._string_items(raw_policy.get("approved_skills", []))
            ),
            approved_permission_config=raw_policy.get("approved_permission_config"),
            approved_sandbox_config=raw_policy.get("approved_sandbox_config"),
        )

    @staticmethod
    def _string_items(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, str) and item]

    @staticmethod
    def _normalize_mcp_identity(raw: Any) -> str | None:
        if isinstance(raw, str) and raw:
            return raw
        if not isinstance(raw, dict):
            return None
        server_name = raw.get("server_name")
        resolved_identity = raw.get("resolved_identity")
        if not isinstance(server_name, str) or not isinstance(resolved_identity, str):
            return None
        return f"{server_name}|{resolved_identity}"


class SpmControlInventoryItemBase(Schema):
    """Common endpoint-collected inventory item data passed to controls."""

    id: uuid.UUID
    observation_id: uuid.UUID
    identity_key: str
    display_name: str
    harness: SpmHarness
    item_type: SpmInventoryItemType
    source_type: SpmInventorySourceType
    item_location: str
    source_location: str
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_state: dict[str, Any] = Field(default_factory=dict)


class SpmDirectoryControlData(SpmControlInventoryItemBase):
    """Directory item data collected from endpoint inventory."""

    directory_path: str
    file_path: str | None = None
    parse_status: str | None = None


class SpmConfigControlData(SpmControlInventoryItemBase):
    """Permission or sandbox config data collected from endpoint inventory."""

    file_path: str | None = None
    project_root: str | None = None
    parse_status: str | None = None
    value: Any = None


class SpmMcpServerControlData(SpmControlInventoryItemBase):
    """MCP server data collected from endpoint inventory."""

    file_path: str | None = None
    project_root: str | None = None
    parse_status: str | None = None
    server_name: str | None = None
    resolved_identity: str | None = None
    mcp_identity_key: str | None = None


class SpmHookControlData(SpmControlInventoryItemBase):
    """Hook data collected from endpoint inventory."""

    file_path: str | None = None
    project_root: str | None = None
    parse_status: str | None = None
    fingerprint: str | None = None
    event: str | None = None
    command: str | None = None


class SpmSkillControlData(SpmControlInventoryItemBase):
    """Skill data collected from endpoint inventory."""

    file_path: str | None = None
    project_root: str | None = None
    parse_status: str | None = None
    fingerprint: str | None = None
    name: str | None = None
    skill: Any = None


class SpmInstructionFileControlData(SpmControlInventoryItemBase):
    """Claude instruction-file data collected from endpoint inventory."""

    file_path: str | None = None
    project_root: str | None = None
    parse_status: str | None = None
    enforceable: bool | None = None
    language_signal: dict[str, Any] = Field(default_factory=dict)
    obfuscation: dict[str, Any] = Field(default_factory=dict)
    urls: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    ips: list[str] = Field(default_factory=list)


type SpmAnyControlInventoryItemData = (
    SpmDirectoryControlData
    | SpmConfigControlData
    | SpmMcpServerControlData
    | SpmHookControlData
    | SpmSkillControlData
    | SpmInstructionFileControlData
)


class SpmControlContext(Schema):
    """Input contract for a single SPM control check."""

    item: SpmAnyControlInventoryItemData
    policy: SpmControlPolicy
    intelligence: dict[str, Any] = Field(default_factory=dict)


class SpmControlResult(Schema):
    """Result returned by a single SPM control check."""

    failed: bool
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommended_payload: dict[str, Any] = Field(default_factory=dict)
    enrichment: dict[str, Any] = Field(default_factory=dict)


class SpmEndpointRead(Schema):
    """SPM endpoint row."""

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    harness: SpmHarness
    platform: SpmEndpointPlatform
    status: SpmEndpointStatus
    compliance_status: SpmEndpointComplianceStatus
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


class SpmInventoryItemRead(Schema):
    """Deduplicated SPM inventory item row."""

    id: uuid.UUID
    organization_id: uuid.UUID
    harness: SpmHarness
    item_type: SpmInventoryItemType
    source_type: SpmInventorySourceType
    item_location: str
    source_location: str
    identity_key: str
    display_name: str
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="item_metadata",
    )
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class SpmInventoryQueryParams(Schema):
    """Filter params for deduplicated inventory."""

    limit: int = Field(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    )
    cursor: str | None = None
    harness: SpmHarness | None = None
    endpoint_id: uuid.UUID | None = None
    item_type: SpmInventoryItemType | None = None
    source_type: SpmInventorySourceType | None = None


class SpmInventoryObservationRead(Schema):
    """Endpoint-scoped observation for an inventory item."""

    id: uuid.UUID
    organization_id: uuid.UUID
    endpoint_id: uuid.UUID
    inventory_item_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_state: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class SpmEndpointInventoryItemRead(Schema):
    """Endpoint-scoped inventory item row with per-observation state."""

    inventory_item_id: uuid.UUID
    inventory_observation_id: uuid.UUID
    organization_id: uuid.UUID
    endpoint_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    harness: SpmHarness
    item_type: SpmInventoryItemType
    source_type: SpmInventorySourceType
    item_location: str
    source_location: str
    identity_key: str
    display_name: str
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="item_metadata",
    )
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_state: dict[str, Any] = Field(default_factory=dict)
    first_seen_at: datetime
    last_seen_at: datetime


class SpmFindingRead(Schema):
    """Current-state finding row."""

    id: uuid.UUID
    organization_id: uuid.UUID
    endpoint_id: uuid.UUID
    inventory_item_id: uuid.UUID
    inventory_observation_id: uuid.UUID | None = None
    control_id: uuid.UUID
    control_key: str
    control_revision: str | None = None
    harness: SpmHarness
    item_type: SpmInventoryItemType
    source_type: SpmInventorySourceType
    item_location: str
    source_location: str
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


class SpmFindingQueryParams(Schema):
    """Filter params for endpoint findings."""

    limit: int = Field(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    )
    cursor: str | None = None
    endpoint_id: uuid.UUID | None = None
    control_id: str | None = Field(default=None, max_length=255)


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


class SpmResponseActionPreviewCreate(Schema):
    """Operator request to create a response action preview for a finding."""

    action: SpmEnforcementAction | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SpmResponseActionPreviewRead(Schema):
    """Endpoint-generated dry-run preview for a response action."""

    id: uuid.UUID
    organization_id: uuid.UUID
    endpoint_id: uuid.UUID
    finding_id: uuid.UUID | None = None
    action: SpmEnforcementAction
    payload: dict[str, Any] = Field(default_factory=dict)
    status: SpmResponseActionPreviewStatus
    requested_by_user_id: uuid.UUID | None = None
    target_path: str | None = None
    before_content: str | None = None
    after_content: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    completed_at: datetime | None = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class SpmSyncAuthKind(Schema):
    """Placeholder schema kept for forward compatibility."""

    type: str


class SpmSyncInventoryItemUpsert(Schema):
    """Inventory item observation submitted by an endpoint."""

    harness: SpmHarness
    item_type: SpmInventoryItemType
    source_type: SpmInventorySourceType
    item_location: str = Field(min_length=1, max_length=1024)
    source_location: str = Field(min_length=1, max_length=1024)
    identity_key: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=255)
    content_hash: str | None = Field(default=None, max_length=64)
    workspace_id: uuid.UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_state: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_inventory_taxonomy(self) -> SpmSyncInventoryItemUpsert:
        validate_inventory_binding(
            harness=self.harness,
            item_type=self.item_type,
            source_type=self.source_type,
            source_location=self.source_location,
        )
        return self


class SpmSyncInventoryRelationshipUpsert(Schema):
    """Endpoint-observed relationship between inventory items."""

    relationship_type: SpmInventoryRelationshipType
    from_identity_key: str = Field(min_length=1, max_length=500)
    to_identity_key: str = Field(min_length=1, max_length=500)
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_state: dict[str, Any] = Field(default_factory=dict)


class SpmInventoryRelationshipRead(Schema):
    """Endpoint-scoped relationship between observed inventory items."""

    id: uuid.UUID
    organization_id: uuid.UUID
    endpoint_id: uuid.UUID
    relationship_type: SpmInventoryRelationshipType
    from_inventory_item_id: uuid.UUID
    to_inventory_item_id: uuid.UUID
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_state: dict[str, Any] = Field(default_factory=dict)
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class SpmInventoryTaxonomyEntryRead(Schema):
    """Public taxonomy metadata for an inventory item or source type."""

    key: str
    display_value: str
    icon_key: str
    description: str
    kind: str | None = None
    enforcement: str | None = None


class SpmInventoryTaxonomyBindingRead(Schema):
    """Allowed source types for an inventory item type."""

    item_type: SpmInventoryItemType
    source_types: list[SpmInventorySourceType]
    enforcement: str


class SpmInventoryTaxonomyHarnessRead(Schema):
    """Harness-scoped Agent SPM inventory taxonomy."""

    item_types: list[SpmInventoryTaxonomyEntryRead]
    source_types: list[SpmInventoryTaxonomyEntryRead]
    bindings: list[SpmInventoryTaxonomyBindingRead]
    relationship_types: list[str]


class SpmInventoryTaxonomyRead(Schema):
    """Agent SPM inventory taxonomy."""

    version: int
    harnesses: dict[SpmHarness, SpmInventoryTaxonomyHarnessRead]


class SpmSyncTaskResult(Schema):
    """Task execution result reported during sync."""

    task_id: uuid.UUID
    status: SpmSyncTaskResultStatus
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=4000)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SpmSyncResponseActionPreviewResult(Schema):
    """Action preview result reported during sync."""

    preview_id: uuid.UUID
    status: SpmResponseActionPreviewStatus
    target_path: str | None = Field(default=None, max_length=1024)
    before_content: str | None = None
    after_content: str | None = None
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
    inventory_items: list[SpmSyncInventoryItemUpsert] = Field(default_factory=list)
    relationships: list[SpmSyncInventoryRelationshipUpsert] = Field(
        default_factory=list
    )
    task_results: list[SpmSyncTaskResult] = Field(default_factory=list)
    action_preview_results: list[SpmSyncResponseActionPreviewResult] = Field(
        default_factory=list
    )


class SpmEndpointSyncResponse(Schema):
    """Private endpoint sync response."""

    endpoint: SpmEndpointRead
    endpoint_secret: str | None = None
    tasks: list[SpmEnforcementTaskRead] = Field(default_factory=list)
    action_previews: list[SpmResponseActionPreviewRead] = Field(default_factory=list)


SpmEndpointListResponse = CursorPaginatedResponse[SpmEndpointRead]
SpmInventoryListResponse = CursorPaginatedResponse[SpmInventoryItemRead]
SpmEndpointInventoryListResponse = CursorPaginatedResponse[SpmEndpointInventoryItemRead]
SpmFindingListResponse = CursorPaginatedResponse[SpmFindingRead]
SpmResponseActionPreviewListResponse = CursorPaginatedResponse[
    SpmResponseActionPreviewRead
]
