"""Error masking policy, independent of expression provenance tracking."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class ErrorMaskingMode(StrEnum):
    PROVENANCE = "provenance"
    CONSERVATIVE = "conservative"


WITHHELD_ERROR_MESSAGE = "Details withheld because this operation may involve secrets."
# Include unknown runtime lineage, even when no secret was fetched in this action.
# This intentionally over-approximates references (including inside literals).
_RUNTIME_REFERENCE = re.compile(
    r"\b(?:SECRETS|ACTIONS|var|inputs|steps|TRIGGER|VARS|ENV)\b"
)


def may_reference_secrets(value: object) -> bool:
    """Conservatively inspect authored inputs without provenance or value tracking."""
    if isinstance(value, str):
        return bool(_RUNTIME_REFERENCE.search(value))
    if isinstance(value, Mapping):
        return any(
            may_reference_secrets(key) or may_reference_secrets(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(may_reference_secrets(item) for item in value)
    return False


@dataclass(slots=True)
class ErrorMaskingContext:
    """Invocation-local policy; unknown sensitivity withholds by default."""

    mode: ErrorMaskingMode
    sensitive: bool = True

    @property
    def withhold(self) -> bool:
        return self.mode is ErrorMaskingMode.CONSERVATIVE and self.sensitive
