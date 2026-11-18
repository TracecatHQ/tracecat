"""Types for custom field column metadata."""

from typing import TypedDict


class CustomFieldColumn(TypedDict):
    """A user-visible column of a workspace custom fields table.

    Read from the Postgres catalog. ``type`` is ``format_type`` without the
    type modifier, for example ``"text"`` or ``"timestamp with time zone"``.
    """

    name: str
    type: str
    nullable: bool
    default: str | None
    comment: str | None
