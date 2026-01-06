"""ExpectedField schema for template action expects definitions.

This is separated from expectations.py to avoid pulling in lark when only
the ExpectedField type is needed.
"""

from typing import Any

from pydantic import BaseModel


class ExpectedField(BaseModel):
    type: str
    description: str | None = None
    default: Any | None = None
