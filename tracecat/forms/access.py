"""Access rules for forms that are not a single scope check.

Kept free of service imports so callers outside the forms module can use it
without an import cycle.
"""

from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope


def can_read_form_config(role: Role) -> bool:
    """Whether a caller may see a form's raw config.

    ``form:read`` alone is what an intake-only requester holds. They get the
    resolved form from the render route and nothing else, so the config needs
    a caller who also builds forms or reads submissions.
    """
    scopes = role.scopes or frozenset()
    if not has_scope(scopes, "form:read"):
        return False
    return has_scope(scopes, "form:update") or has_scope(scopes, "form:submission:read")
