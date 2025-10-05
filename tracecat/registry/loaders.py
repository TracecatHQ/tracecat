from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, TypeAdapter, create_model
from pydantic_core import PydanticUndefined
from tracecat_registry import RegistrySecretTypeValidator

from tracecat import config
from tracecat.db.schemas import RegistryAction
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.validation import TemplateValidator
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    BoundRegistryAction,
    RegistryActionImplValidator,
    RegistryActionUDFImpl,
)
from tracecat.registry.repository import (
    RegisterKwargs,
    attach_validators,
    generate_model_from_function,
    get_signature_docs,
    import_and_reload,
)
from tracecat.types.exceptions import RegistryError

F = Callable[..., Any]

LoaderMode = Literal["validation", "execution"]


def get_bound_action_impl(
    action: RegistryAction, *, mode: LoaderMode = "validation"
) -> BoundRegistryAction:
    impl = RegistryActionImplValidator.validate_python(action.implementation)
    secrets = [
        RegistrySecretTypeValidator.validate_python(secret)
        for secret in action.secrets or []
    ]
    if impl.type == "udf":
        result = load_udf_impl(impl, action, mode=mode)

        # Check if we got a database fallback result (tuple) or a function
        if isinstance(result, tuple):
            # Database fallback case - result is (args_cls, None)
            args_cls, _ = result

            # Create a placeholder function that raises NotImplementedError
            def placeholder_fn(**kwargs):
                raise NotImplementedError(
                    f"Module {impl.module} is not available. "
                    f"This action ({action.name}) requires the module to be installed for execution."
                )

            fn = placeholder_fn
            args_docs = {}
            rtype = Any
            rtype_adapter = None
        else:
            # Normal function loading case
            fn = result
            key = getattr(fn, "__tracecat_udf_key")
            kwargs = getattr(fn, "__tracecat_udf_kwargs")
            logger.trace("Binding UDF", key=key, name=action.name, kwargs=kwargs)
            # Add validators to the function
            validated_kwargs = RegisterKwargs.model_validate(kwargs)
            if mode == "validation":
                attach_validators(fn, TemplateValidator())
            args_docs = get_signature_docs(fn)
            # Generate the model from the function signature
            args_cls, rtype, rtype_adapter = generate_model_from_function(
                func=fn, udf_kwargs=validated_kwargs
            )

        return BoundRegistryAction(
            fn=fn,
            type=impl.type,
            name=action.name,
            namespace=action.namespace,
            description=action.description,
            secrets=secrets,
            args_cls=args_cls,
            args_docs=args_docs,
            rtype_cls=rtype,
            rtype_adapter=rtype_adapter,
            default_title=action.default_title,
            display_group=action.display_group,
            doc_url=action.doc_url,
            author=action.author,
            deprecated=action.deprecated,
            origin=action.origin,
        )
    else:
        logger.trace("Binding template action", name=action.name)
        defn = impl.template_action.definition
        return BoundRegistryAction(
            fn=_not_implemented,
            type=impl.type,
            name=action.name,
            namespace=action.namespace,
            description=action.description,
            secrets=secrets,
            args_cls=create_expectation_model(
                defn.expects, defn.action.replace(".", "__")
            )
            if defn.expects
            else BaseModel,
            args_docs={
                key: schema.description or "-" for key, schema in defn.expects.items()
            },
            rtype_cls=Any,
            rtype_adapter=TypeAdapter(Any),
            default_title=action.default_title,
            display_group=action.display_group,
            doc_url=action.doc_url,
            author=action.author,
            deprecated=action.deprecated,
            include_in_schema=True,
            template_action=impl.template_action,
            origin=action.origin,
        )


def load_udf_impl(
    impl: RegistryActionUDFImpl,
    action: RegistryAction | None = None,
    *,
    mode: LoaderMode = "validation"
) -> F | tuple[type[BaseModel], Any]:
    """Load a UDF implementation."""
    module_path = impl.module
    function_name = impl.name

    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        logger.warning(
            "Force reloading local registry. You should only use this for development and not in production. "
            "In production, you should use a remote git repository."
        )
        return _load_function_from_module(module_path, function_name)

    # For remote/custom modules, try database first if we're in validation mode
    if not module_path.startswith("tracecat_registry") and mode == "validation":
        database_result = _load_action_from_database(action, function_name)
        if database_result is not None:
            logger.info(f"Using database interface for {module_path} in validation mode")
            return database_result

    # Try to import the module
    try:
        return _load_function_from_module(module_path, function_name)
    except ModuleNotFoundError as e:
        # Handle tracecat_registry import failures with reload fallback
        if module_path.startswith("tracecat_registry"):
            logger.warning(
                "Recovering from tracecat_registry import error; attempting safe reload",
                module=module_path,
                error=str(e),
            )
            return _load_function_from_module(module_path, function_name, force_reload=True)

        # Handle custom/remote module failures based on mode
        if mode == "execution":
            logger.error(
                f"Module {module_path} not found and required for execution",
                error=str(e)
            )
            raise RegistryError(
                f"Required module '{module_path}' not found. "
                f"The package containing this action must be installed on the executor."
            ) from e

        # Final fallback to empty model for validation mode
        args_cls = create_model(f"{function_name}_Args")
        return (args_cls, None)

def _load_action_from_database(action: RegistryAction | None, function_name: str) -> tuple[type[BaseModel], Any] | None:
    """Try to create args_cls from database interface."""
    if not action or not action.interface or "expects" not in action.interface:
        return None

    try:
        schema = action.interface["expects"]
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        # Build field definitions for create_model
        fields = {}
        for field_name, _field_schema in properties.items():
            field_type = Any  # Default type
            # Use PydanticUndefined for required fields so they show up as defaults
            default = PydanticUndefined if field_name in required else None
            fields[field_name] = (field_type, default)

        model_name = action.name if action and action.name else function_name
        args_cls = create_model(f"{model_name}_Args", **fields)
        return (args_cls, None)
    except Exception as e:
        logger.warning(
            "Failed to load action from database interface, schema may be malformed",
            action_name=action.name if action else "unknown",
            error=str(e)
        )
        return None


def _load_function_from_module(module_path: str, function_name: str, force_reload: bool = False) -> F:
    """Load a function from a module, with optional forced reload."""
    if force_reload:
        mod = import_and_reload(module_path)
    else:
        mod = importlib.import_module(module_path)

    try:
        return getattr(mod, function_name)
    except AttributeError as e:
        # Rare race condition - try one safe reload before failing
        logger.warning(
            "Function not found after import; attempting safe reload",
            module=module_path,
            function=function_name,
            error=str(e),
        )
        mod = import_and_reload(module_path)
        return getattr(mod, function_name)


def _not_implemented() -> NoReturn:
    raise NotImplementedError(
        "This is a template action, it must be run with concrete arguments"
    )
