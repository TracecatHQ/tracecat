"""Tracecat UDF registry."""

from __future__ import annotations

import asyncio
import functools
import importlib
import inspect
import pkgutil
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from types import CoroutineType, FunctionType, GenericAlias, MethodType, ModuleType
from typing import Annotated, Any, Generic, Literal, Self, TypedDict, TypeVar, cast

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    create_model,
    model_validator,
)
from pydantic_core import ValidationError
from typing_extensions import Doc

from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.db.schemas import UDFSpec
from tracecat.dsl.models import ArgsT, DSLNodeResult
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.expectations import ExpectedField, create_expectation_model
from tracecat.expressions.validation import TemplateValidator
from tracecat.identifiers import OwnerID
from tracecat.logger import logger
from tracecat.secrets.models import SecretKey, SecretName
from tracecat.types.exceptions import TracecatException

DEFAULT_NAMESPACE = "core"

ArgsClsT = TypeVar("ArgsClsT", bound=type[BaseModel])


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


class _RegisterKwargs(BaseModel):
    default_title: str | None
    display_group: str | None
    namespace: str
    description: str
    secrets: list[RegistrySecret] | None
    version: str | None
    include_in_schema: bool


# total=False allows for additional fields in the TypedDict
class RegisteredUDFMetadata(TypedDict, total=False):
    """Metadata for a registered UDF."""

    is_template: bool
    default_title: str | None
    display_group: str | None
    include_in_schema: bool


class RegisteredUDF(BaseModel, Generic[ArgsClsT]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    fn: FunctionType | MethodType | CoroutineType
    key: str
    description: str
    namespace: str
    version: str | None = None
    secrets: list[RegistrySecret] | None = None
    args_cls: ArgsClsT
    args_docs: dict[str, str] = Field(default_factory=dict)
    rtype_cls: Any | None = None
    rtype_adapter: TypeAdapter[Any] | None = None
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

    def validate_args[T](self, *args, **kwargs) -> T:
        """Validate the input arguments for a UDF.

        Checks:
        1. The UDF must be called with keyword arguments only.
        2. The input arguments must be validated against the UDF's model.
        """
        if len(args) > 0:
            raise RegistryValidationError(
                "UDF must be called with keyword arguments.", key=self.key
            )

        # Validate the input arguments, fail early if the input is invalid
        # Note that we've added TemplateValidator to the list of validators
        # so template expressions will pass args model validation
        try:
            # Note that we're allowing type coercion for the input arguments
            # Use cases would be transforming a UTC string to a datetime object
            # We return the validated input arguments as a dictionary
            validated: BaseModel = self.args_cls.model_validate(kwargs)
            return cast(T, validated.model_dump())
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

    async def run_async(
        self, *, args: ArgsT, context: dict[str, Any] | None = None
    ) -> Any:
        """Run a UDF async.

        You only need to pass `base_context` if the UDF is a template.
        """
        if self.metadata.get("is_template"):
            kwargs = cast(ArgsT, {"args": args, "base_context": context or {}})
        else:
            kwargs = args
        logger.warning("Running UDF async", kwargs=kwargs)
        if self.is_async:
            return await self.fn(**kwargs)
        return await asyncio.to_thread(self.fn, **kwargs)

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
    _udf_registry: dict[str, RegisteredUDF[ArgsClsT]]
    _remote: str | None
    _done_init: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._udf_registry = {}
            cls._remote = config.TRACECAT__REMOTE_REGISTRY_URL
        return cls._instance

    def __contains__(self, name: str) -> bool:
        return name in self._udf_registry

    def __getitem__(self, name: str) -> RegisteredUDF[ArgsClsT]:
        return self.get(name)

    def __iter__(self):
        return iter(self._udf_registry.items())

    def __len__(self) -> int:
        return len(self._udf_registry)

    @property
    def store(self) -> dict[str, RegisteredUDF[ArgsClsT]]:
        return self._udf_registry

    @property
    def keys(self) -> list[str]:
        return list(self._udf_registry.keys())

    def get(self, name: str) -> RegisteredUDF[ArgsClsT]:
        """Retrieve a registered udf."""
        return self._udf_registry[name]

    def get_schemas(self) -> dict[str, dict]:
        return {key: udf.construct_schema() for key, udf in self._udf_registry.items()}

    def init(
        self,
        include_base: bool = True,
        include_remote: bool = False,
        include_templates: bool = False,
    ) -> None:
        """Initialize the registry."""
        if not _Registry._done_init:
            logger.warning("Initializing registry")
            # Load udfs
            if include_base:
                self._load_base_udfs()

            # Load remote udfs
            if include_remote and self._remote:
                self._load_remote_udfs(self._remote, module_name="udfs")

            # Load template actions
            if include_templates:
                self._load_template_actions()

            _Registry._done_init = True

    @classmethod
    def _reset(cls) -> None:
        logger.warning("Resetting registry")
        cls._udf_registry = {}
        cls._done_init = False

    def _load_base_udfs(self) -> None:
        """Load all udfs and template actions into the registry."""
        # Load udfs
        logger.info("Loading base UDFs")
        from tracecat import actions

        self._register_udfs_from_module(actions, visited_modules=set())

    def _register_udf(
        self,
        *,
        fn: FunctionType,
        key: str,
        namespace: str,
        version: str | None,
        description: str,
        secrets: list[RegistrySecret] | None,
        default_title: str | None,
        display_group: str | None,
        include_in_schema: bool,
    ):
        logger.debug(f"Registering UDF {key=}")

        secret_names = [secret.name for secret in secrets or []]

        _attach_validators(fn, TemplateValidator())
        args_docs = _get_signature_docs(fn)
        args_cls, rtype, rtype_adapter = _generate_model_from_function(
            func=fn, namespace=namespace
        )

        wrapped_fn: FunctionType
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapped_fn(*args, **kwargs) -> Any:
                """Asynchronous wrapper function for the UDF.

                This wrapper handles argument validation and secret injection for async UDFs.
                """

                validated_kwargs = self[key].validate_args(*args, **kwargs)
                async with AuthSandbox(secrets=secret_names, target="env"):
                    return await fn(**validated_kwargs)
        else:

            @functools.wraps(fn)
            def wrapped_fn(*args, **kwargs) -> Any:
                """Synchronous wrapper function for the UDF.

                This wrapper handles argument validation and secret injection for sync UDFs.
                """

                validated_kwargs = self[key].validate_args(*args, **kwargs)
                with AuthSandbox(secrets=secret_names, target="env"):
                    return fn(**validated_kwargs)

        self._udf_registry[key] = RegisteredUDF(
            fn=wrapped_fn,
            key=key,
            namespace=namespace,
            version=version,
            description=description,
            secrets=secrets,
            args_cls=args_cls,
            args_docs=args_docs,
            rtype_cls=rtype,
            rtype_adapter=rtype_adapter,
            metadata=RegisteredUDFMetadata(
                default_title=default_title,
                display_group=display_group,
                include_in_schema=include_in_schema,
            ),
        )

    def _register_udfs_from_module(
        self,
        module: ModuleType,
        *,
        visited_modules: set[str],
        base_path: str = "",
    ) -> None:
        """Recursively register all UDFs from a given module and its submodules."""
        if module.__name__ in visited_modules:
            logger.debug("Skipping visited module", module=module.__name__)
            return

        visited_modules.add(module.__name__)

        logger.trace(f"Registering UDFs from module: {module.__name__}")
        for name, obj in inspect.getmembers(module):
            if inspect.isfunction(obj) and hasattr(obj, "__tracecat_udf"):
                key = getattr(
                    obj,
                    "__tracecat_udf_key",
                    f"{base_path}.{name}" if base_path else name,
                )
                kwargs = getattr(obj, "__tracecat_udf_kwargs", None)
                if kwargs is None:
                    logger.warning(
                        f"Skipping UDF {key!r}. Missing __tracecat_udf_kwargs",
                        key=key,
                        name=name,
                        base_path=base_path,
                    )
                    continue
                logger.trace(
                    f"Registering UDF: {key}", key=key, name=name, base_path=base_path
                )
                validated_kwargs = _RegisterKwargs.model_validate(kwargs)
                if key in self._udf_registry:
                    logger.trace(f"UDF {key!r} is already registered, skipping")
                    continue
                self._register_udf(
                    fn=obj,
                    key=key,
                    namespace=validated_kwargs.namespace,
                    version=validated_kwargs.version,
                    description=validated_kwargs.description,
                    secrets=validated_kwargs.secrets,
                    default_title=validated_kwargs.default_title,
                    display_group=validated_kwargs.display_group,
                    include_in_schema=validated_kwargs.include_in_schema,
                )

        if hasattr(module, "__path__"):  # Check if the module is a package
            for _, submodule_name, _is_pkg in pkgutil.iter_modules(module.__path__):
                full_submodule_name = f"{module.__name__}.{submodule_name}"
                if full_submodule_name not in visited_modules:
                    logger.trace(f"Importing submodule: {full_submodule_name}")
                    submodule = importlib.import_module(full_submodule_name)
                    new_base_path = (
                        f"{base_path}.{submodule_name}" if base_path else submodule_name
                    )
                    self._register_udfs_from_module(
                        submodule,
                        base_path=new_base_path,
                        visited_modules=visited_modules,
                    )

    def _load_remote_udfs(self, remote_registry_url: str, module_name: str) -> None:
        """Load udfs from a remote source."""
        with logger.contextualize(remote=remote_registry_url):
            logger.info("Loading remote udfs")
            try:
                logger.trace("BEFORE", keys=self.keys)
                # TODO(perf): Use asyncio
                logger.info("Installing remote udfs")
                subprocess.run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--system",
                        remote_registry_url,
                    ],
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                logger.error("Error installing remote udfs", error=e)
                raise
            try:
                # Import the module
                logger.info("Importing remote udfs", module_name=module_name)
                module = importlib.import_module(module_name)

                # # Reload the module to ensure fresh execution
                self._register_udfs_from_module(module, visited_modules=set())

                logger.trace("AFTER", keys=self.keys)
            except ImportError as e:
                logger.error("Error importing remote udfs", error=e)
                raise

    def _load_template_actions(self) -> None:
        """Load template actions from the actions/templates directory."""

        # Load the default templates
        path = Path(__file__).parent.parent / "templates"
        logger.info(f"Loading default templates from {path!s}")
        for file in path.iterdir():
            if not (
                file.is_file()
                and file.suffix in (".yml", ".yaml")
                and not file.name.startswith("_")
            ):
                logger.info(f"Skipping template {file!s}")
                continue
            logger.info(f"Loading template {file!s}")
            # Load TemplateActionDefinition
            template_action = TemplateAction.from_yaml(file)

            key = template_action.definition.action
            if key in self._udf_registry:
                # Already registered, skip
                logger.info(f"Template {key!r} already registered, skipping")
                continue

            # Register the action
            defn = template_action.definition
            expectation = defn.expects
            self._udf_registry[key] = RegisteredUDF(
                fn=template_action.run,
                key=key,
                namespace=defn.namespace,
                version=None,
                description=defn.description,
                secrets=defn.secrets,
                args_cls=create_expectation_model(expectation, key.replace(".", "__"))
                if expectation
                else None,
                args_docs={
                    key: schema.description or "-"
                    for key, schema in expectation.items()
                },
                rtype_cls=Any,
                rtype_adapter=TypeAdapter(Any),
                metadata=RegisteredUDFMetadata(
                    is_template=True,
                    default_title=defn.title,
                    display_group=defn.display_group,
                    include_in_schema=True,
                ),
            )

    def register(
        self,
        *,
        default_title: str | None = None,
        display_group: str | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        description: str,
        secrets: list[RegistrySecret] | None = None,
        version: str | None = None,
        include_in_schema: bool = True,
    ) -> Callable[[FunctionType], FunctionType]:
        """Decorator factory to register a new UDF (User-Defined Function) with additional parameters.

        This method creates a decorator that can be used to register a function as a UDF in the Tracecat system.
        It handles the registration process, including metadata assignment, argument validation, and execution wrapping.

        Parameters
        ----------
        default_title : str | None, optional
            The default title for the UDF in the catalog, by default None.
        display_group : str | None, optional
            The group under which the UDF should be displayed in the catalog, by default None.
        namespace : str, optional
            The namespace to register the UDF under, by default 'core'.
        description : str
            A detailed description of the UDF's purpose and functionality.
        secrets : list[RegistrySecret] | None, optional
            A list of secret keys required by the UDF, by default None.
        version : str | None, optional
            The version of the UDF, by default None.
        include_in_schema : bool, optional
            Whether to include this UDF in the API schema, by default True.

        Returns
        -------
        Callable[[FunctionType], FunctionType]
            A decorator function that registers the decorated function as a UDF.

        Notes
        -----
        The decorated function will be wrapped to handle argument validation and secret injection.
        Both synchronous and asynchronous functions are supported.
        """

        def decorator_register(fn: FunctionType) -> FunctionType:
            """The decorator function to register a new UDF.

            This inner function handles the actual registration process for a given function.

            Parameters
            ----------
            fn : FunctionType
                The function to be registered as a UDF.

            Returns
            -------
            FunctionType
                The wrapped and registered UDF function.

            Raises
            ------
            ValueError
                If the UDF key is already registered or if the provided object is not callable.
            """
            if not callable(fn):
                raise ValueError("The provided object is not callable.")

            key = f"{namespace}.{fn.__name__}"

            setattr(fn, "__tracecat_udf", True)
            setattr(fn, "__tracecat_udf_key", key)
            setattr(
                fn,
                "__tracecat_udf_kwargs",
                {
                    "default_title": default_title,
                    "display_group": display_group,
                    "include_in_schema": include_in_schema,
                    "namespace": namespace,
                    "version": version,
                    "description": description,
                    "secrets": secrets,
                },
            )
            return fn

        return decorator_register

    def filter(
        self,
        namespace: str | None = None,
        include_marked: bool = False,
        include_keys: set[str] | None = None,
    ) -> Iterator[tuple[str, RegisteredUDF[ArgsClsT]]]:
        """Filter the registry.

        If namespace is provided, only return UDFs in that namespace.
        If not, return all UDFs.

        If include_marked is True, include UDFs marked with `include_in_schema: False`.
        Defining include_keys will override all other filters for.

        """
        # Get the net set of keys to include
        include_keys = include_keys or set()

        def include(udf: RegisteredUDF[ArgsClsT]) -> bool:
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


class ActionLayer(BaseModel):
    ref: str = Field(..., description="The reference of the layer")
    action: str
    args: ArgsT

    async def run(self, *, registry: _Registry, context: dict[str, Any]) -> Any:
        udf = registry.get(self.action)
        udf.validate_args(**self.args)
        concrete_args = cast(ArgsT, eval_templated_object(self.args, operand=context))
        return await udf.run_async(args=concrete_args, context=context)


class TemplateActionDefinition(BaseModel):
    action: str = Field(..., description="The action key")
    namespace: str = Field(..., description="The namespace of the action")
    title: str = Field(..., description="The title of the action")
    description: str = Field("", description="The description of the action")
    display_group: str = Field(..., description="The display group of the action")
    secrets: list[RegistrySecret] | None = Field(
        None, description="The secrets to pass to the action"
    )
    expects: dict[str, ExpectedField] = Field(
        ..., description="The arguments to pass to the action"
    )
    layers: list[ActionLayer] = Field(
        ..., description="The internal layers of the action"
    )
    returns: str | list[str] | dict[str, Any] = Field(
        ..., description="The result of the action"
    )

    # Validate layers
    @model_validator(mode="after")
    def validate_layers(self) -> TemplateActionDefinition:
        layer_refs = [layer.ref for layer in self.layers]
        unique_layer_refs = set(layer_refs)

        if len(layer_refs) != len(unique_layer_refs):
            duplicate_layer_refs = [
                ref for ref in layer_refs if layer_refs.count(ref) > 1
            ]
            raise ValueError(
                f"Duplicate layer references found: {duplicate_layer_refs}"
            )

        return self


class TemplateAction(BaseModel):
    type: Literal["action"] = Field("action", frozen=True)
    definition: TemplateActionDefinition

    @staticmethod
    def from_yaml(path: Path) -> TemplateAction:
        with path.open("r") as f:
            template = yaml.safe_load(f)

        return TemplateAction(**template)

    async def run(
        self,
        *,
        args: dict[str, Any],
        base_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the layers of the action.

        Assumptions:
        - Args have been validated against the expected arguments
        - All expressions are deterministic

        Returns
        -------
        - If no return is specified, we return the last layer's result
        - If a return is specified, we return the result of the expression
        """

        context = base_context.copy() | {"inputs": args, "layers": {}}
        logger.info("Running template action", action=self.definition.action)
        for layer in self.definition.layers:
            result = await layer.run(context=context, registry=registry)
            context["layers"][layer.ref] = DSLNodeResult(
                result=result,
                result_typename=type(result).__name__,
            )

        # Handle returns
        return eval_templated_object(self.definition.returns, operand=context)
