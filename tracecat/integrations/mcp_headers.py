"""Late template resolution for remote MCP Custom-auth header values."""

from typing import cast

import orjson

from tracecat.auth.sandbox import AuthSandbox
from tracecat.auth.types import Role
from tracecat.authz.controls import check_scopes
from tracecat.contexts import ctx_secret_masks
from tracecat.exceptions import TracecatCredentialsError, TracecatExpressionError
from tracecat.expressions.eval import collect_expressions, eval_templated_object
from tracecat.integrations.mcp_validation import MCPConfigurationError
from tracecat.parse import traverse_expressions
from tracecat.secrets.common import MaskedSecretError, call_with_masked_errors
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.masking import SecretMaskCollector
from tracecat.variables.schemas import VariableSearch
from tracecat.variables.service import VariablesService


async def resolve_mcp_header_values(
    headers: dict[str, str], *, role: Role
) -> dict[str, str]:
    """Resolve header values in the workspace's default secrets environment.

    Header names remain literal. The returned credentials are for an immediate
    outbound connection only, not persistence or workflow transport.
    """
    try:
        return await _resolve_mcp_header_values(headers, role=role)
    except TracecatCredentialsError:
        error = MCPConfigurationError(
            "Could not resolve MCP header secrets in the default environment"
        )
    # Expression diagnostics quote the source, and header values may embed
    # literal credentials that secret masking doesn't know about.
    except MaskedSecretError:
        error = MCPConfigurationError("MCP header values could not be evaluated")
    except TracecatExpressionError:
        error = MCPConfigurationError("MCP header values contain an invalid expression")
    raise error


def require_header_expression_scope(raw_headers: str, *, role: Role) -> None:
    """Require ``integration:update`` to store or test header expressions.

    Expressions make the server send workspace secrets to the integration's
    URL. Like reusing stored credentials, that is update-scoped, so a
    create-only caller can't pair a secret reference with a URL it controls.

    Raises:
        ScopeDeniedError: If the headers contain expressions and the role lacks
            ``integration:update``.
    """
    try:
        parsed = orjson.loads(raw_headers)
    except orjson.JSONDecodeError:
        # Malformed headers are rejected when they are decrypted for use.
        return
    if isinstance(parsed, dict) and any(traverse_expressions(list(parsed.values()))):
        check_scopes(role, "integration:update")


async def _resolve_mcp_header_values(
    headers: dict[str, str], *, role: Role
) -> dict[str, str]:
    values = list(headers.values())
    collected = collect_expressions(values)
    async with AuthSandbox(
        role=role,
        secrets=collected.secrets,
        environment=DEFAULT_SECRETS_ENVIRONMENT,
    ) as sandbox:
        secrets = sandbox.secrets.copy()

    variables = {}
    if collected.variables:
        async with VariablesService.with_session(role=role) as service:
            variables = {
                variable.name: variable.values
                for variable in await service.search_variables(
                    VariableSearch(
                        names=collected.variables,
                        environment=DEFAULT_SECRETS_ENVIRONMENT,
                    )
                )
            }

    masks = ctx_secret_masks.get() or SecretMaskCollector()
    masks.observe(secrets)
    resolved = call_with_masked_errors(
        lambda: eval_templated_object(
            values, operand={"SECRETS": secrets, "VARS": variables}
        ),
        masks=masks.values,
    )

    if not isinstance(resolved, list) or not all(
        isinstance(value, str) for value in resolved
    ):
        raise MCPConfigurationError("Resolved MCP header values must be strings")
    return dict(zip(headers, cast(list[str], resolved), strict=True))
