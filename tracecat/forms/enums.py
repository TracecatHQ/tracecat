"""Enums for forms."""

from enum import StrEnum


class FormStatus(StrEnum):
    """Whether a form accepts submissions."""

    ONLINE = "online"
    OFFLINE = "offline"


class FormEventType(StrEnum):
    """Activity recorded against a form.

    Stored as a plain string column, so a new value needs no migration.
    """

    FORM_VIEWED = "form_viewed"
    FORM_SUBMITTED = "form_submitted"
