"""Domain types for case-comment agent invocations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict


class PersistedCommentAgentRole(TypedDict):
    """JSON-safe authorization context retained for durable delivery."""

    type: Literal["user", "service", "service_account"]
    workspace_id: str | None
    bound_workspace_id: str | None
    organization_id: str | None
    user_id: str | None
    service_account_id: str | None
    service_id: str
    is_platform_superuser: bool
    scopes: list[str] | None


@dataclass(frozen=True, slots=True)
class CommentThreadEntry:
    """One comment in the thread supplied to an invoked agent."""

    id: uuid.UUID
    parent_id: uuid.UUID | None
    author_label: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CommentThreadContext:
    """Ordered snapshot of the thread containing an agent mention."""

    thread_root_id: uuid.UUID
    invoking_comment_id: uuid.UUID
    entries: tuple[CommentThreadEntry, ...]


@dataclass(frozen=True, slots=True)
class PreparedCommentAgentSession:
    """Linked session and prompt prepared for parent-workflow execution."""

    invocation_id: uuid.UUID
    session_id: uuid.UUID
    prompt: str
