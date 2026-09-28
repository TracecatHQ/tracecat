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
# ``uuid.UUID`` accepts the 36-character canonical form with braces and the
# ``urn:uuid:`` prefix combined. Bounding every variable-width token keeps
# failed matches linear in the size of untrusted comment content.
_MAX_TARGET_ID_LENGTH = 47
_MAX_TARGET_TYPE_LENGTH = max(
    len(target_type.value) for target_type in MentionTargetType
)


@dataclass(frozen=True, slots=True)
class MentionToken:
    """A parsed case-comment mention target."""

    target_type: MentionTargetType
    target_id: uuid.UUID
    label: str


_MENTION_PATTERN = re.compile(
    rf"\[@(?P<label>[^\]]{{1,{_MAX_LABEL_LENGTH}}})\]"
    rf"\(mention://(?P<target_type>[a-z]{{1,{_MAX_TARGET_TYPE_LENGTH}}})/"
    rf"(?P<target_id>[^)]{{1,{_MAX_TARGET_ID_LENGTH}}})\)"
)


def parse_mentions(content: str) -> list[MentionToken]:
    """Parse mention tokens from comment content.

    Malformed tokens (bad UUID, unknown target type, overlong label, or
    broken syntax) are skipped silently.
    """
    tokens: list[MentionToken] = []
    for match in _MENTION_PATTERN.finditer(content):
        if token := _parse_mention_match(match):
            tokens.append(token)
    return tokens


def render_mentions_as_text(content: str) -> str:
    """Replace valid encoded mentions with their visible ``@label`` text.

    Malformed mention-like text is preserved verbatim instead of being presented
    as a real mention.
    """

    def replace(match: re.Match[str]) -> str:
        if token := _parse_mention_match(match):
            return f"@{token.label}"
        return match.group(0)

    return _MENTION_PATTERN.sub(replace, content)


def _parse_mention_match(match: re.Match[str]) -> MentionToken | None:
    """Validate one encoded mention match.

    This module is the only place in the application that should understand the
    mention token encoding.
    """
    try:
        target_type = MentionTargetType(match.group("target_type"))
        target_id = uuid.UUID(match.group("target_id"))
    except ValueError:
        return None

    label = match.group("label")
    if len(label) > _MAX_LABEL_LENGTH:
        return None
    return MentionToken(target_type=target_type, target_id=target_id, label=label)
