"""Unit tests for RBAC scope matching and controls."""

from __future__ import annotations

import pytest

from tracecat.auth.types import Role
from tracecat.authz.controls import (
    get_missing_scopes,
    has_all_scopes,
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
    VIEWER_SCOPES,
)
from tracecat.contexts import ctx_role
from tracecat.exceptions import ScopeDeniedError


def _set_role_with_scopes(scopes: frozenset[str]) -> None:
    """Helper to set ctx_role with the given scopes."""
    role = Role(type="user", service_id="tracecat-api", scopes=scopes)
    ctx_role.set(role)


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

    def test_viewer_includes_inbox_read(self):
        assert "inbox:read" in VIEWER_SCOPES

    def test_viewer_scopes_are_read_only(self):
        for scope in VIEWER_SCOPES:
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
        assert ORG_MEMBER_SCOPES == frozenset({"org:read", "org:member:read"})


class TestRequireScopeDecorator:
    """Tests for the @require_scope decorator."""

    def test_require_scope_passes_with_exact_scope(self):
        _set_role_with_scopes(frozenset({"workflow:read"}))

        @require_scope("workflow:read")
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_passes_with_wildcard(self):
        _set_role_with_scopes(frozenset({"workflow:*"}))

        @require_scope("workflow:read")
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_passes_with_superuser(self):
        _set_role_with_scopes(frozenset({"*"}))

        @require_scope("org:delete")
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_fails_without_scope(self):
        _set_role_with_scopes(frozenset({"case:read"}))

        @require_scope("workflow:read")
        def protected_func():
            return "success"

        with pytest.raises(ScopeDeniedError) as exc_info:
            protected_func()

        assert "workflow:read" in exc_info.value.required_scopes
        assert "workflow:read" in exc_info.value.missing_scopes

    def test_require_scope_multiple_all_required(self):
        _set_role_with_scopes(frozenset({"workflow:read", "workflow:execute"}))

        @require_scope("workflow:read", "workflow:execute", require_all=True)
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_multiple_missing_one(self):
        _set_role_with_scopes(frozenset({"workflow:read"}))

        @require_scope("workflow:read", "workflow:execute", require_all=True)
        def protected_func():
            return "success"

        with pytest.raises(ScopeDeniedError) as exc_info:
            protected_func()

        assert "workflow:execute" in exc_info.value.missing_scopes

    def test_require_scope_any_one_sufficient(self):
        _set_role_with_scopes(frozenset({"workflow:read"}))

        @require_scope("workflow:read", "workflow:execute", require_all=False)
        def protected_func():
            return "success"

        assert protected_func() == "success"

    def test_require_scope_any_none_present(self):
        _set_role_with_scopes(frozenset({"case:read"}))

        @require_scope("workflow:read", "workflow:execute", require_all=False)
        def protected_func():
            return "success"

        with pytest.raises(ScopeDeniedError):
            protected_func()

    def test_require_scope_fails_without_role(self):
        """Test that require_scope fails when ctx_role is None."""
        ctx_role.set(None)

        @require_scope("workflow:read")
        def protected_func():
            return "success"

        with pytest.raises(ScopeDeniedError) as exc_info:
            protected_func()

        assert "workflow:read" in exc_info.value.required_scopes

    @pytest.mark.anyio
    async def test_require_scope_async_function(self):
        _set_role_with_scopes(frozenset({"workflow:read"}))

        @require_scope("workflow:read")
        async def async_protected_func():
            return "async success"

        result = await async_protected_func()
        assert result == "async success"

    @pytest.mark.anyio
    async def test_require_scope_async_function_denied(self):
        _set_role_with_scopes(frozenset({"case:read"}))

        @require_scope("workflow:read")
        async def async_protected_func():
            return "async success"

        with pytest.raises(ScopeDeniedError):
            await async_protected_func()


class TestRequireActionScope:
    """Tests for the require_action_scope function."""

    def test_require_action_scope_with_exact_scope(self):
        """User with exact action scope can execute."""
        _set_role_with_scopes(frozenset({"action:core.http_request:execute"}))
        # Should not raise
        require_action_scope("core.http_request")

    def test_require_action_scope_with_global_wildcard(self):
        """Superuser with * scope can execute any action."""
        _set_role_with_scopes(frozenset({"*"}))
        require_action_scope("core.http_request")
        require_action_scope("tools.okta.list_users")

    def test_require_action_scope_with_action_wildcard(self):
        """User with action:*:execute can execute any action."""
        _set_role_with_scopes(frozenset({"action:*:execute"}))
        require_action_scope("core.http_request")
        require_action_scope("tools.okta.list_users")

    def test_require_action_scope_with_prefix_wildcard(self):
        """User with action:core.*:execute can execute core actions."""
        _set_role_with_scopes(frozenset({"action:core.*:execute"}))
        require_action_scope("core.http_request")
        require_action_scope("core.transform.forward")

        # Should fail for non-core actions
        with pytest.raises(ScopeDeniedError) as exc_info:
            require_action_scope("tools.okta.list_users")
        assert "action:tools.okta.list_users:execute" in exc_info.value.missing_scopes

    def test_require_action_scope_with_integration_wildcard(self):
        """User with action:tools.okta.*:execute can execute okta actions."""
        _set_role_with_scopes(frozenset({"action:tools.okta.*:execute"}))
        require_action_scope("tools.okta.list_users")
        require_action_scope("tools.okta.suspend_user")

        # Should fail for other integrations
        with pytest.raises(ScopeDeniedError):
            require_action_scope("tools.slack.send_message")

    def test_require_action_scope_denied(self):
        """User without action scope gets denied."""
        _set_role_with_scopes(frozenset({"workflow:execute"}))

        with pytest.raises(ScopeDeniedError) as exc_info:
            require_action_scope("core.http_request")

        assert exc_info.value.required_scopes == ["action:core.http_request:execute"]
        assert exc_info.value.missing_scopes == ["action:core.http_request:execute"]

    def test_require_action_scope_empty_scopes(self):
        """User with no scopes gets denied."""
        _set_role_with_scopes(frozenset())

        with pytest.raises(ScopeDeniedError):
            require_action_scope("core.http_request")

    def test_require_action_scope_multiple_scopes(self):
        """User with multiple action scopes can execute matching actions."""
        _set_role_with_scopes(
            frozenset(
                {
                    "action:core.*:execute",
                    "action:tools.okta.*:execute",
                    "workflow:execute",
                }
            )
        )
        require_action_scope("core.http_request")
        require_action_scope("tools.okta.list_users")

        # Should fail for non-matching actions
        with pytest.raises(ScopeDeniedError):
            require_action_scope("tools.slack.send_message")
