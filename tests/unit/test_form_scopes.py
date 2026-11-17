"""Form scopes per role, the Intake only preset, and who else holds them."""

import pytest

from tracecat.authz.scopes import (
    ADMIN_SCOPES,
    EDITOR_SCOPES,
    INTAKE_SCOPES,
    ORG_ADMIN_SCOPES,
    ORG_MEMBER_SCOPES,
    ORG_OWNER_SCOPES,
    PRESET_ROLE_SCOPES,
    SERVICE_PRINCIPAL_SCOPES,
    VIEWER_SCOPES,
    WORKSPACE_OPERATIONAL_SCOPES,
    is_org_level_role,
)
from tracecat.authz.seeding import PRESET_ROLE_DEFINITIONS, SYSTEM_SCOPE_DEFINITIONS
from tracecat.service_accounts.constants import (
    ORG_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES,
    WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES,
)

ALL = frozenset(
    {
        "form:read",
        "form:create",
        "form:update",
        "form:delete",
        "form:submit",
        "form:submission:read",
        "form:submission:delete",
    }
)
INTAKE = frozenset({"form:read", "form:submit"})
VIEWER = INTAKE | {"form:submission:read"}
# Workflows read and edit forms and manage submissions. They never submit or
# remove a form.
OPERATIONAL = ALL - {"form:submit", "form:delete"}


def _form_scopes(scopes: frozenset[str]) -> frozenset[str]:
    return frozenset(scope for scope in scopes if scope.startswith("form:"))


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        pytest.param(INTAKE_SCOPES, INTAKE, id="intake"),
        pytest.param(VIEWER_SCOPES, VIEWER, id="viewer"),
        pytest.param(
            EDITOR_SCOPES, VIEWER | {"form:create", "form:update"}, id="editor"
        ),
        pytest.param(ADMIN_SCOPES, ALL, id="admin"),
        pytest.param(ORG_ADMIN_SCOPES, ALL, id="org-admin"),
        pytest.param(ORG_OWNER_SCOPES, ALL, id="org-owner"),
        pytest.param(ORG_MEMBER_SCOPES, frozenset(), id="org-member"),
        pytest.param(WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES, ALL, id="ws-sa"),
        pytest.param(ORG_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES, ALL, id="org-sa"),
        pytest.param(WORKSPACE_OPERATIONAL_SCOPES, OPERATIONAL, id="operational"),
        pytest.param(
            SERVICE_PRINCIPAL_SCOPES["tracecat-executor"], OPERATIONAL, id="executor"
        ),
        pytest.param(
            SERVICE_PRINCIPAL_SCOPES["tracecat-runner"], OPERATIONAL, id="runner"
        ),
    ],
)
def test_form_scopes_per_holder(
    scopes: frozenset[str], expected: frozenset[str]
) -> None:
    assert _form_scopes(scopes) == expected


def test_intake_only_is_a_seeded_workspace_preset_with_form_scopes_only() -> None:
    role = PRESET_ROLE_DEFINITIONS["workspace-intake"]

    assert INTAKE_SCOPES == INTAKE
    assert role.name == "Intake only"
    assert role.scopes == PRESET_ROLE_SCOPES["workspace-intake"] == INTAKE_SCOPES
    assert not is_org_level_role(INTAKE_SCOPES)


def test_every_form_scope_is_a_system_scope() -> None:
    definitions = {
        d.name: d for d in SYSTEM_SCOPE_DEFINITIONS if d.name.startswith("form:")
    }

    assert set(definitions) == ALL
    for name, definition in definitions.items():
        assert f"{definition.resource}:{definition.action}" == name
