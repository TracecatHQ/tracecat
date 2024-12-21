"""Action identifiers."""

from typing import Annotated

from pydantic import StringConstraints
from slugify import slugify

ActionID = Annotated[str, StringConstraints(pattern=r"act-[0-9a-f]{32}")]
"""A unique ID for an action. e.g. 'act-77932a0b140a4465a1a25a5c95edcfb8'"""

ActionKey = Annotated[str, StringConstraints(pattern=r"act:wf-[0-9a-f]{32}:[a-z0-9_]+")]
"""A unique key for an action, using the workflow ID and action ref. e.g. 'act:wf-77932a0b140a4465a1a25a5c95edcfb8:reshape_findings_into_smac'"""

ActionRef = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""A workflow-local unique reference for an action. e.g. 'reshape_findings_into_smac'"""


def ref(text: str) -> ActionRef:
    """Return a slugified version of the text."""
    return slugify(text, separator="_")
