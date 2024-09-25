from __future__ import annotations

from typing import Any, cast

from tracecat.auth.sandbox import AuthSandbox
from tracecat.dsl.models import DSLNodeResult
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.shared import ExprContext
from tracecat.logger import logger
from tracecat.registry.client import RegistryClient
from tracecat.registry.manager import RegistryManager
from tracecat.registry.models import ArgsT, RegisteredUDF, RunActionParams
from tracecat.secrets.secrets_manager import env_sandbox


async def run_template(
    *,
    udf: RegisteredUDF,
    args: ArgsT,
    base_context: dict[str, Any] | None = None,
    version: str | None = None,
) -> Any:
    """Handle template execution

    Move the template action execution here, so we can
    override run_async's implementation
    """
    context = base_context.copy() | {
        ExprContext.TEMPLATE_ACTION_INPUTS: args,
        ExprContext.TEMPLATE_ACTION_LAYERS: {},
    }
    defn = udf.template_action.definition
    logger.info("Running template action", action=defn.action)
    registry = RegistryManager().get_registry(version)

    for layer in defn.layers:
        # Evaluate a layer
        layer_udf = registry.get(layer.action)
        validated_args = layer_udf.validate_args(**layer.args)
        concrete_args = cast(
            ArgsT, eval_templated_object(validated_args, operand=context)
        )
        result = await run_async(
            udf=layer_udf,
            args=concrete_args,
            context=context,
            version=version,
            remote=False,
        )
        # Store the result of the layer
        logger.info("Storing layer result", layer=layer.ref, result=result)
        context[ExprContext.TEMPLATE_ACTION_LAYERS][layer.ref] = DSLNodeResult(
            result=result,
            result_typename=type(result).__name__,
        )

    # Handle returns
    return eval_templated_object(defn.returns, operand=context)


async def run_async(
    *,
    udf: RegisteredUDF,
    args: ArgsT,
    context: dict[str, Any] | None = None,
    remote: bool = False,
    version: str | None = None,
) -> Any:
    """Run a UDF async."""
    validated_args = udf.validate_args(**args)
    if udf.metadata.get("is_template"):
        logger.warning("Running template UDF async")
        return await run_template(
            udf=udf, args=validated_args, base_context=context or {}
        )

    logger.warning("Running regular UDF async")
    secret_names = [secret.name for secret in udf.secrets or []]
    if remote:
        # NOTE!!!: Avoid using this if possible
        # This runs the UDF in the API server
        async with (
            AuthSandbox(secrets=secret_names, target="context") as sandbox,
            RegistryClient() as registry_client,
        ):
            secrets = sandbox.secrets.copy()
            return await registry_client.call_action(
                key=udf.key,
                version=version,
                args=validated_args,
                context=context,
                secrets=secrets,
            )
    else:
        async with (
            AuthSandbox(secrets=secret_names, target="context") as sandbox,
        ):
            # Flatten the secrets to a dict[str, str]
            secret_context = sandbox.secrets.copy()
            flattened_secrets: dict[str, str] = {}
            for name, keyvalues in secret_context.items():
                for key, value in keyvalues.items():
                    if key in flattened_secrets:
                        raise ValueError(
                            f"Key {key!r} is duplicated in {name!r}! "
                            "Please ensure only one secret with a given name is set. "
                            "e.g. If you have `first_secret.KEY` set, then you cannot "
                            "also set `second_secret.KEY` as `KEY` is duplicated."
                        )
                    flattened_secrets[key] = value

            with env_sandbox(flattened_secrets):
                # Run the UDF in the caller process (usually the worker)
                return await RegistryManager().run_action(
                    action_name=udf.key,
                    params=RunActionParams(args=validated_args, context=context),
                    version=version,
                )
