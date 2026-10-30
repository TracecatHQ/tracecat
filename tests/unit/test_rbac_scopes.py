"""Unit tests for RBAC scope matching and controls."""

from __future__ import annotations

import pytest

from tracecat.auth.types import Role
from tracecat.authz.controls import (
    ensure_can_grant_scopes,
    get_missing_scopes,
    has_any_scope,
    has_scope,
    require_action_scope,
    require_scope,
    scope_matches,
    validate_scope_string,
)
from tracecat.authz.scopes import (
    ADMIN_SCOPES,
    EDITOR_SCOPES,
    ORG_ADMIN_SCOPES,
    ORG_MEMBER_SCOPES,
    ORG_OWNER_SCOPES,
    PRESET_ROLE_SCOPES,
    SERVICE_PRINCIPAL_SCOPES,
    VIEWER_SCOPES,
    WORKSPACE_OPERATIONAL_SCOPES,
)
from tracecat.contexts import ctx_role
from tracecat.exceptions import ScopeDeniedError, TracecatAuthorizationError


@pytest.fixture(autouse=True)
def _reset_ctx_role():  # pyright: ignore[reportUnusedFunction]
    """Ensure role context is isolated per test in this module."""
    token = ctx_role.set(None)
    try:
        yield
    finally:
        ctx_role.reset(token)


def _set_role_with_scopes(scopes: frozenset[str]) -> None:
    """Helper to set ctx_role with the given scopes."""
    role = Role(type="user", service_id="tracecat-api", scopes=scopes)
    ctx_role.set(role)


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("workflow:read", True),
        ("case:create", True),
        ("org:member:invite", True),
        ("workflow:*", True),
        ("action:*:execute", True),
        ("*", True),
        ("action:core.http_request:execute", True),
        ("action:tools.okta-client:execute", True),
        ("Workflow:read", False),
        ("WORKFLOW:READ", False),
        ("workflow: read", False),
        ("workflow :read", False),
        ("workflow:?", False),
        ("workflow:[read]", False),
    ],
)
def test_validate_scope_string(scope: str, expected: bool) -> None:
    assert validate_scope_string(scope) is expected


@pytest.mark.parametrize(
    ("granted", "required", "expected"),
    [
        ("workflow:read", "workflow:read", True),
        ("workflow:read", "workflow:create", False),
        ("*", "workflow:read", True),
        ("*", "org:member:invite", True),
        ("*", "anything:here", True),
        ("workflow:*", "workflow:read", True),
        ("workflow:*", "workflow:create", True),
        ("workflow:*", "workflow:execute", True),
        ("workflow:*", "case:read", False),
        ("action:*:execute", "action:core.http:execute", True),
        ("action:*:execute", "action:tools.okta:execute", True),
        ("action:*:execute", "action:tools.okta:read", False),
        ("action:core.*:execute", "action:core.http:execute", True),
        ("action:core.*:execute", "action:core.transform:execute", True),
        ("action:core.*:execute", "action:tools.okta:execute", False),
        ("action:*.*:execute", "action:core.http:execute", True),
        ("*:*", "workflow:read", True),
    ],
)
def test_scope_matches(granted: str, required: str, expected: bool) -> None:
    assert scope_matches(granted, required) is expected


class TestScopeImplication:
    """Tests for scope implication (update implies read)."""

    def test_update_implies_read(self):
        assert scope_matches("workflow:update", "workflow:read") is True
        assert scope_matches("integration:update", "integration:read") is True
        assert scope_matches("case:update", "case:read") is True

    def test_update_implies_read_nested_resource(self):
        assert scope_matches("org:settings:update", "org:settings:read") is True
        assert scope_matches("org:member:update", "org:member:read") is True

    def test_create_does_not_imply_read(self):
        assert scope_matches("workflow:create", "workflow:read") is False

    def test_delete_does_not_imply_read(self):
        assert scope_matches("workflow:delete", "workflow:read") is False

    def test_execute_does_not_imply_read(self):
        assert scope_matches("workflow:execute", "workflow:read") is False

    def test_update_does_not_imply_other_actions(self):
        assert scope_matches("workflow:update", "workflow:create") is False
        assert scope_matches("workflow:update", "workflow:delete") is False
        assert scope_matches("workflow:update", "workflow:execute") is False

    def test_update_does_not_cross_resources(self):
        assert scope_matches("workflow:update", "case:read") is False
        assert scope_matches("integration:update", "workflow:read") is False

    def test_has_scope_via_implication(self):
        scopes = frozenset({"workflow:update"})
        assert has_scope(scopes, "workflow:read") is True
        assert has_scope(scopes, "workflow:update") is True
        assert has_scope(scopes, "workflow:delete") is False

    def test_get_missing_scopes_with_implication(self):
        scopes = frozenset({"workflow:update"})
        missing = get_missing_scopes(scopes, {"workflow:read", "workflow:update"})
        assert missing == set()

    def test_wildcard_not_affected_by_implication(self):
        # Wildcards already cover everything, implication shouldn't interfere
        assert scope_matches("workflow:*", "workflow:read") is True
        assert scope_matches("workflow:*", "workflow:update") is True


@pytest.mark.parametrize(
    ("scopes", "required", "expected"),
    [
        ({"workflow:read", "case:create"}, "workflow:read", True),
        ({"workflow:read", "case:create"}, "case:create", True),
        ({"workflow:read", "case:create"}, "workflow:delete", False),
        ({"workflow:*", "case:read"}, "workflow:read", True),
        ({"workflow:*", "case:read"}, "workflow:create", True),
        ({"workflow:*", "case:read"}, "workflow:delete", True),
        ({"workflow:*", "case:read"}, "case:read", True),
        ({"workflow:*", "case:read"}, "case:create", False),
        ({"*"}, "workflow:read", True),
        ({"*"}, "org:delete", True),
        ({"*"}, "anything:here", True),
        (set(), "workflow:read", False),
    ],
)
def test_has_scope(scopes: set[str], required: str, expected: bool) -> None:
    assert has_scope(frozenset(scopes), required) is expected


@pytest.mark.parametrize(
    ("scopes", "required", "expected"),
    [
        ({"workflow:read", "case:create"}, {"workflow:read", "workflow:delete"}, True),
        ({"workflow:read", "case:create"}, {"org:delete", "secret:read"}, False),
        ({"workflow:*"}, {"workflow:read", "case:read"}, True),
        ({"workflow:*"}, {"case:read", "org:read"}, False),
    ],
)
def test_has_any_scope(scopes: set[str], required: set[str], expected: bool) -> None:
    assert has_any_scope(frozenset(scopes), required) is expected


@pytest.mark.parametrize(
    ("scopes", "missing"),
    [
        ({"workflow:read", "workflow:execute"}, set()),
        ({"workflow:read"}, {"workflow:execute"}),
        ({"case:read"}, {"workflow:read", "workflow:execute"}),
        ({"workflow:*"}, set()),
    ],
)
def test_get_missing_scopes(scopes: set[str], missing: set[str]) -> None:
    required = {"workflow:read", "workflow:execute"}
    assert get_missing_scopes(frozenset(scopes), required) == missing


class TestEnsureCanGrantScopes:
    """Tests for the scope-ceiling check, including wildcard semantics.

    Wildcard matching is inherited from ``has_scope``; these pin the ceiling
    behavior on both sides of the wildcard so a change to ``has_scope``
    semantics cannot silently open an escalation path.
    """

    def test_exact_match_allowed(self):
        ensure_can_grant_scopes(frozenset({"org:member:read"}), ["org:member:read"])

    def test_wildcard_granter_covers_concrete_scope(self):
        ensure_can_grant_scopes(frozenset({"org:*"}), ["org:owner:assign"])

    def test_concrete_granter_cannot_grant_wildcard_scope(self):
        with pytest.raises(
            TracecatAuthorizationError,
            match=r"Cannot grant scopes not held by the caller: org:\*",
        ):
            ensure_can_grant_scopes(
                frozenset({"org:read", "org:owner:assign"}), ["org:*"]
            )

    def test_empty_granter_denied(self):
        with pytest.raises(TracecatAuthorizationError):
            ensure_can_grant_scopes(frozenset(), ["org:member:read"])


class TestSystemRoleScopes:
    """Tests for system role scope definitions."""

    def test_scim_management_defaults_to_org_administrators(self):
        assert "org:scim:manage" in ORG_OWNER_SCOPES
        assert "org:scim:manage" in ORG_ADMIN_SCOPES
        assert "org:scim:manage" not in ORG_MEMBER_SCOPES | ADMIN_SCOPES

    def test_viewer_includes_inbox_read(self):
        assert "inbox:read" in VIEWER_SCOPES

    def test_viewer_scopes_are_read_only(self):
        # Submitting a form is intake, not editing: viewers may do it.
        for scope in VIEWER_SCOPES - {"form:submit"}:
            # Viewer should only have read scopes (no create, update, delete, execute)
            action = scope.split(":")[-1]
            assert action in ("read", "member:read"), f"Unexpected scope: {scope}"

    def test_editor_includes_viewer(self):
        assert VIEWER_SCOPES.issubset(EDITOR_SCOPES)

    def test_admin_includes_editor(self):
        assert EDITOR_SCOPES.issubset(ADMIN_SCOPES)

    def test_preset_role_mapping(self):
        assert PRESET_ROLE_SCOPES["workspace-viewer"] == VIEWER_SCOPES
        assert PRESET_ROLE_SCOPES["workspace-editor"] == EDITOR_SCOPES
        assert PRESET_ROLE_SCOPES["workspace-admin"] == ADMIN_SCOPES
        assert PRESET_ROLE_SCOPES["organization-owner"] == ORG_OWNER_SCOPES
        assert PRESET_ROLE_SCOPES["organization-admin"] == ORG_ADMIN_SCOPES
        assert PRESET_ROLE_SCOPES["organization-member"] == ORG_MEMBER_SCOPES


class TestOrgRoleScopes:
    """Tests for organization role scope definitions."""

    def test_owner_has_org_delete(self):
        assert "org:delete" in ORG_OWNER_SCOPES
        assert "org:delete" not in ORG_ADMIN_SCOPES
        assert "org:delete" not in ORG_MEMBER_SCOPES

    def test_owner_has_billing_update(self):
        assert "org:billing:update" in ORG_OWNER_SCOPES
        assert "org:billing:update" not in ORG_ADMIN_SCOPES

    def test_admin_has_billing_read(self):
        assert "org:billing:read" in ORG_ADMIN_SCOPES

    def test_member_has_minimal_scopes(self):
        assert ORG_MEMBER_SCOPES == frozenset(
            {
                "org:read",
                "org:member:read",
                "org:registry:read",
                "org:secret:read",
            }
        )

    def test_member_floor_grants_no_agent_scopes(self):
        """Agent access comes only from workspace roles, never the member floor."""
        assert not {scope for scope in ORG_MEMBER_SCOPES if scope.startswith("agent:")}

    def test_viewer_reads_agents_and_editor_runs_them(self):
        """Viewers get agent:read only; editors and admins also get agent:execute."""
        assert "agent:read" in VIEWER_SCOPES
        assert "agent:execute" not in VIEWER_SCOPES
        assert "agent:execute" in EDITOR_SCOPES
        assert "agent:execute" in ADMIN_SCOPES

    def test_editors_push_but_only_admins_pull_git_sync(self):
        """Pulling overwrites workspace resources, so editors can only push."""
        assert "workspace_sync:push" in EDITOR_SCOPES
        assert "workspace_sync:pull" not in EDITOR_SCOPES
        for scopes in (ADMIN_SCOPES, ORG_ADMIN_SCOPES, ORG_OWNER_SCOPES):
            assert {"workspace_sync:push", "workspace_sync:pull"} <= scopes
        for scopes in PRESET_ROLE_SCOPES.values():
            assert not {"workflow:sync", "workspace_sync:sync"} & scopes


_BOTH = ("workflow:read", "workflow:execute")


@pytest.mark.parametrize(
    ("scopes", "required", "require_all", "allowed", "expected_missing"),
    [
        ({"workflow:read"}, ("workflow:read",), True, True, None),
        ({"workflow:*"}, ("workflow:read",), True, True, None),
        ({"*"}, ("org:delete",), True, True, None),
        ({"case:read"}, ("workflow:read",), True, False, "workflow:read"),
        ({"workflow:read", "workflow:execute"}, _BOTH, True, True, None),
        ({"workflow:read"}, _BOTH, True, False, "workflow:execute"),
        ({"workflow:read"}, _BOTH, False, True, None),
        ({"case:read"}, _BOTH, False, False, None),
    ],
)
def test_require_scope(
    scopes: set[str],
    required: tuple[str, ...],
    require_all: bool,
    allowed: bool,
    expected_missing: str | None,
) -> None:
    _set_role_with_scopes(frozenset(scopes))

    @require_scope(*required, require_all=require_all)
    def protected_func():
        return "success"

    if allowed:
        assert protected_func() == "success"
        return
    with pytest.raises(ScopeDeniedError) as exc_info:
        protected_func()
    if expected_missing is not None:
        assert expected_missing in exc_info.value.required_scopes
        assert expected_missing in exc_info.value.missing_scopes


def test_require_scope_fails_without_role() -> None:
    ctx_role.set(None)

    @require_scope("workflow:read")
    def protected_func():
        return "success"

    with pytest.raises(ScopeDeniedError) as exc_info:
        protected_func()

    assert "workflow:read" in exc_info.value.required_scopes


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("scopes", "allowed"), [({"workflow:read"}, True), ({"case:read"}, False)]
)
async def test_require_scope_async_function(scopes: set[str], allowed: bool) -> None:
    _set_role_with_scopes(frozenset(scopes))

    @require_scope("workflow:read")
    async def async_protected_func():
        return "async success"

    if allowed:
        assert await async_protected_func() == "async success"
    else:
        with pytest.raises(ScopeDeniedError):
            await async_protected_func()


_CORE_OKTA = {
    "action:core.*:execute",
    "action:tools.okta.*:execute",
    "workflow:execute",
}


@pytest.mark.parametrize(
    ("scopes", "action", "allowed"),
    [
        ({"action:core.http_request:execute"}, "core.http_request", True),
        ({"*"}, "core.http_request", True),
        ({"*"}, "tools.okta.list_users", True),
        ({"action:*:execute"}, "core.http_request", True),
        ({"action:*:execute"}, "tools.okta.list_users", True),
        ({"action:core.*:execute"}, "core.http_request", True),
        ({"action:core.*:execute"}, "core.transform.forward", True),
        ({"action:core.*:execute"}, "tools.okta.list_users", False),
        ({"action:tools.okta.*:execute"}, "tools.okta.list_users", True),
        ({"action:tools.okta.*:execute"}, "tools.okta.suspend_user", True),
        ({"action:tools.okta.*:execute"}, "tools.slack.send_message", False),
        ({"workflow:execute"}, "core.http_request", False),
        (set(), "core.http_request", False),
        (_CORE_OKTA, "core.http_request", True),
        (_CORE_OKTA, "tools.okta.list_users", True),
        (_CORE_OKTA, "tools.slack.send_message", False),
    ],
)
def test_require_action_scope(scopes: set[str], action: str, allowed: bool) -> None:
    _set_role_with_scopes(frozenset(scopes))

    if allowed:
        require_action_scope(action)
        return
    with pytest.raises(ScopeDeniedError) as exc_info:
        require_action_scope(action)
    assert exc_info.value.required_scopes == [f"action:{action}:execute"]
    assert exc_info.value.missing_scopes == [f"action:{action}:execute"]


class TestServicePrincipalScopes:
    """Tests for service principal scope definitions.

    Regression: the LLM gateway needs org:secret:read to fetch provider
    credentials for Workspace Chat via SecretsService.get_org_secret_by_name().
    Without it, Workspace Chat fails with ScopeDeniedError for all users.
    """

    def test_llm_gateway_has_org_secret_read(self):
        """LLM gateway must have org:secret:read for provider credential lookup."""
        assert "org:secret:read" in SERVICE_PRINCIPAL_SCOPES["tracecat-llm-gateway"]

    def test_all_service_principals_have_org_secret_read(self):
        """All service principals inherit org:secret:read from WORKSPACE_OPERATIONAL_SCOPES."""
        assert "org:secret:read" in WORKSPACE_OPERATIONAL_SCOPES
        for service_id, scopes in SERVICE_PRINCIPAL_SCOPES.items():
            assert "org:secret:read" in scopes, (
                f"Service principal {service_id!r} is missing org:secret:read"
            )

    def test_llm_gateway_has_agent_read(self):
        """LLM gateway needs agent:read for the get_provider_credentials call chain."""
        assert "agent:read" in SERVICE_PRINCIPAL_SCOPES["tracecat-llm-gateway"]

    def test_llm_gateway_has_secret_read(self):
        """LLM gateway needs secret:read for workspace credential lookup (case chat path)."""
        assert "secret:read" in SERVICE_PRINCIPAL_SCOPES["tracecat-llm-gateway"]
