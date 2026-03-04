"""Schemas for Watchtower monitor endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class WatchtowerAgentRead(BaseModel):
    """Watchtower agent row for monitor list views."""

    id: UUID
    organization_id: UUID
    fingerprint_hash: str
    agent_type: str
    agent_source: str
    agent_icon_key: str | None
    raw_user_agent: str | None
    raw_client_info: dict[str, object] | None
    auth_client_id: str | None
    last_user_id: UUID | None
    last_user_email: str | None
    last_user_name: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    blocked_at: datetime | None
    blocked_reason: str | None
    status: str
    active_session_count: int = 0


class WatchtowerAgentSessionRead(BaseModel):
    """Watchtower agent session row for monitor list views."""

    id: UUID
    organization_id: UUID
    agent_id: UUID | None
    session_state: str
    auth_transaction_id: str | None
    auth_client_id: str | None
    oauth_callback_seen_at: datetime | None
    agent_session_id: str | None
    initialize_seen_at: datetime | None
    user_id: UUID | None
    user_email: str | None
    user_name: str | None
    workspace_id: UUID | None
    first_seen_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None
    status: str


class WatchtowerAgentToolCallRead(BaseModel):
    """Watchtower tool-call event row."""

    id: UUID
    organization_id: UUID
    agent_id: UUID
    agent_session_id: UUID
    workspace_id: UUID | None
    tool_name: str
    call_status: str
    latency_ms: int | None
    args_redacted: dict[str, object]
    error_redacted: str | None
    called_at: datetime


class WatchtowerAgentListResponse(BaseModel):
    """Paginated response for Watchtower agents."""

    items: list[WatchtowerAgentRead]
    next_cursor: str | None = None
    has_more: bool = False


class WatchtowerAgentSessionListResponse(BaseModel):
    """Paginated response for Watchtower sessions."""

    items: list[WatchtowerAgentSessionRead]
    next_cursor: str | None = None
    has_more: bool = False


class WatchtowerAgentToolCallListResponse(BaseModel):
    """Paginated response for Watchtower tool calls."""

    items: list[WatchtowerAgentToolCallRead]
    next_cursor: str | None = None
    has_more: bool = False


class WatchtowerRevokeAgentSessionRequest(BaseModel):
    """Request payload for session revocation."""

    reason: str | None = Field(default=None, max_length=2000)


class WatchtowerDisableAgentRequest(BaseModel):
    """Request payload for disabling an agent."""

    reason: str | None = Field(default=None, max_length=2000)


class WatchtowerEnableAgentRequest(BaseModel):
    """Request payload for enabling an agent."""

    reason: str | None = Field(default=None, max_length=2000)
