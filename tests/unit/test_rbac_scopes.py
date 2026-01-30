"""Unit tests for RBAC scope matching and controls."""

from __future__ import annotations

import pytest

from tracecat.authz.controls import (
    get_missing_scopes,
    has_all_scopes,
    has_any_scope,
    has_scope,
    require_scope,
    scope_matches,
    validate_scope_string,
)
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.authz.scopes import (
    ADMIN_SCOPES,
    EDITOR_SCOPES,
    ORG_ADMIN_SCOPES,
    ORG_MEMBER_SCOPES,
    ORG_OWNER_SCOPES,
    ORG_ROLE_SCOPES,
    PRESET_ROLE_SCOPES,
    VIEWER_SCOPES,
)
from tracecat.contexts import ctx_scopes
from tracecat.exceptions import ScopeDeniedError


class TestValidateScopeString:
    """Tests for scope string validation."""

    def test_valid_simple_scope(self):
        assert validate_scope_string("workflow:read") is True
        assert validate_scope_string("case:create") is True
        assert validate_scope_string("org:member:invite") is True

    def test_valid_scope_with_wildcard(self):
        assert validate_scope_string("workflow:*") is True
        assert validate_scope_string("action:*:execute") is True
        assert validate_scope_string("*") is True

    def test_valid_scope_with_special_chars(self):
        assert validate_scope_string("action:core.http_request:execute") is True
        assert validate_scope_string("action:tools.okta-client:execute") is True

    def test_invalid_scope_uppercase(self):
        assert validate_scope_string("Workflow:read") is False
        assert validate_scope_string("WORKFLOW:READ") is False

    def test_invalid_scope_spaces(self):
        assert validate_scope_string("workflow: read") is False
        assert validate_scope_string("workflow :read") is False

    def test_invalid_scope_other_patterns(self):
        assert validate_scope_string("workflow:?") is False
        assert validate_scope_string("workflow:[read]") is False


class TestScopeMatches:
    """Tests for scope matching with wildcards."""

    def test_exact_match(self):
        assert scope_matches("workflow:read", "workflow:read") is True
        assert scope_matches("workflow:read", "workflow:create") is False

    def test_global_wildcard(self):
        assert scope_matches("*", "workflow:read") is True
        assert scope_matches("*", "org:member:invite") is True
        assert scope_matches("*", "anything:here") is True

    def test_suffix_wildcard(self):
        assert scope_matches("workflow:*", "workflow:read") is True
        assert scope_matches("workflow:*", "workflow:create") is True
        assert scope_matches("workflow:*", "workflow:execute") is True
        assert scope_matches("workflow:*", "case:read") is False

    def test_middle_wildcard(self):
        assert scope_matches("action:*:execute", "action:core.http:execute") is True
        assert scope_matches("action:*:execute", "action:tools.okta:execute") is True
        assert scope_matches("action:*:execute", "action:tools.okta:read") is False

    def test_prefix_wildcard(self):
        assert (
            scope_matches("action:core.*:execute", "action:core.http:execute") is True
        )
        assert (
            scope_matches("action:core.*:execute", "action:core.transform:execute")
            is True
        )
        assert (
            scope_matches("action:core.*:execute", "action:tools.okta:execute") is False
        )

    def test_multiple_wildcards(self):
        # Multiple wildcards in a scope
        assert scope_matches("action:*.*:execute", "action:core.http:execute") is True
        assert scope_matches("*:*", "workflow:read") is True


class TestHasScope:
    """Tests for has_scope function."""

    def test_has_exact_scope(self):
        scopes = frozenset({"workflow:read", "case:create"})
        assert has_scope(scopes, "workflow:read") is True
        assert has_scope(scopes, "case:create") is True
        assert has_scope(scopes, "workflow:delete") is False

    def test_has_scope_via_wildcard(self):
        scopes = frozenset({"workflow:*", "case:read"})
        assert has_scope(scopes, "workflow:read") is True
        assert has_scope(scopes, "workflow:create") is True
        assert has_scope(scopes, "workflow:delete") is True
        assert has_scope(scopes, "case:read") is True
        assert has_scope(scopes, "case:create") is False

    def test_has_scope_global_wildcard(self):
        scopes = frozenset({"*"})
        assert has_scope(scopes, "workflow:read") is True
        assert has_scope(scopes, "org:delete") is True
        assert has_scope(scopes, "anything:here") is True

    def test_empty_scopes(self):
        scopes: frozenset[str] = frozenset()
        assert has_scope(scopes, "workflow:read") is False


class TestHasAllScopes:
    """Tests for has_all_scopes function."""

    def test_has_all_exact_scopes(self):
        scopes = frozenset({"workflow:read", "workflow:execute", "case:read"})
        assert has_all_scopes(scopes, {"workflow:read", "workflow:execute"}) is True
        assert has_all_scopes(scopes, {"workflow:read", "case:create"}) is False

    def test_has_all_via_wildcard(self):
        scopes = frozenset({"workflow:*"})
        assert has_all_scopes(scopes, {"workflow:read", "workflow:execute"}) is True
        assert has_all_scopes(scopes, {"workflow:read", "case:read"}) is False


class TestHasAnyScope:
    """Tests for has_any_scope function."""

    def test_has_any_exact_scope(self):
        scopes = frozenset({"workflow:read", "case:create"})
        assert has_any_scope(scopes, {"workflow:read", "workflow:delete"}) is True
        assert has_any_scope(scopes, {"org:delete", "secret:read"}) is False

    def test_has_any_via_wildcard(self):
        scopes = frozenset({"workflow:*"})
        assert has_any_scope(scopes, {"workflow:read", "case:read"}) is True
        assert has_any_scope(scopes, {"case:read", "org:read"}) is False


class TestGetMissingScopes:
    """Tests for get_missing_scopes function."""

    def test_no_missing_scopes(self):
        scopes = frozenset({"workflow:read", "workflow:execute"})
        missing = get_missing_scopes(scopes, {"workflow:read", "workflow:execute"})
        assert missing == set()

    def test_some_missing_scopes(self):
        scopes = frozenset({"workflow:read"})
        missing = get_missing_scopes(scopes, {"workflow:read", "workflow:execute"})
        assert missing == {"workflow:execute"}

    def test_all_missing_scopes(self):
        scopes = frozenset({"case:read"})
        missing = get_missing_scopes(scopes, {"workflow:read", "workflow:execute"})
        assert missing == {"workflow:read", "workflow:execute"}

    def test_wildcard_covers_scopes(self):
        scopes = frozenset({"workflow:*"})
        missing = get_missing_scopes(scopes, {"workflow:read", "workflow:execute"})
        assert missing == set()


class TestSystemRoleScopes:
    """Tests for system role scope definitions."""

    def test_viewer_scopes_are_read_only(self):
        for scope in VIEWER_SCOPES:
            # Viewer should only have read scopes (no create, update, delete, execute)
            action = scope.split(":")[-1]
            assert action in ("read", "member:read"), f"Unexpected scope: {scope}"

    def test_editor_includes_viewer(self):
        assert VIEWER_SCOPES.issubset(EDITOR_SCOPES)

    def test_admin_includes_editor(self):
        assert EDITOR_SCOPES.issubset(ADMIN_SCOPES)

    def test_system_role_mapping(self):
        assert PRESET_ROLE_SCOPES[WorkspaceRole.VIEWER] == VIEWER_SCOPES
        assert PRESET_ROLE_SCOPES[WorkspaceRole.EDITOR] == EDITOR_SCOPES
        assert PRESET_ROLE_SCOPES[WorkspaceRole.ADMIN] == ADMIN_SCOPES


class TestOrgRoleScopes:
    """Tests for organization role scope definitions."""

    def test_owner_has_org_delete(self):
        assert "org:delete" in ORG_OWNER_SCOPES
        assert "org:delete" not in ORG_ADMIN_SCOPES
        assert "org:delete" not in ORG_MEMBER_SCOPES

    def test_owner_has_billing_manage(self):
        assert "org:billing:manage" in ORG_OWNER_SCOPES
        assert "org:billing:manage" not in ORG_ADMIN_SCOPES

    def test_admin_has_billing_read(self):
        assert "org:billing:read" in ORG_ADMIN_SCOPES

    def test_member_has_minimal_scopes(self):
        assert ORG_MEMBER_SCOPES == frozenset({"org:read", "org:member:read"})

    def test_org_role_mapping(self):
        assert ORG_ROLE_SCOPES[OrgRole.OWNER] == ORG_OWNER_SCOPES
        assert ORG_ROLE_SCOPES[OrgRole.ADMIN] == ORG_ADMIN_SCOPES
        assert ORG_ROLE_SCOPES[OrgRole.MEMBER] == ORG_MEMBER_SCOPES


class TestRequireScopeDecorator:
    """Tests for the @require_scope decorator."""

    def test_require_scope_passes_with_exact_scope(self):
        ctx_scopes.set(frozenset({"workflow:read"}))

        @require_scope("workflow:read")
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_passes_with_wildcard(self):
        ctx_scopes.set(frozenset({"workflow:*"}))

        @require_scope("workflow:read")
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_passes_with_superuser(self):
        ctx_scopes.set(frozenset({"*"}))

        @require_scope("org:delete")
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_fails_without_scope(self):
        ctx_scopes.set(frozenset({"case:read"}))

        @require_scope("workflow:read")
        def protected_func():
            return "success"

        with pytest.raises(ScopeDeniedError) as exc_info:
            protected_func()

        assert "workflow:read" in exc_info.value.required_scopes
        assert "workflow:read" in exc_info.value.missing_scopes

    def test_require_scope_multiple_all_required(self):
        ctx_scopes.set(frozenset({"workflow:read", "workflow:execute"}))

        @require_scope("workflow:read", "workflow:execute", require_all=True)
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_multiple_missing_one(self):
        ctx_scopes.set(frozenset({"workflow:read"}))

        @require_scope("workflow:read", "workflow:execute", require_all=True)
        def protected_func():
            return "success"

        with pytest.raises(ScopeDeniedError) as exc_info:
            protected_func()

        assert "workflow:execute" in exc_info.value.missing_scopes

    def test_require_scope_any_one_sufficient(self):
        ctx_scopes.set(frozenset({"workflow:read"}))

        @require_scope("workflow:read", "workflow:execute", require_all=False)
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_any_none_present(self):
        ctx_scopes.set(frozenset({"case:read"}))

        @require_scope("workflow:read", "workflow:execute", require_all=False)
        def protected_func():
            return "success"

        with pytest.raises(ScopeDeniedError):
            protected_func()

    @pytest.mark.anyio
    async def test_require_scope_async_function(self):
        ctx_scopes.set(frozenset({"workflow:read"}))

        @require_scope("workflow:read")
        async def async_protected_func():
            return "async success"

        result = await async_protected_func()
        assert result == "async success"

    @pytest.mark.anyio
    async def test_require_scope_async_function_denied(self):
        ctx_scopes.set(frozenset({"case:read"}))

        @require_scope("workflow:read")
        async def async_protected_func():
            return "async success"

        with pytest.raises(ScopeDeniedError):
            await async_protected_func()
