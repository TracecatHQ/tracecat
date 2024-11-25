from __future__ import annotations

import importlib
from collections.abc import Callable
from types import FunctionType
from typing import Any, NoReturn

from pydantic import BaseModel, TypeAdapter

from tracecat.db.schemas import RegistryAction
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.validation import TemplateValidator
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    BoundRegistryAction,
    RegistryActionImpl,
    RegistryActionImplValidator,
    RegistryActionTemplateImpl,
    RegistryActionUDFImpl,
)
from tracecat.registry.repository import (
    RegisterKwargs,
    attach_validators,
    generate_model_from_function,
    get_signature_docs,
)

F = FunctionType


def get_bound_action_impl(
    action: RegistryAction,
) -> BoundRegistryAction[type[BaseModel]]:
    impl = RegistryActionImplValidator.validate_python(action.implementation)
    impl_loader = _LOADERS[impl.type]
    fn: F = impl_loader(impl)
    if impl.type == "udf":
        key = getattr(fn, "__tracecat_udf_key")
        kwargs = getattr(fn, "__tracecat_udf_kwargs")
        logger.trace("Binding UDF", key=key, name=action.name)
        # Add validators to the function
        validated_kwargs = RegisterKwargs.model_validate(kwargs)
        attach_validators(fn, TemplateValidator())
        args_docs = get_signature_docs(fn)
        # Generate the model from the function signature
        args_cls, rtype, rtype_adapter = generate_model_from_function(
            func=fn, namespace=validated_kwargs.namespace
        )
        return BoundRegistryAction(
            fn=fn,
            type=action.type,
            name=action.name,
            namespace=action.namespace,
            description=action.description,
            secrets=action.secrets,
            args_cls=args_cls,
            args_docs=args_docs,
            rtype_cls=rtype,
            rtype_adapter=rtype_adapter,
            default_title=action.default_title,
            display_group=action.display_group,
            origin=action.origin,
        )
    else:
        logger.trace("Binding template action", name=action.name)
        defn = impl.template_action.definition
        return BoundRegistryAction(
            fn=fn,
            type=action.type,
            name=action.name,
            namespace=action.namespace,
            description=action.description,
            secrets=action.secrets,
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


def load_template_impl(impl: RegistryActionTemplateImpl) -> F:
    return _not_implemented


def _not_implemented() -> NoReturn:
    raise NotImplementedError(
        "This is a template action, it must be run with concrete arguments"
    )


_LOADERS: dict[str, Callable[[RegistryActionImpl], F]] = {
    "udf": load_udf_impl,
    "template": load_template_impl,
}
