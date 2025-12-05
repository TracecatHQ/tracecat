"""Action identifiers."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import StringConstraints
from slugify import slugify

from tracecat.identifiers.common import TracecatUUID
from tracecat.identifiers.resource import ResourcePrefix

# Prefixes
ACT_ID_PREFIX = "act_"
LEGACY_ACTION_ID_PREFIX = "act-"

# Patterns for validation
_ACT_ID_SHORT_PATTERN = rf"{ACT_ID_PREFIX}[0-9a-zA-Z]+"
_LEGACY_ACTION_ID_PATTERN = r"act-[0-9a-f]{32}"

# Short ID type (used as TracecatUUID type parameter)
ActionIDShort = Annotated[str, StringConstraints(pattern=_ACT_ID_SHORT_PATTERN)]
LegacyActionID = Annotated[str, StringConstraints(pattern=_LEGACY_ACTION_ID_PATTERN)]


class ActionUUID(TracecatUUID[ActionIDShort]):
    """UUID for action resources.

    Supports:
    - Native UUID format (database storage)
    - Short ID format: `act_xxx`
    - Legacy format: `act-<32hex>`
    """

    prefix = ACT_ID_PREFIX
    legacy_prefix = LEGACY_ACTION_ID_PREFIX


AnyActionID = ActionUUID | ActionIDShort | LegacyActionID | uuid.UUID

# Keep ActionID as alias for backward compatibility in type hints
ActionID = uuid.UUID
"""A unique ID for an action. Now uses native UUID format."""

ActionKey = Annotated[str, StringConstraints(pattern=r"act:wf-[0-9a-f]{32}:[a-z0-9_]+")]
"""A unique key for an action, using the workflow ID and action ref. e.g. 'act:wf-77932a0b140a4465a1a25a5c95edcfb8:reshape_findings_into_smac'"""

ActionRef = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""A workflow-local unique reference for an action. e.g. 'reshape_findings_into_smac'"""


def ref(text: str) -> ActionRef:
    """Return a slugified version of the text."""
    return slugify(text, separator="_")


def key(workflow_id: str, action_ref: str) -> ActionKey:
    """Identifier key for an action, using the workflow ID and action ref."""
    return f"{ResourcePrefix.ACTION}:{workflow_id}:{action_ref}"
