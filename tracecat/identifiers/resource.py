"""Resource identifiers generation utilities."""

from collections.abc import Callable
from typing import Annotated
from uuid import uuid4

from pydantic import StringConstraints

ResourceID = Annotated[str, StringConstraints(pattern=r"[a-z\-]{2,}-[0-9a-f]{32}")]
"""Resource identifier pattern. e.g. 'wf-77932a0b140a4465a1a25a5c95edcfb8'"""


def gen_resource_id(prefix: str, *, sep: str = "-") -> ResourceID:
    """Generate a short unique identifier with a prefix."""

    return prefix + sep + uuid4().hex


def gen_id(prefix: str, *, sep: str = "-") -> Callable[[str], ResourceID]:
    """Factory function to generate a short unique identifier with a prefix."""

    def wrapper() -> ResourceID:
        return gen_resource_id(prefix, sep=sep)

    return wrapper
