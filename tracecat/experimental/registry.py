import asyncio
import inspect
import os
from collections.abc import Callable
from types import GenericAlias
from typing import (
    Any,
    ParamSpec,
    Self,
    TypedDict,
    TypeVar,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    create_model,
)
from slugify import slugify
from typing_extensions import Doc

from tracecat.auth import AuthenticatedAPIClient, Role
from tracecat.db import Secret
from tracecat.logging import logger

_P = ParamSpec("_P")
_R = TypeVar("_R")

FunctionType = Callable[_P, _R]


class Schema(TypedDict):
    args: dict[str, Any]
    rtype: dict[str, Any]


class RegisteredUDF(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    fn: FunctionType
    description: str
    namespace: str | None = None
    version: str | None = None
    secrets: list[str] | None = None
    args_cls: type[BaseModel]
    args_docs: dict[str, str] = Field(default_factory=dict)
    rtype_cls: type | GenericAlias | None = None
    rtype_adapter: TypeAdapter | BaseModel | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def schema(self) -> Schema:
        return Schema(
            args=self.args_cls.model_json_schema(),
            rtype=self.rtype_adapter.json_schema(),
        )


class _Registry:
    """Singleton class to store and manage all registered udfs."""

    _instance: Self = None
    _udf_registry: dict[str, RegisteredUDF]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._udf_registry = {}
        return cls._instance

    def __contains__(self, name: str) -> bool:
        return name in self._udf_registry

    def __getitem__(self, name: str) -> RegisteredUDF:
        return self.get(name)

    def get(self, name: str) -> RegisteredUDF:
        """Retrieve a registered udf."""
        return self._udf_registry[name]

    def get_schemas(self) -> dict[str, Schema]:
        return {key: udf.schema() for key, udf in self._udf_registry.items()}

    def register(
        self,
        *,
        description: str,
        secrets: list[str] | None = None,
        namespace: str | None = None,
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
            logger.info("Registering udf", key=key)

            def wrapped_fn(*args, **kwargs):
                """Wrapper function for the udf.

                Responsibilities
                ----------------
                Before invoking the function:
                1. Grab all the secrets from the secrets API.
                2. Inject all secret keys into the execution environment.
                3. Clean up the environment after the function has executed.
                """
                if len(args) > 0:
                    raise ValueError("UDF must be called with keyword arguments.")

                # Validate the input arguments, fail early if the input is invalid
                self[key].args_cls.model_validate(kwargs, strict=True)

                role: Role = kwargs.pop("__role", Role(type="service"))
                with logger.contextualize(user_id=role.user_id, pid=os.getpid()):
                    with _AuthSandbox(role=role, secrets=secrets):
                        return fn(**kwargs)

            if key in self:
                raise ValueError(f"UDF {key!r} is already registered.")
            if not callable(fn):
                raise ValueError("Provided object is not a callable function.")
            # Store function and decorator arguments in a dict
            args_cls, rtype_cls, rtype_adapter = _generate_model_from_function(
                fn, namespace=namespace
            )
            args_docs = _get_signature_docs(fn)
            self._udf_registry[key] = RegisteredUDF(
                fn=wrapped_fn,
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


def udf_slug(func: Callable, namespace: str | None = None) -> str:
    namespace = namespace or func.__module__
    clean_ns = slugify(namespace, separator="_")
    return f"{clean_ns}__{func.__name__}"


def _generate_model_from_function(
    func: Callable[_P, _R], namespace: str | None = None
) -> tuple[type[BaseModel], type[BaseModel] | None]:
    # Get the signature of the function
    sig = inspect.signature(func)
    # Create a dictionary to hold field definitions
    fields = {}
    for name, param in sig.parameters.items():
        # Use the annotation and default value of the parameter to define the model field
        field_type = param.annotation
        default = ... if param.default is param.empty else param.default
        fields[name] = (field_type, default)
    # Dynamically create and return the Pydantic model class
    input_model = create_model(f"{udf_slug(func, namespace)}_model", **fields)
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


class _AuthSandbox:
    _role: Role
    _secret_names: list[str]
    _secret_objs: list[Secret]

    def __init__(self, role: Role, secrets: list[str]):
        self._role = role
        self._secret_names = secrets
        self._secret_objs = []

    def __enter__(self) -> Self:
        if self._secret_names:
            logger.info("Setting secrets in the environment")
            # self.secret_objs = self._get_secrets()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._unset_secrets()
        if exc_type is not None:
            logger.error("An error occurred", exc_info=(exc_type, exc_value, traceback))

    def _get_secrets(self) -> list[Secret]:
        """Retrieve secrets from the secrets API."""

        logger.debug("Getting secrets {}", self._secret_names)
        return asyncio.run(self._batch_get_secrets())

    def _set_secrets(self):
        """Set secrets in the environment."""
        for secret in self.secrets:
            logger.info("Setting secret {!r}", secret.name)
            for kv in secret.keys:
                os.environ[kv.key] = kv.value

    def _unset_secrets(self):
        for secret in self._secret_objs:
            logger.info("Deleting secret {!r}", secret.name)
            for kv in secret.keys:
                del os.environ[kv.key]

    async def _batch_get_secrets(self) -> list[Secret]:
        """Retrieve secrets from the secrets API."""
        async with AuthenticatedAPIClient(role=self._role) as client:
            # NOTE(perf): This is not really batched - room for improvement
            secret_responses = await asyncio.gather(
                *[
                    client.get(f"/secrets/{secret_name}")
                    for secret_name in self._secret_names
                ]
            )
            return [
                Secret.model_validate_json(secret_bytes.content)
                for secret_bytes in secret_responses
            ]


registry = _Registry()
