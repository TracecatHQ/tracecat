from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, TypeAdapter
from tracecat_registry import RegistrySecret

from tracecat import config
from tracecat.db.schemas import RegistryAction
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.validation import CoreSchemaTemplateValidator
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

F = Callable[..., Any]

LoaderMode = Literal["validation", "execution"]


def get_bound_action_impl(
    action: RegistryAction, *, mode: LoaderMode = "validation"
) -> BoundRegistryAction:
    impl = RegistryActionImplValidator.validate_python(action.implementation)
    secrets = [RegistrySecret(**secret) for secret in action.secrets or []]
    if impl.type == "udf":
        fn = load_udf_impl(impl)
        key = getattr(fn, "__tracecat_udf_key")
        kwargs = getattr(fn, "__tracecat_udf_kwargs")
        logger.trace("Binding UDF", key=key, name=action.name, kwargs=kwargs)
        # Add validators to the function
        validated_kwargs = RegisterKwargs.model_validate(kwargs)
        if mode == "validation":
            attach_validators(fn, CoreSchemaTemplateValidator())
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


def load_udf_impl(impl: RegistryActionUDFImpl) -> F:
    """Load a UDF implementation."""
    module_path = impl.module
    function_name = impl.name

    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        logger.warning(
            "Force reloading local registry. You should only use this for development and not in production. "
            "In production, you should use a remote git repository."
        )
        mod = import_and_reload(module_path)
    else:
        mod = importlib.import_module(module_path)
    fn = getattr(mod, function_name)
    return fn


def _not_implemented() -> NoReturn:
    raise NotImplementedError(
        "This is a template action, it must be run with concrete arguments"
    )
