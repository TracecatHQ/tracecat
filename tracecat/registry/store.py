import functools
import importlib
import inspect
import re
import subprocess
from collections.abc import Callable, Iterator
from importlib.resources import files
from pathlib import Path
from timeit import default_timer
from types import FunctionType, GenericAlias, ModuleType
from typing import Annotated, Any
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model
from tracecat_registry import RegistrySecret
from typing_extensions import Doc

from tracecat import __version__ as DEFAULT_VERSION
from tracecat import config
from tracecat.auth.sandbox import AuthSandbox
from tracecat.concurrency import apartial
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.validation import TemplateValidator
from tracecat.logger import logger
from tracecat.registry.models import ArgsClsT, RegisteredUDFMetadata, RegisteredUDFRead
from tracecat.registry.template_actions import TemplateAction
from tracecat.registry.udfs import RegisteredUDF


class _RegisterKwargs(BaseModel):
    default_title: str | None
    display_group: str | None
    namespace: str
    description: str
    secrets: list[RegistrySecret] | None
    version: str | None
    include_in_schema: bool


class Registry:
    """Class to store and manage all registered udfs."""

    def __init__(self, version: str = DEFAULT_VERSION):
        self._store: dict[str, RegisteredUDF[ArgsClsT]] = {}
        self._remote = config.TRACECAT__REMOTE_REGISTRY_URL
        self._is_initialized: bool = False
        self._version = version

    def __contains__(self, name: str) -> bool:
        return name in self._store

    def __getitem__(self, name: str) -> RegisteredUDF[ArgsClsT]:
        return self.get(name)

    def __iter__(self):
        return iter(self._store.items())

    def __len__(self) -> int:
        return len(self._store)

    def length(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"Registry(version={self._version}, store={[x.key for x in self._store.values()]})"

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def store(self) -> dict[str, RegisteredUDF[ArgsClsT]]:
        return self._store

    @property
    def keys(self) -> list[str]:
        return list(self._store.keys())

    @property
    def version(self) -> str:
        return self._version

    def get(self, name: str) -> RegisteredUDF[ArgsClsT]:
        """Retrieve a registered udf."""
        return self._store[name]

    def get_schemas(self) -> dict[str, dict]:
        return {key: udf.construct_schema() for key, udf in self._store.items()}

    def init(
        self,
        include_base: bool = True,
        include_remote: bool = False,
        include_templates: bool = True,
    ) -> None:
        """Initialize the registry."""
        if not self._is_initialized:
            logger.info("Initializing registry")
            # Load udfs
            if include_base:
                self._load_base_udfs()

            # Load remote udfs
            if include_remote and self._remote:
                self._load_remote_udfs(self._remote, module_name="udfs")

            # Load template actions
            if include_templates:
                self._load_template_actions()

            logger.info("Registry initialized", num_actions=len(self._store))
            self._is_initialized = True

    def list_actions(self) -> list[RegisteredUDFRead]:
        return [
            RegisteredUDFRead.model_validate(
                x.model_dump(
                    mode="json",
                    exclude={
                        "fn",
                        "args_cls",
                        "rtype_cls",
                        "rtype_adapter",
                    },
                )
            )
            for x in self._store.values()
        ]

    def _reset(self) -> None:
        logger.warning("Resetting registry")
        self._store = {}
        self._is_initialized = False

    def _load_base_udfs(self) -> None:
        """Load all udfs and template actions into the registry."""
        # Load udfs
        logger.info("Loading base UDFs")
        import tracecat_registry

        self._register_udfs_from_package(tracecat_registry)

    def register_udf(
        self,
        *,
        fn: FunctionType,
        key: str,
        namespace: str,
        version: str | None,
        description: str,
        secrets: list[RegistrySecret] | None,
        args_cls: ArgsClsT,
        args_docs: dict[str, str],
        rtype: type,
        rtype_adapter: TypeAdapter,
        default_title: str | None,
        display_group: str | None,
        include_in_schema: bool,
        is_template: bool = False,
        origin: str = "base",
    ):
        logger.debug(f"Registering UDF {key=}")

        secret_names = [secret.name for secret in secrets or []]

        wrapped_fn: FunctionType
        # Authsandbox isn't threadsafe. Don't do this
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapped_fn(*args, **kwargs) -> Any:
                """Asynchronous wrapper function for the UDF.

                This wrapper handles argument validation and secret injection for async UDFs.
                """
                async with AuthSandbox(secrets=secret_names, target="env"):
                    return await fn(**kwargs)
        else:

            @functools.wraps(fn)
            def wrapped_fn(*args, **kwargs) -> Any:
                """Synchronous wrapper function for the UDF.

                This wrapper handles argument validation and secret injection for sync UDFs.
                """
                with AuthSandbox(secrets=secret_names, target="env"):
                    return fn(**kwargs)

        self._store[key] = RegisteredUDF(
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
                is_template=is_template,
                default_title=default_title,
                display_group=display_group,
                include_in_schema=include_in_schema,
                origin=origin,
            ),
        )

    def _register_udf_from_function(
        self,
        fn: FunctionType,
        *,
        name: str,
        origin: str = "base",
    ) -> None:
        # Get function metadata
        key = getattr(fn, "__tracecat_udf_key")
        kwargs = getattr(fn, "__tracecat_udf_kwargs")
        logger.info(f"Registering UDF: {key}", key=key, name=name)
        # Add validators to the function
        validated_kwargs = _RegisterKwargs.model_validate(kwargs)
        _attach_validators(fn, TemplateValidator())
        args_docs = _get_signature_docs(fn)
        # Generate the model from the function signature
        args_cls, rtype, rtype_adapter = _generate_model_from_function(
            func=fn, namespace=validated_kwargs.namespace
        )
        self.register_udf(
            fn=fn,
            key=key,
            namespace=validated_kwargs.namespace,
            version=validated_kwargs.version,
            description=validated_kwargs.description,
            secrets=validated_kwargs.secrets,
            default_title=validated_kwargs.default_title,
            display_group=validated_kwargs.display_group,
            include_in_schema=validated_kwargs.include_in_schema,
            args_cls=args_cls,
            args_docs=args_docs,
            rtype=rtype,
            rtype_adapter=rtype_adapter,
            is_template=False,
            origin=origin,
        )

    def _register_udfs_from_package(
        self,
        module: ModuleType,
        *,
        origin: str = "base",
    ) -> None:
        start_time = default_timer()
        # Use rglob to find all python files
        base_path = module.__path__[0]
        base_package = module.__name__
        num_udfs = 0
        # Ignore __init__.py
        module_paths = [
            path for path in Path(base_path).rglob("*.py") if path.stem != "__init__"
        ]
        for path in module_paths:
            logger.info(f"Loading UDFs from {path!s}")
            # Convert path to relative path
            relative_path = path.relative_to(base_path)
            # Create fully qualified module name
            udf_module_parts = list(relative_path.parent.parts) + [relative_path.stem]
            udf_module_name = f"{base_package}.{'.'.join(udf_module_parts)}"
            udf_module = importlib.import_module(udf_module_name)
            # Get all functions in the module
            for name, obj in inspect.getmembers(udf_module):
                # Register the UDF if it is a function and has UDF metadata
                is_func = inspect.isfunction(obj)
                is_udf = hasattr(obj, "__tracecat_udf_key")
                has_udf_kwargs = hasattr(obj, "__tracecat_udf_kwargs")
                if is_func and is_udf and has_udf_kwargs:
                    self._register_udf_from_function(obj, name=name, origin=origin)
                    num_udfs += 1
        time_elapsed = default_timer() - start_time
        logger.info(
            f"✅ Registered {num_udfs} UDFs in {time_elapsed:.2f}s",
            num_udfs=num_udfs,
            time_elapsed=time_elapsed,
        )

    def _load_remote_udfs(self, remote_registry_url: str, module_name: str) -> None:
        """Load udfs from a remote source."""
        with logger.contextualize(remote=remote_registry_url):
            logger.info("Loading remote udfs")
            try:
                logger.trace("BEFORE", keys=self.keys)
                # TODO(perf): Use asyncio
                logger.info("Installing remote udfs", remote=remote_registry_url)
                subprocess.run(
                    ["uv", "pip", "install", "--system", remote_registry_url],
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
                url_obj = urlparse(remote_registry_url)
                # XXX(safety): Reconstruct url without credentials.
                # Note that we do not recommend passing credentials in the url.
                cleaned_url = urlunparse(
                    (url_obj.scheme, url_obj.netloc, url_obj.path, "", "", "")
                )
                self._register_udfs_from_package(module, origin=cleaned_url)
                logger.trace("AFTER", keys=self.keys)
            except ImportError as e:
                logger.error("Error importing remote udfs", error=e)
                raise

    def _load_template_actions(self) -> None:
        """Load template actions from the actions/templates directory."""

        start_time = default_timer()
        # Use importlib to find path to tracecat_registry package
        pkg_root = files("tracecat_registry")
        pkg_path = Path(pkg_root)

        # Load the default templates
        logger.info(f"Loading template actions in {pkg_path!s}")
        # Load all .yml files using rglob
        num_templates = 0
        for file_path in pkg_path.rglob("*.yml"):
            logger.info(f"Loading template {file_path!s}")
            # Load TemplateActionDefinition
            template_action = TemplateAction.from_yaml(file_path)

            key = template_action.definition.action
            if key in self._store:
                # Already registered, skip
                logger.info(f"Template {key!r} already registered, skipping")
                continue

            # Register the action
            defn = template_action.definition
            expectation = defn.expects

            self.register_udf(
                fn=apartial(template_action.run, registry=self),
                key=key,
                namespace=defn.namespace,
                version=None,
                description=defn.description,
                secrets=defn.secrets,
                args_cls=create_expectation_model(expectation, key.replace(".", "__"))
                if expectation
                else BaseModel,
                args_docs={
                    key: schema.description or "-"
                    for key, schema in expectation.items()
                },
                rtype=Any,
                rtype_adapter=TypeAdapter(Any),
                default_title=defn.title,
                display_group=defn.display_group,
                include_in_schema=True,
                is_template=True,
                origin="base",
            )
            num_templates += 1

        time_elapsed = default_timer() - start_time
        logger.info(
            f"✅ Registered {num_templates} template actions in {time_elapsed:.2f}s",
            num_templates=num_templates,
            time_elapsed=time_elapsed,
        )

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


def _udf_slug_camelcase(func: FunctionType, namespace: str) -> str:
    # Use slugify to preprocess the string
    slugified_string = re.sub(r"[^a-zA-Z0-9]+", " ", namespace)
    slugified_name = re.sub(r"[^a-zA-Z0-9]+", " ", func.__name__)
    # Split the slugified string into words
    words = slugified_string.split() + slugified_name.split()

    # Capitalize the first letter of each word except the first word
    # Join the words together without spaces
    return "".join(word.capitalize() for word in words)


def init() -> None:
    """Initialize the registry."""
    registry.init()


registry = Registry()
