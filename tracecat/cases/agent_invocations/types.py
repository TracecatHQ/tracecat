"""Domain types for case-comment agent invocations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

type CaseCommentAgentInvocationErrorKind = Literal[
    "startup",
    "preparation",
    "agent_turn",
    "completion",
    "cancelled",
]


class CaseCommentAgentInvocationError(TypedDict):
    """Structured terminal failure persisted for a comment agent invocation."""

    kind: CaseCommentAgentInvocationErrorKind
    message: str


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
class CommentAgentInput:
    """Structured initial input for an agent invoked from a comment thread."""

    model_context_prompt: str
    display_messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedCommentAgentSession:
    """Linked session and prompt prepared for parent-workflow execution."""

    invocation_id: uuid.UUID
    session_id: uuid.UUID
    prompt: str
    display_messages: tuple[str, ...]
