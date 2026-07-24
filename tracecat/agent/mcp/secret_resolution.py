"""Resolve workspace-backed template expressions in MCP string mappings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from tracecat.auth.types import Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.exceptions import TracecatException, TracecatValidationError
from tracecat.expressions.core import CollectedExprs
from tracecat.expressions.eval import collect_expressions, eval_templated_object


@dataclass(frozen=True, slots=True)
class TemplatedMappingResolution:
    """Resolved mapping plus non-sensitive reference counts for logging."""

    mapping: dict[str, str]
    secret_ref_count: int
    var_ref_count: int


class TemplatedMappingResolutionError(TracecatException):
    """Raised when mapping resolution cannot safely produce strings."""


def _template_target(
    mapping: dict[str, str], *, resolve_keys: bool
) -> dict[str, str] | list[str]:
    return mapping if resolve_keys else list(mapping.values())


def _template_reference_summary(collected: CollectedExprs) -> str:
    references = [
        *(f"SECRETS.{path}" for path in sorted(collected.secrets)),
        *(f"VARS.{name}" for name in sorted(collected.variables)),
    ]
    return ", ".join(f"'{reference}'" for reference in references)


def _template_value_keys(mapping: dict[str, str]) -> list[str]:
    return sorted(key for key, value in mapping.items() if "${{" in value)


def _resolution_error(
    mapping: dict[str, str],
    collected: CollectedExprs | None = None,
) -> TemplatedMappingResolutionError:
    if collected and (summary := _template_reference_summary(collected)):
        return TemplatedMappingResolutionError(
            f"Could not resolve template reference(s): {summary}"
        )
    keys = _template_value_keys(mapping)
    if keys:
        return TemplatedMappingResolutionError(
            "Could not resolve template expression in mapping value(s): "
            f"{', '.join(repr(key) for key in keys)}"
        )
    return TemplatedMappingResolutionError(
        "Could not resolve template expression in mapping values"
    )


async def resolve_templated_mapping(
    mapping: dict[str, str],
    *,
    role: Role,
    resolve_keys: bool = True,
) -> TemplatedMappingResolution:
    """Resolve MCP mapping expressions at call time without persisting results.

    Resolution fails closed: missing references, malformed expressions, and
    unresolved template markers raise ``TemplatedMappingResolutionError``.
    ``resolve_keys=False`` preserves header names and resolves values only.
    """
    # Keep these service imports local: executor.service imports secrets_manager,
    # which imports IntegrationService, and IntegrationService imports this module.
    from tracecat.executor.service import get_workspace_variables
    from tracecat.secrets import secrets_manager

    target = _template_target(mapping, resolve_keys=resolve_keys)
    try:
        collected = collect_expressions(target)
    except Exception as exc:
        raise _resolution_error(mapping) from exc

    if (
        not collected.secrets
        and not collected.variables
        and not _template_value_keys(mapping)
    ):
        return TemplatedMappingResolution(
            mapping=mapping,
            secret_ref_count=0,
            var_ref_count=0,
        )

    try:
        secrets = await secrets_manager.get_action_secrets(
            secret_exprs=collected.secrets,
            action_secrets=set(),
        )
        vars_map = await get_workspace_variables(
            variable_exprs=collected.variables,
            role=role,
        )

        context = create_default_execution_context()
        context["SECRETS"] = secrets
        context["VARS"] = vars_map
        resolved = eval_templated_object(target, operand=context, strict=True)
    except Exception as exc:
        raise _resolution_error(mapping, collected) from exc

    if resolve_keys:
        if not isinstance(resolved, dict):
            raise TracecatValidationError(
                "Resolved mapping must be a JSON object with string values"
            )
        resolved_mapping = resolved
    else:
        if not isinstance(resolved, list) or len(resolved) != len(mapping):
            raise TracecatValidationError(
                "Resolved mapping must preserve all string values"
            )
        resolved_mapping = dict(zip(mapping, resolved, strict=True))

    non_string_keys = [
        key for key, value in resolved_mapping.items() if not isinstance(value, str)
    ]
    if non_string_keys:
        raise TemplatedMappingResolutionError(
            "Resolved mapping values must be strings "
            f"(invalid keys: {sorted(non_string_keys)})"
        )

    typed_mapping = cast(dict[str, str], resolved_mapping)
    if _template_value_keys(typed_mapping):
        raise _resolution_error(mapping, collected)

    return TemplatedMappingResolution(
        mapping=typed_mapping,
        secret_ref_count=len(collected.secrets),
        var_ref_count=len(collected.variables),
    )
