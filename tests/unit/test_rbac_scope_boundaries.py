"""Medium priority RBAC tests for scope boundaries.

Tests wildcard matching edge cases, error semantics, and guardrails.
"""

from __future__ import annotations

import pytest

from tracecat.authz.controls import (
    has_scope,
    require_action_scope,
    require_scope,
    scope_matches,
    validate_scope_string,
)
from tracecat.authz.scopes import ADMIN_SCOPES, EDITOR_SCOPES, VIEWER_SCOPES
from tracecat.contexts import ctx_scopes
from tracecat.exceptions import ScopeDeniedError

# =============================================================================
# Wildcard and Matching Edge Cases
# =============================================================================


class TestWildcardBoundaries:
    """Test that wildcard matching respects proper boundaries."""

    def test_action_core_prefix_boundary_no_overmatch(self):
        """action:core.*:execute does NOT match action:coreevil.foo:execute.

        The dot in core.* should act as a boundary, not matching 'coreevil'.
        """
        # action:core.*:execute should match core.http, core.transform, etc.
        assert (
            scope_matches("action:core.*:execute", "action:core.http:execute") is True
        )
        assert (
            scope_matches("action:core.*:execute", "action:core.transform:execute")
            is True
        )

        # Should NOT match things that start with 'core' but aren't 'core.*'
        # Note: With the current regex implementation, * matches any sequence
        # This test documents the expected behavior
        # action:core.*:execute translates to action:core\..*:execute regex
        # which would match action:core.evil:execute but not action:coreevil:execute
        assert (
            scope_matches("action:core.*:execute", "action:coreevil.foo:execute")
            is False
        )

    def test_action_wildcard_execute_does_not_grant_non_action_scopes(self):
        """action:*:execute cannot access workflow:*, secret:*, RBAC endpoints."""
        scopes = frozenset({"action:*:execute"})

        # Can execute any action
        assert has_scope(scopes, "action:core.http:execute") is True
        assert has_scope(scopes, "action:tools.okta:execute") is True

        # Cannot access non-action resources
        assert has_scope(scopes, "workflow:read") is False
        assert has_scope(scopes, "workflow:execute") is False
        assert has_scope(scopes, "secret:read") is False
        assert has_scope(scopes, "org:rbac:read") is False

    def test_scope_matching_exact_colon_segments(self):
        """workspace:member:read is NOT satisfied by workspace:read (and vice versa)."""
        scopes = frozenset({"workspace:read"})

        # workspace:read should NOT satisfy workspace:member:read
        assert has_scope(scopes, "workspace:member:read") is False

        # And vice versa
        scopes_member = frozenset({"workspace:member:read"})
        assert has_scope(scopes_member, "workspace:read") is False

    def test_scope_matching_no_prefix_substring(self):
        """workflow:read does NOT satisfy workflow:read_all or workflow:reader."""
        scopes = frozenset({"workflow:read"})

        # Exact match works
        assert has_scope(scopes, "workflow:read") is True

        # Prefix should not match longer scope names
        assert has_scope(scopes, "workflow:read_all") is False
        assert has_scope(scopes, "workflow:reader") is False

    def test_global_wildcard_matches_everything(self):
        """* scope matches absolutely everything."""
        scopes = frozenset({"*"})

        assert has_scope(scopes, "workflow:read") is True
        assert has_scope(scopes, "org:delete") is True
        assert has_scope(scopes, "anything:here:deeply:nested") is True
        assert has_scope(scopes, "action:tools.okta.list_users:execute") is True

    def test_suffix_wildcard_requires_prefix_match(self):
        """workflow:* only matches workflow:something, not workflow alone."""
        scopes = frozenset({"workflow:*"})

        assert has_scope(scopes, "workflow:read") is True
        assert has_scope(scopes, "workflow:execute") is True
        assert has_scope(scopes, "workflow:delete") is True

        # Does not match other resources
        assert has_scope(scopes, "case:read") is False
        assert has_scope(scopes, "secret:read") is False


class TestScopeValidation:
    """Test scope string validation and edge cases."""

    def test_scope_parser_rejects_whitespace(self):
        """Leading/trailing whitespace in scopes should be invalid."""
        assert validate_scope_string(" workflow:read") is False
        assert validate_scope_string("workflow:read ") is False
        assert validate_scope_string(" workflow:read ") is False
        assert validate_scope_string("workflow: read") is False

    def test_scope_parser_rejects_uppercase(self):
        """Uppercase letters should be rejected."""
        assert validate_scope_string("Workflow:read") is False
        assert validate_scope_string("workflow:Read") is False
        assert validate_scope_string("WORKFLOW:READ") is False

    def test_scope_parser_accepts_valid_patterns(self):
        """Valid scope patterns should be accepted."""
        assert validate_scope_string("workflow:read") is True
        assert validate_scope_string("workflow:*") is True
        assert validate_scope_string("action:*:execute") is True
        assert validate_scope_string("action:core.http:execute") is True
        assert validate_scope_string("action:tools.okta-client:execute") is True
        assert validate_scope_string("*") is True


# =============================================================================
# Error Semantics
# =============================================================================


class TestErrorSemantics:
    """Test proper error responses for authorization failures."""

    def test_insufficient_scope_raises_scope_denied_error(self):
        """Missing scope should raise ScopeDeniedError with details."""
        scopes = frozenset({"case:read"})
        token = ctx_scopes.set(scopes)

        try:

            @require_scope("workflow:read")
            def protected_func():
                return "success"

            with pytest.raises(ScopeDeniedError) as exc_info:
                protected_func()

            # Verify error details
            assert exc_info.value.required_scopes == ["workflow:read"]
            assert exc_info.value.missing_scopes == ["workflow:read"]
        finally:
            ctx_scopes.reset(token)

    def test_scope_denied_error_lists_all_missing(self):
        """When multiple scopes are missing, all should be listed."""
        scopes = frozenset({"case:read"})
        token = ctx_scopes.set(scopes)

        try:

            @require_scope("workflow:read", "workflow:execute", require_all=True)
            def multi_scope_func():
                return "success"

            with pytest.raises(ScopeDeniedError) as exc_info:
                multi_scope_func()

            # Both scopes should be missing
            assert "workflow:read" in exc_info.value.missing_scopes
            assert "workflow:execute" in exc_info.value.missing_scopes
        finally:
            ctx_scopes.reset(token)

    def test_require_scope_any_error_lists_all_required(self):
        """When require_all=False and none present, all required listed."""
        scopes = frozenset({"case:read"})
        token = ctx_scopes.set(scopes)

        try:

            @require_scope("workflow:read", "workflow:execute", require_all=False)
            def any_scope_func():
                return "success"

            with pytest.raises(ScopeDeniedError) as exc_info:
                any_scope_func()

            # All required scopes should be listed
            assert "workflow:read" in exc_info.value.required_scopes
            assert "workflow:execute" in exc_info.value.required_scopes
        finally:
            ctx_scopes.reset(token)


class TestActionScopeErrors:
    """Test action scope error handling."""

    def test_action_scope_denied_error_format(self):
        """Action scope denial should have proper error format."""
        scopes = frozenset({"workflow:execute"})  # No action scopes
        token = ctx_scopes.set(scopes)

        try:
            with pytest.raises(ScopeDeniedError) as exc_info:
                require_action_scope("core.http_request")

            assert exc_info.value.required_scopes == [
                "action:core.http_request:execute"
            ]
            assert exc_info.value.missing_scopes == ["action:core.http_request:execute"]
        finally:
            ctx_scopes.reset(token)

    def test_action_scope_with_dots_in_name(self):
        """Action scopes with dots in name should work correctly."""
        scopes = frozenset({"action:tools.okta.list_users:execute"})
        token = ctx_scopes.set(scopes)

        try:
            # This should pass
            require_action_scope("tools.okta.list_users")
        finally:
            ctx_scopes.reset(token)


# =============================================================================
# Scope Decision Isolation
# =============================================================================


class TestScopeDecisionIsolation:
    """Test that scope decisions are properly isolated per request."""

    def test_scope_decision_uses_current_context(self):
        """Each scope check should use current context, not cached."""
        # First context: allowed
        scopes_1 = frozenset({"workflow:read"})
        token_1 = ctx_scopes.set(scopes_1)

        @require_scope("workflow:read")
        def read_workflow():
            return "read"

        assert read_workflow() == "read"
        ctx_scopes.reset(token_1)

        # Second context: denied (different scopes)
        scopes_2 = frozenset({"case:read"})
        token_2 = ctx_scopes.set(scopes_2)

        with pytest.raises(ScopeDeniedError):
            read_workflow()

        ctx_scopes.reset(token_2)

    def test_same_user_different_workspace_different_decision(self):
        """Same check can have different results in different contexts."""
        # Workspace A context: has workflow:execute
        ws_a_scopes = frozenset({"workflow:read", "workflow:execute"})
        token_a = ctx_scopes.set(ws_a_scopes)

        @require_scope("workflow:execute")
        def execute_workflow():
            return "executed"

        assert execute_workflow() == "executed"
        ctx_scopes.reset(token_a)

        # Workspace B context: only has read
        ws_b_scopes = frozenset({"workflow:read"})
        token_b = ctx_scopes.set(ws_b_scopes)

        with pytest.raises(ScopeDeniedError):
            execute_workflow()

        ctx_scopes.reset(token_b)


# =============================================================================
# Role Scope Hierarchy Verification
# =============================================================================


class TestRoleScopeHierarchy:
    """Verify that role scope hierarchies are properly defined."""

    def test_viewer_is_subset_of_editor(self):
        """VIEWER scopes should be subset of EDITOR scopes."""
        assert VIEWER_SCOPES.issubset(EDITOR_SCOPES)

    def test_editor_is_subset_of_admin(self):
        """EDITOR scopes should be subset of ADMIN scopes."""
        assert EDITOR_SCOPES.issubset(ADMIN_SCOPES)

    def test_viewer_has_only_read_scopes(self):
        """VIEWER should only have read-related scopes."""
        for scope in VIEWER_SCOPES:
            # Viewer scopes should end in :read or :member:read
            parts = scope.split(":")
            action = parts[-1]
            assert action == "read", f"Viewer has non-read scope: {scope}"

    def test_editor_can_create_and_update_but_not_delete(self):
        """EDITOR should have create/update but limited delete."""
        # Editor should have create and update for workflows
        assert "workflow:create" in EDITOR_SCOPES
        assert "workflow:update" in EDITOR_SCOPES

        # Editor should NOT have workflow:delete
        assert "workflow:delete" not in EDITOR_SCOPES

    def test_admin_can_delete(self):
        """ADMIN should have delete permissions."""
        assert "workflow:delete" in ADMIN_SCOPES
        assert "case:delete" in ADMIN_SCOPES
        assert "secret:delete" in ADMIN_SCOPES


# =============================================================================
# Action Scope Boundaries
# =============================================================================


class TestActionScopeBoundaries:
    """Test action scope matching boundaries."""

    def test_action_execute_scopes_resolve_correctly(self):
        """Test action scope resolution with different patterns."""
        # No action scopes -> denies all actions
        no_scopes = frozenset({"workflow:execute"})
        assert has_scope(no_scopes, "action:core.http:execute") is False

        # action:core.*:execute -> allows core, denies non-core
        core_scopes = frozenset({"action:core.*:execute"})
        assert has_scope(core_scopes, "action:core.http:execute") is True
        assert has_scope(core_scopes, "action:core.transform:execute") is True
        assert has_scope(core_scopes, "action:tools.okta:execute") is False

        # action:*:execute -> allows all actions
        all_action_scopes = frozenset({"action:*:execute"})
        assert has_scope(all_action_scopes, "action:core.http:execute") is True
        assert has_scope(all_action_scopes, "action:tools.okta:execute") is True
        assert has_scope(all_action_scopes, "action:integrations.slack:execute") is True

    def test_integration_wildcard_scopes(self):
        """Test integration-specific wildcard patterns."""
        # action:tools.okta.*:execute -> only okta actions
        okta_scopes = frozenset({"action:tools.okta.*:execute"})
        assert has_scope(okta_scopes, "action:tools.okta.list_users:execute") is True
        assert has_scope(okta_scopes, "action:tools.okta.suspend_user:execute") is True
        assert (
            has_scope(okta_scopes, "action:tools.slack.send_message:execute") is False
        )

    def test_multiple_action_scopes_union(self):
        """Multiple action scopes combine as union."""
        scopes = frozenset(
            {
                "action:core.*:execute",
                "action:tools.okta.*:execute",
            }
        )

        # Core actions allowed
        assert has_scope(scopes, "action:core.http:execute") is True

        # Okta actions allowed
        assert has_scope(scopes, "action:tools.okta.list_users:execute") is True

        # Other tools not allowed
        assert has_scope(scopes, "action:tools.slack.send_message:execute") is False
