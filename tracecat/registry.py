"""Tracecat UDF registry."""

from __future__ import annotations

import asyncio
import functools
import inspect
import re
from collections.abc import Callable, Coroutine, Iterator
from types import FunctionType, GenericAlias
from typing import Annotated, Any, Generic, Self, TypedDict, TypeVar

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model
from pydantic_core import ValidationError
from typing_extensions import Doc

from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.db.schemas import UDFSpec
from tracecat.expressions.validation import TemplateValidator
from tracecat.identifiers import OwnerID
from tracecat.secrets.models import SecretKey, SecretName
from tracecat.types.exceptions import TracecatException

DEFAULT_NAMESPACE = "core"


class RegistrySecret(BaseModel):
    name: SecretName
    keys: list[SecretKey]


class RegistryUDFError(TracecatException):
    """Exception raised when a registry UDF error occurs."""


class RegistryValidationError(TracecatException):
    """Exception raised when a registry validation error occurs."""

    def __init__(self, *args, key: str, err: ValidationError | str | None = None):
        super().__init__(*args)
        self.key = key
        self.err = err


class UDFSchema(BaseModel):
    args: dict[str, Any]
    rtype: dict[str, Any] | None
    secrets: list[RegistrySecret] | None
    version: str | None
    description: str
    namespace: str
    key: str
    metadata: RegisteredUDFMetadata


ArgsT = TypeVar("ArgsT", bound=type[BaseModel])


# total=False allows for additional fields in the TypedDict
class RegisteredUDFMetadata(TypedDict, total=False):
    """Metadata for a registered UDF."""

    default_title: str | None
    display_group: str | None
    include_in_schema: bool


class RegisteredUDF(BaseModel, Generic[ArgsT]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    fn: FunctionType
    key: str
    description: str
    namespace: str
    version: str | None = None
    secrets: list[RegistrySecret] | None = None
    args_cls: ArgsT
    args_docs: dict[str, str] = Field(default_factory=dict)
    rtype_cls: Any | None = None
    rtype_adapter: TypeAdapter | None = None
    metadata: RegisteredUDFMetadata = Field(default_factory=dict)

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.fn)

    def construct_schema(self) -> dict[str, Any]:
        return UDFSchema(
            args=self.args_cls.model_json_schema(),
            rtype=None if not self.rtype_adapter else self.rtype_adapter.json_schema(),
            secrets=self.secrets,
            version=self.version,
            description=self.description,
            namespace=self.namespace,
            key=self.key,
            metadata=self.metadata,
        ).model_dump(mode="json")

    def validate_args(self, *args, **kwargs) -> dict[str, Any]:
        """Validate the input arguments for a UDF.

        Checks:
        1. The UDF must be called with keyword arguments only.
        2. The input arguments must be validated against the UDF's model.
        """
        if len(args) > 0:
            raise RegistryValidationError("UDF must be called with keyword arguments.")

        # Validate the input arguments, fail early if the input is invalid
        # Note that we've added TemplateValidator to the list of validators
        # so template expressions will pass args model validation
        try:
            # Note that we're allowing type coercion for the input arguments
            # Use cases would be transforming a UTC string to a datetime object
            # We return the validated input arguments as a dictionary
            validated: BaseModel = self.args_cls.model_validate(kwargs)
            return validated.model_dump()
        except ValidationError as e:
            logger.error(f"Validation error for UDF {self.key!r}. {e.errors()!r}")
            raise RegistryValidationError(
                f"Validation error for UDF {self.key!r}. {e.errors()!r}",
                key=self.key,
                err=e,
            ) from e
        except Exception as e:
            raise RegistryValidationError(
                f"Unexpected error when validating input arguments for UDF {self.key!r}. {e}",
                key=self.key,
            ) from e

    async def run_async(self, args: dict[str, Any]) -> Coroutine[Any, Any, Any]:
        """Run a UDF async."""
        if self.is_async:
            return await self.fn(**args)
        return await asyncio.to_thread(self.fn, **args)

    def to_udf_spec(
        self, owner_id: OwnerID = config.TRACECAT__DEFAULT_USER_ID
    ) -> UDFSpec:
        return UDFSpec(
            owner_id=owner_id,
            key=self.key,
            description=self.description,
            namespace=self.namespace,
            version=self.version,
            json_schema=self.construct_schema(),
            meta=self.metadata,
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

    def get_schemas(self) -> dict[str, dict]:
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
        secrets: list[RegistrySecret] | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        version: str | None = None,
        default_title: str | None = None,
        display_group: str | None = None,
        include_in_schema: bool = True,
    ):
        """Decorator factory to register a new udf function with additional parameters.

        Parameters
        ----------
        description : str
            A description of the udf.
        secrets : list[str] | None, optional
            Required secrets, by default None
        namespace : str, optional
            The namespace to register the UDF, by default `core`
        version : str | None, optional
            The UDF version, by default None
        default_title : str | None, optional
            The default title (also the catalog name) for the UDF, by default None
        display_group : str | None, optional
            The group under which the UDF should be displayed in the catalog, by default None
        """

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
            7. [x] Add TemplateValidator to the function annotations.
            """
            key = f"{namespace}.{fn.__name__}"
            logger.debug(f"Registering udf {key=}")

            wrapped_fn: FunctionType
            secret_names = [secret.name for secret in secrets or []]

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

                    validated_kwargs = self[key].validate_args(*args, **kwargs)
                    async with AuthSandbox(secrets=secret_names, target="env"):
                        return await fn(**validated_kwargs)
            else:

                @functools.wraps(fn)
                def wrapped_fn(*args, **kwargs) -> Any:
                    """Sync version of the wrapper function for the udf."""

                    validated_kwargs = self[key].validate_args(*args, **kwargs)
                    with AuthSandbox(secrets=secret_names, target="env"):
                        return fn(**validated_kwargs)

            if key in self:
                raise ValueError(f"UDF {key!r} is already registered.")
            if not callable(fn):
                raise ValueError("Provided object is not a callable function.")
            # Store function and decorator arguments in a dict

            _attach_validators(fn, TemplateValidator())
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
                metadata=RegisteredUDFMetadata(
                    default_title=default_title,
                    display_group=display_group,
                    include_in_schema=include_in_schema,
                ),
            )

            setattr(wrapped_fn, "__tracecat_udf", True)
            setattr(wrapped_fn, "__tracecat_udf_key", key)
            return wrapped_fn

        return decorator_register

    def filter(
        self,
        namespace: str | None = None,
        include_marked: bool = False,
        include_keys: set[str] | None = None,
    ) -> Iterator[tuple[str, RegisteredUDF]]:
        """Filter the registry.

        If namespace is provided, only return UDFs in that namespace.
        If not, return all UDFs.

        If include_marked is True, include UDFs marked with `include_in_schema: False`.
        Defining include_keys will override all other filters for.

        """
        # Get the net set of keys to include
        include_keys = include_keys or set()

        def include(udf: RegisteredUDF) -> bool:
            inc = True
            inc &= udf.key in include_keys
            if not include_marked:
                inc &= udf.metadata.get("include_in_schema", True)
            if namespace:
                inc &= udf.namespace.startswith(namespace)
            return inc

        return ((key, udf) for key, udf in self.__iter__() if include(udf))


registry = _Registry()


def _attach_validators(func: FunctionType, *validators: Callable):
    sig = inspect.signature(func)

    new_annotations = {
        name: Annotated[
            param.annotation,
            *validators,
        ]
        for name, param in sig.parameters.items()
    }
    if sig.return_annotation is not sig.empty:
        new_annotations["return"] = sig.return_annotation
    func.__annotations__ = new_annotations


def _udf_slug_camelcase(func: FunctionType, namespace: str) -> str:
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
    input_model = create_model(
        _udf_slug_camelcase(func, namespace),
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )  # type: ignore
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


def init() -> None:
    """Initialize the registry."""
    registry.init()
