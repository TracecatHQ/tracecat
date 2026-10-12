"""Form scopes per role, service-account allowlist and executor scopes."""

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

ALL_FORM_SCOPES = frozenset(
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
INTAKE_FORM_SCOPES = frozenset({"form:read", "form:submit"})
VIEWER_FORM_SCOPES = INTAKE_FORM_SCOPES | {"form:submission:read"}
EDITOR_FORM_SCOPES = VIEWER_FORM_SCOPES | {"form:create", "form:update"}


def _form_scopes(scopes: frozenset[str]) -> frozenset[str]:
    return frozenset(scope for scope in scopes if scope.startswith("form:"))


@pytest.mark.parametrize(
    ("scopes", "expected"),
    [
        pytest.param(INTAKE_SCOPES, INTAKE_FORM_SCOPES, id="intake"),
        pytest.param(VIEWER_SCOPES, VIEWER_FORM_SCOPES, id="viewer"),
        pytest.param(EDITOR_SCOPES, EDITOR_FORM_SCOPES, id="editor"),
        pytest.param(ADMIN_SCOPES, ALL_FORM_SCOPES, id="admin"),
        pytest.param(ORG_ADMIN_SCOPES, ALL_FORM_SCOPES, id="org-admin"),
        pytest.param(ORG_OWNER_SCOPES, ALL_FORM_SCOPES, id="org-owner"),
        pytest.param(ORG_MEMBER_SCOPES, frozenset(), id="org-member"),
    ],
)
def test_form_scopes_per_role(scopes: frozenset[str], expected: frozenset[str]) -> None:
    assert _form_scopes(scopes) == expected


def test_intake_role_holds_only_form_scopes() -> None:
    assert INTAKE_SCOPES == INTAKE_FORM_SCOPES
    assert PRESET_ROLE_SCOPES["workspace-intake"] == INTAKE_SCOPES
    assert not is_org_level_role(INTAKE_SCOPES)


def test_intake_role_is_seeded_as_a_preset() -> None:
    role = PRESET_ROLE_DEFINITIONS["workspace-intake"]

    assert role.name == "Intake only"
    assert role.scopes == INTAKE_SCOPES


def test_every_form_scope_is_a_system_scope() -> None:
    definitions = {
        definition.name: definition
        for definition in SYSTEM_SCOPE_DEFINITIONS
        if definition.name.startswith("form:")
    }

    assert set(definitions) == ALL_FORM_SCOPES
    for name, definition in definitions.items():
        assert f"{definition.resource}:{definition.action}" == name


def test_service_accounts_can_be_assigned_every_form_scope() -> None:
    assert _form_scopes(WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES) == ALL_FORM_SCOPES
    assert _form_scopes(ORG_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES) == ALL_FORM_SCOPES


def test_executor_cannot_submit_or_delete_forms() -> None:
    expected = ALL_FORM_SCOPES - {"form:submit", "form:delete"}

    assert _form_scopes(WORKSPACE_OPERATIONAL_SCOPES) == expected
    for service_id in ("tracecat-executor", "tracecat-runner"):
        assert _form_scopes(SERVICE_PRINCIPAL_SCOPES[service_id]) == expected
