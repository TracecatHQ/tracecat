import asyncio
import importlib
import inspect
import json
import re
import subprocess
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from timeit import default_timer
from types import FunctionType, GenericAlias, ModuleType
from typing import Annotated, Any, Literal
from urllib.parse import urlparse, urlunparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    create_model,
)
from tracecat_registry import REGISTRY_VERSION, RegistrySecret
from typing_extensions import Doc

from tracecat import config
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.validation import TemplateValidator
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    ArgsClsT,
    BoundRegistryAction,
    TemplateAction,
)
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN


class RegisterKwargs(BaseModel):
    default_title: str | None
    display_group: str | None
    namespace: str
    description: str
    secrets: list[RegistrySecret] | None
    include_in_schema: bool


class Repository:
    """Registry class to store UDF actions and template actions.

    Responsibilities
    ----------------
    1. Load and register UDFs and template actions from their source (python file, git repo, etc)
    2. Maintain a mapping of each action's name to actual function implementation
    3. Serve function execution requests from a registry manager
    """

    def __init__(
        self, version: str = REGISTRY_VERSION, origin: str = DEFAULT_REGISTRY_ORIGIN
    ):
        self._store: dict[str, BoundRegistryAction[ArgsClsT]] = {}
        self._remote = config.TRACECAT__REMOTE_REPOSITORY_URL
        self._is_initialized: bool = False
        self._version = version
        self._origin = origin
        logger.info("Registry origin", origin=self._origin, version=self._version)

    def __contains__(self, name: str) -> bool:
        return name in self._store

    def __getitem__(self, name: str) -> BoundRegistryAction[ArgsClsT]:
        return self.get(name)

    def __iter__(self):
        return iter(self._store.items())

    def __len__(self) -> int:
        return len(self._store)

    def length(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"Registry(version={self._version}, store={json.dumps([x.action for x in self._store.values()], indent=2)})"

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def store(self) -> dict[str, BoundRegistryAction[ArgsClsT]]:
        return self._store

    @property
    def keys(self) -> list[str]:
        return list(self._store.keys())

    @property
    def version(self) -> str:
        return self._version

    def get(self, name: str) -> BoundRegistryAction[ArgsClsT]:
        """Retrieve a registered udf."""
        return self._store[name]

    def safe_remote_url(self, remote_registry_url: str) -> str:
        """Clean a remote registry url."""
        return safe_url(remote_registry_url)

    def init(
        self,
        include_base: bool = True,
        include_remote: bool = False,
        include_templates: bool = True,
    ) -> None:
        """Initialize the registry."""
        if not self._is_initialized:
            logger.info(
                "Initializing registry",
                version=self.version,
                include_base=include_base,
                include_remote=include_remote,
                include_templates=include_templates,
            )
            # Load udfs
            if include_base:
                self._load_base_udfs()

            # Load remote udfs
            if include_remote and self._remote:
                self._load_remote_udfs(self._remote, module_name="udfs")

            # Load template actions
            if include_templates:
                self._load_base_template_actions()

            logger.info("Registry initialized", num_actions=len(self._store))
            self._is_initialized = True

    def register_udf(
        self,
        *,
        fn: FunctionType,
        name: str,
        type: Literal["udf", "template"],
        namespace: str,
        description: str,
        secrets: list[RegistrySecret] | None,
        args_cls: ArgsClsT,
        args_docs: dict[str, str],
        rtype: type,
        rtype_adapter: TypeAdapter,
        default_title: str | None,
        display_group: str | None,
        include_in_schema: bool,
        template_action: TemplateAction | None = None,
        origin: str = DEFAULT_REGISTRY_ORIGIN,
    ):
        reg_action = BoundRegistryAction(
            fn=fn,
            name=name,
            namespace=namespace,
            version=self.version,
            description=description,
            type=type,
            secrets=secrets,
            args_cls=args_cls,
            args_docs=args_docs,
            rtype_cls=rtype,
            rtype_adapter=rtype_adapter,
            default_title=default_title,
            display_group=display_group,
            origin=origin,
            template_action=template_action,
            include_in_schema=include_in_schema,
        )

        logger.debug(f"Registering UDF {reg_action.action=}")
        self._store[reg_action.action] = reg_action

    def register_template_action(
        self, template_action: TemplateAction, origin: str = DEFAULT_REGISTRY_ORIGIN
    ) -> None:
        """Register a template action."""

        # Register the action
        defn = template_action.definition
        expectation = defn.expects

        self.register_udf(
            fn=Repository._not_implemented,
            type="template",
            name=defn.name,
            namespace=defn.namespace,
            description=defn.description,
            secrets=defn.secrets,
            args_cls=create_expectation_model(
                expectation, defn.action.replace(".", "__")
            )
            if expectation
            else BaseModel,
            args_docs={
                key: schema.description or "-" for key, schema in expectation.items()
            },
            rtype=Any,
            rtype_adapter=TypeAdapter(Any),
            default_title=defn.title,
            display_group=defn.display_group,
            include_in_schema=True,
            template_action=template_action,
            origin=origin,
        )

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

    async def load_from_origin(self) -> None:
        """Load the registry from the origin."""
        if self._origin == DEFAULT_REGISTRY_ORIGIN:
            # This is a builtin registry, nothing to load
            logger.info("Loading builtin registry")
            self._load_base_udfs()
            self._load_base_template_actions()
            return

        # Load from remote
        logger.info("Loading UDFs from origin", origin=self._origin)

        org, repo_name, branch = parse_github_url(self._origin)
        logger.info("Parsed GitHub URL", org=org, package_name=repo_name, branch=branch)

        package_name = config.TRACECAT__REMOTE_REPOSITORY_PACKAGE_NAME or repo_name

        module = await self._load_remote_repository(self._origin, package_name)
        logger.info("Imported remote repository", module_name=module.__name__)
        self._register_udfs_from_package(module, origin=self._origin)

    async def _load_remote_repository(
        self, repository_url: str, module_name: str
    ) -> ModuleType:
        """Load actions from a remote source."""
        cleaned_url = self.safe_remote_url(repository_url)
        logger.info("Loading remote repository")

        try:
            process = await asyncio.create_subprocess_exec(
                "uv",
                "pip",
                "install",
                "--system",
                cleaned_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()

            if process.returncode != 0:
                error_message = stderr.decode().strip()
                logger.error(f"Failed to install repository: {error_message}")
                raise RuntimeError(f"Failed to install repository: {error_message}")

            logger.info("Remote repository installed successfully")
        except Exception as e:
            logger.error(f"Error while fetching repository: {str(e)}")
            raise RuntimeError(f"Error while fetching repository: {str(e)}") from e
        try:
            # Import the module
            logger.info("Importing remote repository module", module_name=module_name)
            module = importlib.import_module(module_name)

            # # Reload the module to ensure fresh execution
            self._register_udfs_from_package(module, origin=cleaned_url)
            logger.trace("AFTER", keys=self.keys)
        except ImportError as e:
            logger.error("Error importing remote udfs", error=e)
            raise
        return module

    def _register_udf_from_function(
        self,
        fn: FunctionType,
        *,
        name: str,
        origin: str = DEFAULT_REGISTRY_ORIGIN,
    ) -> None:
        # Get function metadata
        key = getattr(fn, "__tracecat_udf_key")
        kwargs = getattr(fn, "__tracecat_udf_kwargs")
        logger.info(f"Registering UDF: {key}", key=key, name=name)
        # Add validators to the function
        validated_kwargs = RegisterKwargs.model_validate(kwargs)
        attach_validators(fn, TemplateValidator())
        args_docs = get_signature_docs(fn)
        # Generate the model from the function signature
        args_cls, rtype, rtype_adapter = generate_model_from_function(
            func=fn, namespace=validated_kwargs.namespace
        )

        self.register_udf(
            fn=fn,
            type="udf",
            name=name,
            namespace=validated_kwargs.namespace,
            description=validated_kwargs.description,
            secrets=validated_kwargs.secrets,
            default_title=validated_kwargs.default_title,
            display_group=validated_kwargs.display_group,
            include_in_schema=validated_kwargs.include_in_schema,
            args_cls=args_cls,
            args_docs=args_docs,
            rtype=rtype,
            rtype_adapter=rtype_adapter,
            origin=origin,
        )

    def _register_udfs_from_module(
        self,
        module: ModuleType,
        *,
        origin: str = DEFAULT_REGISTRY_ORIGIN,
    ) -> int:
        num_udfs = 0
        for name, obj in inspect.getmembers(module):
            # Get all functions in the module
            if not inspect.isfunction(obj):
                continue
            _enforce_restrictions(obj)
            is_udf = hasattr(obj, "__tracecat_udf_key")
            has_udf_kwargs = hasattr(obj, "__tracecat_udf_kwargs")
            # Register the UDF if it is a function and has UDF metadata
            if is_udf and has_udf_kwargs:
                self._register_udf_from_function(obj, name=name, origin=origin)
                num_udfs += 1
        return num_udfs

    def _register_udfs_from_package(
        self,
        module: ModuleType,
        *,
        origin: str = DEFAULT_REGISTRY_ORIGIN,
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
            num_registered = self._register_udfs_from_module(udf_module, origin=origin)
            num_udfs += num_registered
        time_elapsed = default_timer() - start_time
        logger.info(
            f"✅ Registered {num_udfs} UDFs in {time_elapsed:.2f}s",
            num_udfs=num_udfs,
            time_elapsed=time_elapsed,
        )

    def _load_remote_udfs(self, remote_registry_url: str, module_name: str) -> None:
        """Load udfs from a remote source."""
        cleaned_url = self.safe_remote_url(remote_registry_url)
        with logger.contextualize(remote=cleaned_url):
            logger.info("Loading remote udfs")
            try:
                logger.trace("BEFORE", keys=self.keys)
                # TODO(perf): Use asyncio
                logger.info("Installing remote udfs", remote=cleaned_url)
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
                self._register_udfs_from_package(module, origin=cleaned_url)
                logger.trace("AFTER", keys=self.keys)
            except ImportError as e:
                logger.error("Error importing remote udfs", error=e)
                raise

    def _load_base_template_actions(self) -> None:
        """Load template actions from the actions/templates directory."""

        start_time = default_timer()
        # Use importlib to find path to tracecat_registry package
        pkg_root = files("tracecat_registry")
        pkg_path = Path(pkg_root)

        n_loaded = self.load_template_actions_from_path(pkg_path)

        time_elapsed = default_timer() - start_time
        logger.info(
            f"✅ Registered {n_loaded} template actions in {time_elapsed:.2f}s",
            num_templates=n_loaded,
            time_elapsed=time_elapsed,
        )

    def load_template_actions_from_path(self, path: Path) -> int:
        """Load template actions from a package."""
        # Load the default templates
        logger.info(f"Loading template actions from {path!s}")
        # Load all .yml files using rglob
        n_loaded = 0
        for file_path in path.rglob("*.y{a,}ml"):
            logger.info(f"Loading template {file_path!s}")
            # Load TemplateActionDefinition
            try:
                template_action = TemplateAction.from_yaml(file_path)
            except ValidationError as e:
                logger.error(
                    f"Could not parse {file_path!s} as template action, skipped",
                    error=e,
                )
                continue
            except Exception as e:
                logger.error(
                    f"Unexpected error loading template action {file_path!s}", error=e
                )
                continue

            key = template_action.definition.action
            if key in self._store:
                # Already registered, skip
                logger.info(f"Template {key!r} already registered, skipping")
                continue

            self.register_template_action(
                template_action, origin=DEFAULT_REGISTRY_ORIGIN
            )
            n_loaded += 1
        return n_loaded

    @staticmethod
    def _not_implemented():
        raise NotImplementedError("Template actions has no direct implementation")


def attach_validators(func: FunctionType, *validators: Callable):
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


def generate_model_from_function(
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


def get_signature_docs(fn: FunctionType) -> dict[str, str]:
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


def _enforce_restrictions(fn: FunctionType) -> FunctionType:
    """
    Ensure that a function does not access os.environ, os.getenv, or import os.

    Parameters
    ----------
    fn : FunctionType
        The function to be checked.

    Returns
    -------
    FunctionType
        The original function if no access to os.environ, os.getenv, or import os is found.

    Raises
    ------
    ValueError
        If the function accesses os.environ, os.getenv, or imports os.
    """
    code = fn.__code__
    names = code.co_names
    path = f"{fn.__module__}.{fn.__qualname__}"
    # consts = code.co_consts
    # Check for import statements of os
    if "os" in names:
        # What would you even need it for?
        logger.warning(
            f"Importing `os` module - use at your own risk! Found in: {path}"
        )

    # Check for direct access to os.environ
    if "os" in names and "environ" in names:
        raise ValueError(
            "`os.environ` usage is not allowed in user-defined code."
            f" Found in: {path}"
        )

    # Check for invocations of os.getenv
    if "os" in names and "getenv" in names:
        raise ValueError(
            "`os.getenv()` usage is not allowed in user-defined code."
            f" Found in: {path}"
        )

    return fn


def safe_url(url: str) -> str:
    """Remove credentials from a url."""
    url_obj = urlparse(url)
    # XXX(safety): Reconstruct url without credentials.
    # Note that we do not recommend passing credentials in the url.
    cleaned_url = urlunparse((url_obj.scheme, url_obj.netloc, url_obj.path, "", "", ""))
    return cleaned_url


def parse_github_url(url: str) -> tuple[str, str, str]:
    """
    Parse a GitHub URL to extract organization, package name, and branch.
    Handles both standard GitHub URLs and 'git+' prefixed URLs with '@' for branch specification.
    Args:
        url (str): The GitHub URL to parse.
    Returns:
        tuple[str, str, str]: A tuple containing (organization, package_name, branch).
    Raises:
        ValueError: If the URL is not a valid GitHub repository URL.
    """

    parsed_url = urlparse(url)
    if parsed_url.netloc != "github.com":
        raise ValueError("Not a valid GitHub URL")

    # Split path and potential branch
    path_and_branch = parsed_url.path.split("@")
    path_parts = path_and_branch[0].strip("/").split("/")

    if len(path_parts) < 2:
        raise ValueError("Invalid GitHub repository URL")

    organization = path_parts[0]
    package_name = path_parts[1]

    # Check for branch in URL
    branch = "main"  # Default to 'main' if no branch is specified
    if len(path_and_branch) > 1:
        branch = path_and_branch[1]
    elif len(path_parts) > 3 and path_parts[2] == "tree":
        branch = path_parts[3]

    return organization, package_name, branch
