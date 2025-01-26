from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, NoReturn

from pydantic import BaseModel, TypeAdapter
from tracecat_registry import RegistrySecret

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
)

F = Callable[..., Any]


def get_bound_action_impl(
    action: RegistryAction,
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


def load_udf_impl(impl: RegistryActionUDFImpl) -> F:
    """Load a UDF implementation."""
    module_path = impl.module
    function_name = impl.name
    mod = importlib.import_module(module_path)
    fn = getattr(mod, function_name)
    return fn


def _not_implemented() -> NoReturn:
    raise NotImplementedError(
        "This is a template action, it must be run with concrete arguments"
    )
