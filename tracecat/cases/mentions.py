from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from tracecat.cases.enums import MentionTargetType

# Must match the `label` column width on `CaseCommentMention`
# (`tracecat/db/models.py`, `String(255)`). A label longer than this cannot be
# persisted, so an overlong label is treated as a malformed token and skipped,
# consistent with the other malformed-token handling below.
_MAX_LABEL_LENGTH = 255


@dataclass(frozen=True, slots=True)
class MentionToken:
    """A parsed case-comment mention target."""

    target_type: MentionTargetType
    target_id: uuid.UUID
    label: str


_MENTION_PATTERN = re.compile(
    r"\[@(?P<label>[^\]]+)\]\(mention://(?P<target_type>[a-z]+)/(?P<target_id>[^)]+)\)"
)


def parse_mentions(content: str) -> list[MentionToken]:
    """Parse mention tokens from comment content.

    Malformed tokens (bad UUID, unknown target type, overlong label, or
    broken syntax) are skipped silently. This is the only function in the
    application that should understand the mention token encoding.
    """
    tokens: list[MentionToken] = []
    for match in _MENTION_PATTERN.finditer(content):
        try:
            target_type = MentionTargetType(match.group("target_type"))
        except ValueError:
            continue
        try:
            target_id = uuid.UUID(match.group("target_id"))
        except ValueError:
            continue
        label = match.group("label")
        if len(label) > _MAX_LABEL_LENGTH:
            continue
        tokens.append(
            MentionToken(
                target_type=target_type,
                target_id=target_id,
                label=label,
            )
        )
    return tokens
