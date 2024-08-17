"""Resource identifiers generation utilities."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from pydantic import StringConstraints

ResourceID = Annotated[str, StringConstraints(pattern=r"[a-z\-]{2,}-[0-9a-f]{32}")]
"""Resource identifier pattern. e.g. 'wf-77932a0b140a4465a1a25a5c95edcfb8'"""


def generate_resource_id(prefix: ResourcePrefix, *, sep: str = "-") -> ResourceID:
    """Generate a short unique identifier with a prefix."""

    return prefix + sep + uuid4().hex


def id_factory(
    prefix: ResourcePrefix, *, sep: str = "-"
) -> Callable[[str], ResourceID]:
    """Factory function to generate a short unique identifier with a prefix."""

    # Assert that the prefix is a valid resource class identifier.
    if prefix not in ResourcePrefix:
        raise ValueError(f"Invalid resource class identifier: {prefix!r}")

    def wrapper() -> ResourceID:
        return generate_resource_id(prefix, sep=sep)

    return wrapper


class ResourcePrefix(StrEnum):
    """Resource class identifier."""

    ACTION = "act"
    ACTION_RUN = "act-run"  # TODO: Unused
    UDF = "udf"
    WORKFLOW = "wf"
    WORKFLOW_EXECUTION = "exec"
    WORKFLOW_DEFN = "wf-defn"
    WORKFLOW_RUN = "wf-run"  # TODO: Unused
    WEBHOOK = "wh"
    SCHEDULE = "sch"
    SECRET = "secret"
    USER = "user"
    ORG = "org"
    CASE = "case"
    CASE_ACTION = "case-act"
    CASE_EVENT = "case-evt"
    CASE_CONTEXT = "case-ctx"

    def factory(self) -> Callable[[], ResourceID]:
        """Generate a unique ID with this prefix."""

        return id_factory(self)


class ResourceType(StrEnum):
    """Resource type identifier."""

    WORKSPACE = "workspace"
    ORGANIZATION = "organization"
    USER = "user"


if __name__ == "__main__":
    print(ResourcePrefix.WORKFLOW.factory()())
    print(id_factory("act")())
    print(id_factory("fails!"))
