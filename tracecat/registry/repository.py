from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import re
import sys
from collections.abc import Callable
from itertools import chain
from pathlib import Path
from timeit import default_timer
from types import ModuleType
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    create_model,
)
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry import RegistrySecret
from typing_extensions import Doc

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.validation import TemplateValidator
from tracecat.git import GitUrl, get_git_repository_sha, parse_git_url
from tracecat.logger import logger
from tracecat.parse import safe_url
from tracecat.registry.actions.models import BoundRegistryAction, TemplateAction
from tracecat.registry.constants import (
    CUSTOM_REPOSITORY_ORIGIN,
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.settings.service import get_setting
from tracecat.ssh import SshEnv, ssh_context
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryError

ArgsClsT = type[BaseModel]
type F = Callable[..., Any]


class RegisterKwargs(BaseModel):
    namespace: str
    description: str
    default_title: str | None = None
    display_group: str | None = None
    doc_url: str | None = None
    author: str | None = None
    deprecated: str | None = None
    secrets: list[RegistrySecret] | None = None
    include_in_schema: bool = True


class Repository:
    """Registry class to store UDF actions and template actions.

    Responsibilities
    ----------------
    1. Load and register UDFs and template actions from their source (python file, git repo, etc)
    2. Maintain a mapping of each action's name to actual function implementation
    3. Serve function execution requests from a registry manager
    """

    def __init__(self, origin: str = DEFAULT_REGISTRY_ORIGIN, role: Role | None = None):
        self._store: dict[str, BoundRegistryAction[ArgsClsT]] = {}
        self._is_initialized: bool = False
        self._origin = origin
        self.role = role or ctx_role.get()

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
        return f"Registry(origin={self._origin}, store={json.dumps([x.action for x in self._store.values()], indent=2)})"

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def store(self) -> dict[str, BoundRegistryAction[ArgsClsT]]:
        return self._store

    @property
    def keys(self) -> list[str]:
        return list(self._store.keys())

    def get(self, name: str) -> BoundRegistryAction[ArgsClsT]:
        """Retrieve a registered udf."""
        return self._store[name]

    def init(self, include_base: bool = True, include_templates: bool = True) -> None:
        """Initialize the registry."""
        if not self._is_initialized:
            logger.info(
                "Initializing registry",
                include_base=include_base,
                include_templates=include_templates,
            )
            # Load udfs
            if include_base:
                self._load_base_udfs()

            # Load template actions
            if include_templates:
                self._load_base_template_actions()

            logger.info("Registry initialized", num_actions=len(self._store))
            self._is_initialized = True

    def register_udf(
        self,
        *,
        fn: F,
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
        doc_url: str | None,
        author: str | None,
        deprecated: str | None,
        include_in_schema: bool,
        template_action: TemplateAction | None = None,
        origin: str = DEFAULT_REGISTRY_ORIGIN,
    ):
        reg_action = BoundRegistryAction(
            fn=fn,
            name=name,
            namespace=namespace,
            description=description,
            type=type,
            doc_url=doc_url,
            author=author,
            deprecated=deprecated,
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

        logger.debug(f"Registering action {reg_action.action=}")
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
            doc_url=defn.doc_url,
            author=defn.author,
            deprecated=defn.deprecated,
            secrets=defn.secrets,
            args_cls=create_expectation_model(
                expectation, defn.action.replace(".", "__")
            )
            if expectation
            else BaseModel,
            args_docs={
                key: schema.description or "-" for key, schema in expectation.items()
            },
            rtype=Any,  # type: ignore
            rtype_adapter=TypeAdapter(Any),
            default_title=defn.title,
            display_group=defn.display_group,
            include_in_schema=True,
            template_action=template_action,
            origin=origin,
        )

    def _load_base_udfs(self) -> None:
        """Load all udfs and template actions into the registry."""
        # Load udfs
        logger.info("Loading base UDFs")
        import tracecat_registry

        self._register_udfs_from_package(tracecat_registry)

    async def load_from_origin(self, commit_sha: str | None = None) -> str | None:
        """Load the registry from the origin and return the commit sha.

        If we pass a local directory, load the files directly.
        """
        if self._origin == DEFAULT_REGISTRY_ORIGIN:
            # This is a builtin registry, nothing to load
            logger.info("Loading builtin registry")
            self._load_base_udfs()
            self._load_base_template_actions()
            return None

        elif self._origin == CUSTOM_REPOSITORY_ORIGIN:
            raise RegistryError("This repository cannot be synced.")
        # Handle local git repositories
        elif self._origin == DEFAULT_LOCAL_REGISTRY_ORIGIN:
            # The local repo doesn't have to be a git repo, but it should be a directory
            if not config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
                raise RegistryError(
                    "Local repository is not enabled on this instance. "
                    "Please set TRACECAT__LOCAL_REPOSITORY_ENABLED=true "
                    "and ensure TRACECAT__LOCAL_REPOSITORY_PATH points to a valid Python package."
                )
            repo_path = Path(config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH)

            if not repo_path.exists():
                raise RegistryError(f"Local git repository not found: {repo_path}")

            # Check that there's either pyproject.toml or setup.py
            if not repo_path.joinpath("pyproject.toml").exists():
                # expand the path to the host path
                if host_path := config.TRACECAT__LOCAL_REPOSITORY_PATH:
                    host_path = Path(host_path).expanduser()
                    logger.debug("Host path", host_path=host_path)
                raise RegistryError(
                    "Local repository does not contain pyproject.toml. "
                    "Please ensure TRACECAT__LOCAL_REPOSITORY_PATH points to a valid Python package."
                    f"Host path: {host_path}"
                )

            dot_git = repo_path.joinpath(".git")
            package_name = repo_path.name
            if dot_git.exists():
                # Use the repository directory name as the package name
                logger.debug(
                    "Using local git repository",
                    repo_path=str(repo_path),
                    package_name=package_name,
                )

                # Install the local repository in editable mode
                env = {"GIT_DIR": dot_git.as_posix()}
                if commit_sha is None:
                    # Get the current commit SHA
                    process = await asyncio.create_subprocess_exec(
                        "git",
                        "rev-parse",
                        "HEAD",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                        cwd=repo_path.as_posix(),
                    )
                    stdout, _ = await process.communicate()
                    if process.returncode != 0:
                        raise RegistryError("Failed to get current commit SHA")
                    commit_sha = stdout.decode().strip()
            else:
                logger.info(
                    "Local repository is not a git repository, skipping commit SHA"
                )

            # Install the package in editable mode
            extra_args = []
            if config.TRACECAT__APP_ENV == "production":
                # We set PYTHONUSERBASE in the prod Dockerfile
                # Otherwise default to the user's home dir at ~/.local
                python_user_base = (
                    os.getenv("PYTHONUSERBASE")
                    or Path.home().joinpath(".local").as_posix()
                )
                logger.debug(
                    "Installing to PYTHONUSERBASE", python_user_base=python_user_base
                )
                extra_args = ["--target", python_user_base]

            cmd = ["uv", "pip", "install", "--system", "--refresh", "--editable"]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                repo_path.as_posix(),
                *extra_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                error_message = stderr.decode().strip()
                raise RegistryError(
                    f"Failed to install local repository: {error_message}"
                )

            # Load the repository module
            logger.debug("Loading local repository", repo_path=repo_path.as_posix())
            module = await self._load_repository(repo_path.as_posix(), package_name)
            logger.info(
                "Imported and reloaded local repository",
                module_name=module.__name__,
                package_name=package_name,
                commit_sha=commit_sha,
            )
            return None
        elif self._origin.startswith("git+ssh://"):
            # Load from remote
            logger.info("Loading UDFs from origin", origin=self._origin)
            allowed_domains = cast(
                set[str],
                await get_setting(
                    "git_allowed_domains",
                    role=self.role,
                    # TODO: Deprecate in future version
                    default=config.TRACECAT__ALLOWED_GIT_DOMAINS,
                )
                or {"github.com"},
            )

            cleaned_url = safe_url(self._origin)
            try:
                git_url = parse_git_url(cleaned_url, allowed_domains=allowed_domains)
                host = git_url.host
                org = git_url.org
                repo_name = git_url.repo
            except ValueError as e:
                raise RegistryError(
                    "Invalid Git repository URL. Please provide a valid Git SSH URL (git+ssh)."
                ) from e
            package_name = (
                await get_setting(
                    "git_repo_package_name",
                    role=self.role,
                    # TODO: Deprecate in future version
                    default=config.TRACECAT__REMOTE_REPOSITORY_PACKAGE_NAME,
                )
                or repo_name
            )
            logger.debug(
                "Parsed Git repository URL",
                host=host,
                org=org,
                repo=repo_name,
                package_name=package_name,
            )

            logger.debug("Git URL", git_url=git_url)
            commit_sha = await self._install_remote_repository(
                git_url=git_url, commit_sha=commit_sha
            )
            module = await self._load_repository(cleaned_url, package_name)
            logger.info(
                "Imported and reloaded remote repository",
                module_name=module.__name__,
                package_name=package_name,
                commit_sha=commit_sha,
            )
            return commit_sha
        else:
            raise RegistryError(f"Unsupported origin: {self._origin}.")

    async def _install_remote_repository(
        self, git_url: GitUrl, commit_sha: str | None = None
    ) -> str:
        """Install the remote repository into the filesystem and return the commit sha."""

        url = git_url.to_url()
        async with (
            get_async_session_context_manager() as session,
            ssh_context(role=self.role, git_url=git_url, session=session) as env,
        ):
            if env is None:
                raise RegistryError("No SSH key found")
            if commit_sha is None:
                commit_sha = await get_git_repository_sha(url, env=env)
            await install_remote_repository(url, commit_sha=commit_sha, env=env)
        return commit_sha

    async def _load_repository(self, repo_url: str, module_name: str) -> ModuleType:
        """Load repository module into memory.
        Args:
            repo_url (str): The URL of the repository
            module_name (str): The name of the Python module to import

        Returns:
            ModuleType: The imported Python module containing the actions

        Raises:
            ImportError: If there is an error importing the module
        """
        try:
            logger.info("Importing repository module", module_name=module_name)
            # We only need to call this at the root level because
            # this deletes all the submodules as well
            pkg_or_mod = import_and_reload(module_name)

            # Reload the module to ensure fresh execution
            logger.info("Registering UDFs from package", module_name=pkg_or_mod)
            self._register_udfs_from_package(pkg_or_mod, origin=repo_url)
        except ImportError as e:
            logger.error(
                "Error importing repository udfs",
                error=e,
                module_name=module_name,
                origin=repo_url,
            )
            raise

        try:
            self.load_template_actions_from_package(pkg_or_mod, origin=repo_url)
        except Exception as e:
            logger.error(
                "Error importing repository template actions",
                error=e,
                module_name=module_name,
                origin=repo_url,
            )
            raise
        return pkg_or_mod

    def _register_udf_from_function(
        self,
        fn: F,
        *,
        name: str,
        origin: str = DEFAULT_REGISTRY_ORIGIN,
    ) -> None:
        # Get function metadata
        key = getattr(fn, "__tracecat_udf_key")
        kwargs = getattr(fn, "__tracecat_udf_kwargs")
        logger.debug("Registering UDF", key=key, name=name)
        # Add validators to the function
        validated_kwargs = RegisterKwargs.model_validate(kwargs)
        attach_validators(fn, TemplateValidator())
        args_docs = get_signature_docs(fn)
        # Generate the model from the function signature
        args_cls, rtype, rtype_adapter = generate_model_from_function(
            func=fn, udf_kwargs=validated_kwargs
        )

        self.register_udf(
            fn=fn,
            type="udf",
            name=name,
            namespace=validated_kwargs.namespace,
            description=validated_kwargs.description,
            doc_url=validated_kwargs.doc_url,
            author=validated_kwargs.author,
            deprecated=validated_kwargs.deprecated,
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
        # Ignore __init__.py and __main__.py
        exclude_filenames = ("__init__", "__main__")
        # Ignore CLI files
        exclude_prefixes = (f"{base_path}/cli",)

        for path in Path(base_path).rglob("*.py"):
            if path.stem in exclude_filenames:
                logger.debug("Skipping excluded filename", path=path)
                continue
            p_str = path.as_posix()
            if any(p_str.startswith(prefix) for prefix in exclude_prefixes):
                logger.debug("Skipping excluded prefix", path=path)
                continue
            logger.info(f"Loading UDFs from {path!s}")
            # Convert path to relative path
            relative_path = path.relative_to(base_path)
            # Create fully qualified module name
            udf_module_parts = list(relative_path.parent.parts) + [relative_path.stem]
            udf_module_name = f"{base_package}.{'.'.join(udf_module_parts)}"
            module = import_and_reload(udf_module_name)
            num_registered = self._register_udfs_from_module(module, origin=origin)
            num_udfs += num_registered
        time_elapsed = default_timer() - start_time
        logger.info(
            f"✅ Registered {num_udfs} UDFs in {time_elapsed:.2f}s",
            num_udfs=num_udfs,
            time_elapsed=time_elapsed,
        )

    def _load_base_template_actions(self) -> None:
        """Load template actions from the actions/templates directory."""

        module = import_and_reload(DEFAULT_REGISTRY_ORIGIN)
        self.load_template_actions_from_package(module, origin=DEFAULT_REGISTRY_ORIGIN)

    def load_template_actions_from_package(
        self, module: ModuleType, origin: str
    ) -> None:
        """Load template actions from a package."""
        start_time = default_timer()
        base_path = module.__path__[0]
        base_package = module.__name__
        pkg_path = Path(base_path)
        n_loaded = self.load_template_actions_from_path(path=pkg_path, origin=origin)
        time_elapsed = default_timer() - start_time
        if n_loaded > 0:
            logger.info(
                f"✅ Registered {n_loaded} template actions in {time_elapsed:.2f}s",
                num_templates=n_loaded,
                time_elapsed=time_elapsed,
                package_name=base_package,
            )
        else:
            logger.info(
                "No template actions found in package", package_name=base_package
            )

    def load_template_action_from_file(
        self, file_path: Path, origin: str, *, overwrite: bool = True
    ) -> TemplateAction | None:
        """Load a template action from a YAML file.

        Args:
            file_path: Path to the YAML file

        Returns:
            The loaded template action, or None if loading failed
        """
        logger.debug("Loading template action from path", path=file_path)
        try:
            template_action = TemplateAction.from_yaml(file_path)
        except ValidationError as e:
            logger.error(
                f"Could not parse {file_path!s} as template action, skipped",
                error=e,
            )
            return None
        except Exception as e:
            logger.error(
                "Unexpected error loading template action",
                error=e,
                path=file_path,
            )
            return None
        key = template_action.definition.action
        if key in self._store and not overwrite:
            logger.debug("Template action already registered, skipping", key=key)
            return None
        else:
            logger.debug("Overwriting template action", key=key)

        self.register_template_action(template_action, origin=origin)
        return template_action

    def load_template_actions_from_path(
        self,
        *,
        path: Path,
        origin: str,
        ignore_path: str = "schemas",
        overwrite: bool = True,
    ) -> int:
        """Load template actions from a package."""
        n_loaded = 0
        all_paths = chain(path.rglob("*.yml"), path.rglob("*.yaml"))
        for file_path in all_paths:
            if ignore_path in file_path.parts:
                continue

            template_action = self.load_template_action_from_file(
                file_path, origin, overwrite=overwrite
            )
            if template_action is None:
                continue

            n_loaded += 1
        return n_loaded

    @staticmethod
    def _not_implemented():
        raise NotImplementedError("Template actions has no direct implementation")


def import_and_reload(module_name: str) -> ModuleType:
    """Import and reload a module."""
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    reloaded_module = importlib.reload(module)
    sys.modules[module_name] = reloaded_module
    return reloaded_module


def attach_validators(func: F, *validators: Any):
    sig = inspect.signature(func)

    new_annotations = {
        name: Annotated[param.annotation, *validators]
        for name, param in sig.parameters.items()
    }
    if sig.return_annotation is not sig.empty:
        new_annotations["return"] = sig.return_annotation
    func.__annotations__ = new_annotations


def generate_model_from_function(
    func: F, udf_kwargs: RegisterKwargs
) -> tuple[type[BaseModel], Any, TypeAdapter]:
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
        _udf_slug_camelcase(func, udf_kwargs.namespace),
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    # Capture the return type of the function
    rtype = sig.return_annotation if sig.return_annotation is not sig.empty else Any
    rtype_adapter = TypeAdapter(rtype)

    return input_model, rtype, rtype_adapter


def get_signature_docs(fn: F) -> dict[str, str]:
    param_docs = {}

    sig = inspect.signature(fn)
    for name, param in sig.parameters.items():
        if hasattr(param.annotation, "__metadata__"):
            for meta in param.annotation.__metadata__:
                if isinstance(meta, Doc):
                    param_docs[name] = meta.documentation
    return param_docs


def _udf_slug_camelcase(func: F, namespace: str) -> str:
    # Use slugify to preprocess the string
    slugified_string = re.sub(r"[^a-zA-Z0-9]+", " ", namespace)
    slugified_name = re.sub(r"[^a-zA-Z0-9]+", " ", func.__name__)
    # Split the slugified string into words
    words = slugified_string.split() + slugified_name.split()

    # Capitalize the first letter of each word except the first word
    # Join the words together without spaces
    return "".join(word.capitalize() for word in words)


def _enforce_restrictions(fn: F) -> F:
    """
    Ensure that a function does not access os.environ, os.getenv, or import os.

    Parameters
    ----------
    fn : F
        The function to be checked.

    Returns
    -------
    F
        The original function if no access to os.environ, os.getenv, or imports os is found.

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
            f"`os.environ` usage is not allowed in user-defined code. Found in: {path}"
        )

    # Check for invocations of os.getenv
    if "os" in names and "getenv" in names:
        raise ValueError(
            f"`os.getenv()` usage is not allowed in user-defined code. Found in: {path}"
        )

    return fn


async def ensure_base_repository(
    *,
    session: AsyncSession,
    role: Role | None = None,
    origin: str = DEFAULT_REGISTRY_ORIGIN,
):
    service = RegistryReposService(session, role=role)
    # Check if the base registry repository already exists
    if await service.get_repository(origin) is None:
        # If it doesn't exist, create the base registry repository
        await service.create_repository(RegistryRepositoryCreate(origin=origin))
        logger.info("Created base registry repository", origin=origin)
    else:
        logger.info("Base registry repository already exists", origin=origin)


async def install_remote_repository(
    repo_url: str, commit_sha: str, env: SshEnv
) -> None:
    logger.info("Loading remote repository", url=repo_url, commit_sha=commit_sha)

    cmd = ["uv", "pip", "install", "--system", "--refresh"]
    extra_args = []
    if config.TRACECAT__APP_ENV == "production":
        # We set PYTHONUSERBASE in the prod Dockerfile
        # Otherwise default to the user's home dir at ~/.local
        python_user_base = (
            os.getenv("PYTHONUSERBASE") or Path.home().joinpath(".local").as_posix()
        )
        logger.trace("Installing to PYTHONUSERBASE", python_user_base=python_user_base)
        extra_args = ["--target", python_user_base]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            *extra_args,
            f"{repo_url}@{commit_sha}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy() | env.to_dict(),
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
