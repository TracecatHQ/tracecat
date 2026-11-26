"""Which roles hold the trigger scopes."""

from tracecat.authz.scopes import (
    ADMIN_SCOPES,
    EDITOR_SCOPES,
    ORG_ADMIN_SCOPES,
    ORG_OWNER_SCOPES,
    VIEWER_SCOPES,
    WORKSPACE_OPERATIONAL_SCOPES,
)
from tracecat.authz.seeding import SYSTEM_SCOPE_DEFINITIONS
from tracecat.service_accounts.constants import (
    WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES,
)

TRIGGER_SCOPES = frozenset(
    {"trigger:read", "trigger:create", "trigger:update", "trigger:delete"}
)


def test_trigger_scopes_are_seeded() -> None:
    seeded = {definition.name for definition in SYSTEM_SCOPE_DEFINITIONS}

    assert TRIGGER_SCOPES <= seeded


def test_viewers_read_and_editors_manage_triggers() -> None:
    assert VIEWER_SCOPES & TRIGGER_SCOPES == {"trigger:read"}
    for scopes in (
        EDITOR_SCOPES,
        ADMIN_SCOPES,
        ORG_OWNER_SCOPES,
        ORG_ADMIN_SCOPES,
        WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES,
    ):
        assert TRIGGER_SCOPES <= scopes


def test_workflows_do_not_manage_triggers() -> None:
    # No registry action reads or writes triggers.
    assert not WORKSPACE_OPERATIONAL_SCOPES & TRIGGER_SCOPES
