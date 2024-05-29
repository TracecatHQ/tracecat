from __future__ import annotations

import functools
import inspect
import os
import re
from collections.abc import Callable
from types import FunctionType, GenericAlias
from typing import Any, Self, TypedDict

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model
from typing_extensions import Doc

from tracecat.auth import AuthSandbox, Role

DEFAULT_NAMESPACE = "core"


class UDFSchema(TypedDict):
    args: dict[str, Any]
    rtype: dict[str, Any] | None
    secrets: list[str] | None


class RegisteredUDF(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    fn: FunctionType
    key: str
    description: str
    namespace: str
    version: str | None = None
    secrets: list[str] | None = None
    args_cls: type[BaseModel]
    args_docs: dict[str, str] = Field(default_factory=dict)
    rtype_cls: Any | None = None
    rtype_adapter: TypeAdapter | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.fn)

    def construct_schema(self) -> UDFSchema:
        return UDFSchema(
            args=self.args_cls.model_json_schema(),
            rtype=None if not self.rtype_adapter else self.rtype_adapter.json_schema(),
            secrets=self.secrets,
            version=self.version,
            description=self.description,
            namespace=self.namespace,
            key=self.key,
            metadata=self.metadata,
        )


class _Registry:
    """Singleton class to store and manage all registered udfs."""

    _instance: Self | None = None
    _udf_registry: dict[str, RegisteredUDF]
    _done_init: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._udf_registry = {}
        return cls._instance

    def __contains__(self, name: str) -> bool:
        return name in self._udf_registry

    def __getitem__(self, name: str) -> RegisteredUDF:
        return self.get(name)

    def __iter__(self):
        return iter(self._udf_registry.items())

    @property
    def store(self) -> dict[str, RegisteredUDF]:
        return self._udf_registry

    @property
    def keys(self) -> list[str]:
        return list(self._udf_registry.keys())

    def get(self, name: str) -> RegisteredUDF:
        """Retrieve a registered udf."""
        return self._udf_registry[name]

    def get_schemas(self) -> dict[str, UDFSchema]:
        return {key: udf.construct_schema() for key, udf in self._udf_registry.items()}

    def init(self) -> None:
        """Initialize the registry."""
        logger.warning("Initializing registry")
        if not _Registry._done_init:
            from tracecat import actions  # noqa: F401

            # Load default workflows

            _Registry._done_init = True

    def register(
        self,
        *,
        description: str,
        secrets: list[str] | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        version: str | None = None,
        **register_kwargs,
    ):
        """Decorator factory to register a new udf function with additional parameters."""

        def decorator_register(fn: FunctionType):
            """The decorator function to register a new udf.

            Responsibilities
            ----------------
            1. [x] Mark the function as a tracecat udf.
            2. [x] Register the udf in the registry.
            3. [x] Construct pydantic models for this udf.
                - [x] Dynamically create a model from the function signature.
                - [x] Register the return type of the function.
            4. [x] Using the model from 3,  create a specification (jsonschema/oas3) for the udf.
            5. [x] Parse out annotated argument docstrings from the function signature.
            6. [x] Store other metadata about the udf.
            """
            key = f"{namespace}.{fn.__name__}"
            logger.debug(f"Registering udf {key=}")

            wrapped_fn: FunctionType

            def _validate_args(*args, **kwargs):
                if len(args) > 0:
                    raise ValueError("UDF must be called with keyword arguments.")

                # Validate the input arguments, fail early if the input is invalid
                self[key].args_cls.model_validate(kwargs, strict=True)

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def wrapped_fn(*args, **kwargs) -> Any:
                    """Wrapper function for the udf.

                    Responsibilities
                    ----------------
                    Before invoking the function:
                    1. Grab all the secrets from the secrets API.
                    2. Inject all secret keys into the execution environment.
                    3. Clean up the environment after the function has executed.
                    """
                    _validate_args(*args, **kwargs)

                    role: Role = kwargs.pop("__role", Role(type="service"))
                    with logger.contextualize(user_id=role.user_id, pid=os.getpid()):
                        async with AuthSandbox(role=role, secrets=secrets):
                            return await fn(**kwargs)
            else:

                @functools.wraps(fn)
                def wrapped_fn(*args, **kwargs) -> Any:
                    """Sync version of the wrapper function for the udf."""

                    _validate_args(*args, **kwargs)

                    role: Role = kwargs.pop("__role", Role(type="service"))
                    with logger.contextualize(user_id=role.user_id, pid=os.getpid()):
                        with AuthSandbox(role=role, secrets=secrets):
                            return fn(**kwargs)

            if key in self:
                raise ValueError(f"UDF {key!r} is already registered.")
            if not callable(fn):
                raise ValueError("Provided object is not a callable function.")
            # Store function and decorator arguments in a dict
            args_cls, rtype_cls, rtype_adapter = _generate_model_from_function(
                fn, namespace=namespace
            )
            # TODO: Remove this
            args_docs = _get_signature_docs(fn)
            self._udf_registry[key] = RegisteredUDF(
                fn=wrapped_fn,
                key=key,
                namespace=namespace,
                version=version,
                description=description,
                secrets=secrets,
                args_cls=args_cls,
                args_docs=args_docs,
                rtype_cls=rtype_cls,
                rtype_adapter=rtype_adapter,
                metadata=register_kwargs,
            )

            setattr(wrapped_fn, "__tracecat_udf", True)
            setattr(wrapped_fn, "__tracecat_udf_key", key)
            return wrapped_fn

        return decorator_register


registry = _Registry()


def _udf_slug_camelcase(func: Callable, namespace: str) -> str:
    # Use slugify to preprocess the string
    slugified_string = re.sub(r"[^a-zA-Z0-9]+", " ", namespace)
    slugified_name = re.sub(r"[^a-zA-Z0-9]+", " ", func.__name__)
    # Split the slugified string into words
    words = slugified_string.split() + slugified_name.split()

    # Capitalize the first letter of each word except the first word
    # Join the words together without spaces
    return "".join(word.capitalize() for word in words)


def _generate_model_from_function(
    func: FunctionType, namespace: str
) -> tuple[type[BaseModel], type | GenericAlias | None, TypeAdapter | None]:
    # Get the signature of the function
    sig = inspect.signature(func)
    # Create a dictionary to hold field definitions
    fields = {}
    for name, param in sig.parameters.items():
        # Use the annotation and default value of the parameter to define the model field
        field_type: type = param.annotation
        field_info_kwargs = {}
        if metadata := getattr(field_type, "__metadata__", None):
            for meta in metadata:
                match meta:
                    case Doc(documentation=doc):
                        field_info_kwargs["description"] = doc

        default = ... if param.default is param.empty else param.default
        field_info = Field(default=default, **field_info_kwargs)
        fields[name] = (field_type, field_info)
    # Dynamically create and return the Pydantic model class
    input_model = create_model(_udf_slug_camelcase(func, namespace), **fields)  # type: ignore
    # Capture the return type of the function
    rtype = sig.return_annotation if sig.return_annotation is not sig.empty else Any
    rtype_adapter = TypeAdapter(rtype)

    return input_model, rtype, rtype_adapter


def _get_signature_docs(fn: FunctionType) -> dict[str, str]:
    param_docs = {}

    sig = inspect.signature(fn)
    for name, param in sig.parameters.items():
        if hasattr(param.annotation, "__metadata__"):
            for meta in param.annotation.__metadata__:
                if isinstance(meta, Doc):
                    param_docs[name] = meta.documentation
    return param_docs
