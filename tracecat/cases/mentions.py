from __future__ import annotations

import re
import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MentionToken:
    """A parsed case-comment mention target."""

    target_type: str
    target_id: uuid.UUID
    label: str


_KNOWN_TARGET_TYPES = frozenset({"agent"})

_MENTION_PATTERN = re.compile(
    r"\[@(?P<label>[^\]]+)\]\(mention://(?P<target_type>[a-z]+)/(?P<target_id>[^)]+)\)"
)


def parse_mentions(content: str) -> list[MentionToken]:
    """Parse mention tokens from comment content.

    Malformed tokens (bad UUID, unknown target type, or broken syntax) are
    skipped silently. This is the only function in the application that should
    understand the mention token encoding.
    """
    tokens: list[MentionToken] = []
    for match in _MENTION_PATTERN.finditer(content):
        target_type = match.group("target_type")
        if target_type not in _KNOWN_TARGET_TYPES:
            continue
        try:
            target_id = uuid.UUID(match.group("target_id"))
        except ValueError:
            continue
        tokens.append(
            MentionToken(
                target_type=target_type,
                target_id=target_id,
                label=match.group("label"),
            )
        )
    return tokens
