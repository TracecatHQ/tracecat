"""Who may do what with forms, beyond a route's scope check.

Kept free of service imports so callers outside the forms module can use it
without an import cycle.
"""

import uuid

from sqlalchemy import ColumnElement, false
from sqlalchemy.orm import InstrumentedAttribute

from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope


def can_edit_forms(role: Role) -> bool:
    """Editors see offline forms and resolve configs that are not saved yet."""
    return has_scope(role.scopes or frozenset(), "form:update")


def can_read_submissions(role: Role) -> bool:
    """Readers see every submission, its run fields and the form's counts."""
    return has_scope(role.scopes or frozenset(), "form:submission:read")


def can_read_form_config(role: Role) -> bool:
    """Whether a caller may see a form's raw config and its workflow.

    ``form:read`` alone is what an intake-only requester holds. They get the
    resolved form from the render route and nothing else.
    """
    if not has_scope(role.scopes or frozenset(), "form:read"):
        return False
    return can_edit_forms(role) or can_read_submissions(role)


def caller_user_id(role: Role) -> uuid.UUID | None:
    """The calling user. None for a service account or a service."""
    return role.user_id if role.type == "user" else None


def is_caller(
    role: Role,
    user_id: InstrumentedAttribute[uuid.UUID | None],
    service_account_id: InstrumentedAttribute[uuid.UUID | None],
) -> ColumnElement[bool]:
    """Match rows the caller made, by the columns that record who made them.

    A role that is neither a user nor a service account matches nothing.
    """
    if (caller := caller_user_id(role)) is not None:
        return user_id == caller
    if role.service_account_id is not None:
        return service_account_id == role.service_account_id
    return false()
