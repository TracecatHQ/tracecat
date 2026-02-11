"""Tests for tracecat/validation/service.py.

Tests the multi-tier validation pipeline: entrypoint validation, expression
validation, action argument validation, and secret validation.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement, DSLConfig
from tracecat.expressions.common import ExprType
from tracecat.validation.service import (
    get_effective_environment,
    validate_dsl,
    validate_dsl_actions,
    validate_dsl_entrypoint,
    validate_dsl_expressions,
    validate_entrypoint_expects,
)

pytestmark = pytest.mark.usefixtures("db")


# --- Helpers ---


def _make_dsl_input(
    actions: list[ActionStatement] | None = None,
    entrypoint_expects: dict[str, Any] | None = None,
    environment: str = "default",
) -> DSLInput:
    """Create a minimal DSLInput for testing."""
    return DSLInput(
        title="Test Workflow",
        description="Test workflow for validation",
        entrypoint=DSLEntrypoint(
            ref="action_a",
            expects=entrypoint_expects,
        ),
        actions=actions
        or [
            ActionStatement(
                ref="action_a",
                action="core.transform.reshape",
                args={"value": "hello"},
            )
        ],
        config=DSLConfig(environment=environment),
    )


def _make_action(
    ref: str = "action_a",
    action: str = "core.transform.reshape",
    args: dict[str, Any] | None = None,
    run_if: str | None = None,
    for_each: str | list[str] | None = None,
    environment: str | None = None,
    depends_on: list[str] | None = None,
) -> ActionStatement:
    return ActionStatement(
        ref=ref,
        action=action,
        args=args or {"value": "hello"},
        run_if=run_if,
        for_each=for_each,
        environment=environment,
        depends_on=depends_on or [],
    )


# --- Test Classes ---


class TestGetEffectiveEnvironment:
    """Test environment resolution (override vs default)."""

    def test_default_environment(self) -> None:
        """Should return default when no override is set."""
        stmt = _make_action(environment=None)
        assert get_effective_environment(stmt, "production") == "production"

    def test_literal_override(self) -> None:
        """Should return override when it's a literal string."""
        stmt = _make_action(environment="staging")
        assert get_effective_environment(stmt, "production") == "staging"

    def test_template_override_falls_back_to_default(self) -> None:
        """Template expressions in environment should fall back to default."""
        stmt = _make_action(environment="${{ TRIGGER.env }}")
        assert get_effective_environment(stmt, "production") == "production"

    def test_empty_string_override_falls_back(self) -> None:
        """Empty string override should fall back to default."""
        stmt = _make_action(environment="")
        assert get_effective_environment(stmt, "production") == "production"


@pytest.mark.anyio
class TestValidateDSLEntrypoint:
    """Test entrypoint schema validation."""

    async def test_no_expects(self) -> None:
        """No expects should produce no errors."""
        dsl = _make_dsl_input(entrypoint_expects=None)
        results = validate_dsl_entrypoint(dsl)
        assert results == []

    async def test_valid_expects(self) -> None:
        """Valid expected fields should produce no errors."""
        dsl = _make_dsl_input(
            entrypoint_expects={
                "name": {"type": "str", "description": "Name field"},
                "count": {"type": "int", "description": "Count field"},
            }
        )
        results = validate_dsl_entrypoint(dsl)
        assert results == []

    async def test_invalid_type_syntax(self) -> None:
        """Invalid type in expects should produce an error."""
        dsl = _make_dsl_input(
            entrypoint_expects={
                "bad_field": {"type": "not_a_valid_type!!!"},
            }
        )
        results = validate_dsl_entrypoint(dsl)
        assert len(results) > 0
        assert all(r.status == "error" for r in results)

    async def test_missing_required_field(self) -> None:
        """Missing required 'type' field should produce an error.

        DSLEntrypoint rejects missing 'type' at construction time (Pydantic),
        so we test validate_entrypoint_expects directly with raw dicts.
        """
        results = validate_entrypoint_expects(
            {"bad_field": {"description": "Missing type"}}
        )
        assert len(results) > 0
        assert any("error" == r.status for r in results)


class TestValidateEntrypointExpects:
    """Test validate_entrypoint_expects directly."""

    def test_empty_expects(self) -> None:
        results = validate_entrypoint_expects(None)
        assert results == []

    def test_valid_str_type(self) -> None:
        results = validate_entrypoint_expects(
            {"name": {"type": "str", "description": "A name"}}
        )
        assert results == []

    def test_valid_optional_type(self) -> None:
        results = validate_entrypoint_expects(
            {"name": {"type": "str | None", "description": "Optional name"}}
        )
        assert results == []

    def test_valid_list_type(self) -> None:
        results = validate_entrypoint_expects(
            {"items": {"type": "list[str]", "description": "A list of strings"}}
        )
        assert results == []

    def test_invalid_field_structure(self) -> None:
        """Non-dict field value should produce errors."""
        results = validate_entrypoint_expects({"bad": "not_a_dict"})
        assert len(results) > 0
        assert results[0].status == "error"

    def test_multiple_fields_mixed_valid_invalid(self) -> None:
        """Should produce errors only for invalid fields."""
        results = validate_entrypoint_expects(
            {
                "good_field": {"type": "str", "description": "Valid"},
                "bad_field": {"type": "!!!invalid!!!"},
            }
        )
        # Only bad_field should have errors
        assert len(results) == 1
        assert "bad_field" in results[0].msg


@pytest.mark.anyio
class TestValidateDSLExpressions:
    """Test expression validation with the visitor pattern."""

    async def test_valid_expressions(self) -> None:
        """Valid expressions should produce no errors."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    args={"value": "literal_string"},
                ),
            ]
        )
        results = await validate_dsl_expressions(dsl)
        assert results == []

    async def test_valid_trigger_expression(self) -> None:
        """Valid TRIGGER expression should not produce errors."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    args={"value": "${{ TRIGGER.data }}"},
                ),
            ]
        )
        results = await validate_dsl_expressions(dsl)
        assert results == []

    async def test_valid_actions_reference(self) -> None:
        """Valid ACTIONS reference to known ref should pass."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(ref="action_a", args={"value": "hello"}),
                _make_action(
                    ref="action_b",
                    args={"value": "${{ ACTIONS.action_a.result }}"},
                    depends_on=["action_a"],
                ),
            ]
        )
        results = await validate_dsl_expressions(dsl)
        assert results == []

    async def test_template_in_environment_rejected(self) -> None:
        """Template expression in environment override should be rejected."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    environment="${{ TRIGGER.env }}",
                    args={"value": "hello"},
                ),
            ]
        )
        results = await validate_dsl_expressions(dsl)
        assert len(results) > 0
        assert any("Template expressions are not allowed" in r.msg for r in results)

    async def test_exclude_expression_types(self) -> None:
        """Excluded expression types should not produce errors."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    args={"value": "${{ SECRETS.my_secret.KEY }}"},
                ),
            ]
        )
        # Excluding SECRET should suppress secret-related expression errors
        results = await validate_dsl_expressions(dsl, exclude={ExprType.SECRET})
        # SECRETS references should not cause errors when excluded
        secret_errors = [
            r
            for r in results
            if r.detail and any("SECRETS" in str(d) for d in r.detail)
        ]
        assert secret_errors == []


@pytest.mark.anyio
class TestValidateDSLActions:
    """Test action argument validation against registry manifests."""

    async def test_valid_run_if(
        self,
        session: AsyncSession,
        svc_role: Role,
        registry_version_with_manifest: None,
    ) -> None:
        """Valid run_if expression should not produce errors from run_if check."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    run_if="${{ TRIGGER.should_run }}",
                    args={"value": "hello"},
                ),
            ]
        )
        results = await validate_dsl_actions(session=session, role=svc_role, dsl=dsl)
        run_if_errors = [
            r
            for r in results
            if r.detail and any(d.msg and "run_if" in d.msg for d in r.detail)
        ]
        assert run_if_errors == []

    async def test_invalid_run_if_not_expression(
        self,
        session: AsyncSession,
        svc_role: Role,
        registry_version_with_manifest: None,
    ) -> None:
        """Non-expression run_if should produce an error."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    run_if="plain_string_not_expression",
                    args={"value": "hello"},
                ),
            ]
        )
        results = await validate_dsl_actions(session=session, role=svc_role, dsl=dsl)
        run_if_errors = [
            r
            for r in results
            if r.detail and any(d.msg and "run_if" in d.msg for d in r.detail)
        ]
        assert len(run_if_errors) > 0

    async def test_valid_for_each_string(
        self,
        session: AsyncSession,
        svc_role: Role,
        registry_version_with_manifest: None,
    ) -> None:
        """Valid for_each string expression should not produce for_each errors."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    for_each="${{ for var.item in TRIGGER.items }}",
                    args={"value": "${{ var.item }}"},
                ),
            ]
        )
        results = await validate_dsl_actions(session=session, role=svc_role, dsl=dsl)
        for_each_errors = [
            r
            for r in results
            if r.detail and any(d.msg and "for_each" in d.msg for d in r.detail)
        ]
        assert for_each_errors == []

    async def test_invalid_for_each_not_expression(
        self,
        session: AsyncSession,
        svc_role: Role,
        registry_version_with_manifest: None,
    ) -> None:
        """Non-expression for_each should produce an error."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    for_each="not_an_expression",
                    args={"value": "hello"},
                ),
            ]
        )
        results = await validate_dsl_actions(session=session, role=svc_role, dsl=dsl)
        for_each_errors = [
            r
            for r in results
            if r.detail and any(d.msg and "for_each" in d.msg for d in r.detail)
        ]
        assert len(for_each_errors) > 0


@pytest.mark.anyio
class TestValidateDSL:
    """Test full multi-tier validation pipeline."""

    async def test_all_tiers_disabled_returns_empty(
        self,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """All validation disabled should return empty set."""
        dsl = _make_dsl_input()
        result = await validate_dsl(
            session,
            dsl,
            role=svc_role,
            validate_entrypoint=False,
            validate_args=False,
            validate_expressions=False,
            validate_secrets=False,
        )
        assert result == set()

    async def test_valid_dsl_no_errors(
        self,
        session: AsyncSession,
        svc_role: Role,
        registry_version_with_manifest: None,
    ) -> None:
        """Valid DSL should produce no validation errors."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    args={"value": "hello"},
                ),
            ]
        )
        result = await validate_dsl(
            session,
            dsl,
            role=svc_role,
            validate_secrets=False,  # Don't validate secrets (no secrets manager)
        )
        assert result == set()

    async def test_entrypoint_errors_propagated(
        self,
        session: AsyncSession,
        svc_role: Role,
        registry_version_with_manifest: None,
    ) -> None:
        """Entrypoint validation errors should appear in results."""
        dsl = _make_dsl_input(
            entrypoint_expects={
                "bad_field": {"type": "!!!invalid!!!"},
            }
        )
        result = await validate_dsl(
            session,
            dsl,
            role=svc_role,
            validate_args=False,
            validate_expressions=False,
            validate_secrets=False,
        )
        assert len(result) > 0
        dsl_errors = [
            r for r in result if r.root.type == "dsl" and r.root.status == "error"
        ]
        assert len(dsl_errors) > 0

    async def test_expression_errors_propagated(
        self,
        session: AsyncSession,
        svc_role: Role,
        registry_version_with_manifest: None,
    ) -> None:
        """Expression validation errors should appear in results."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    environment="${{ TRIGGER.env }}",
                    args={"value": "hello"},
                ),
            ]
        )
        result = await validate_dsl(
            session,
            dsl,
            role=svc_role,
            validate_entrypoint=False,
            validate_args=False,
            validate_secrets=False,
        )
        assert len(result) > 0
        expr_errors = [
            r
            for r in result
            if r.root.type == "expression" and r.root.status == "error"
        ]
        assert len(expr_errors) > 0

    async def test_action_validation_with_valid_args(
        self,
        session: AsyncSession,
        svc_role: Role,
        registry_version_with_manifest: None,
    ) -> None:
        """Valid action args should not produce action validation errors."""
        dsl = _make_dsl_input(
            actions=[
                _make_action(
                    ref="action_a",
                    action="core.transform.reshape",
                    args={"value": "hello"},
                ),
            ]
        )
        result = await validate_dsl(
            session,
            dsl,
            role=svc_role,
            validate_entrypoint=False,
            validate_expressions=False,
            validate_secrets=False,
        )
        action_errors = [
            r for r in result if r.root.type == "action" and r.root.status == "error"
        ]
        assert action_errors == []
