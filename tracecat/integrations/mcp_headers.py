"""Late template resolution for remote MCP Custom-auth header values."""

from typing import cast

from tracecat.auth.sandbox import AuthSandbox
from tracecat.auth.types import Role
from tracecat.contexts import ctx_secret_masks
from tracecat.exceptions import TracecatCredentialsError, TracecatExpressionError
from tracecat.expressions.eval import collect_expressions, eval_templated_object
from tracecat.integrations.mcp_validation import MCPConfigurationError
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
    except MaskedSecretError as exc:
        error = MCPConfigurationError(str(exc))
    except TracecatExpressionError:
        # Parse errors quote the expression source, which may embed literal
        # credentials, and fail before secret masking begins.
        error = MCPConfigurationError("MCP header values contain an invalid expression")
    raise error


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
