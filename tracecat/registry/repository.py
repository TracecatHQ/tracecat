from __future__ import annotations

import asyncio
import importlib
import importlib.resources
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from timeit import default_timer
from types import ModuleType
from typing import Annotated, Any, Literal, TypedDict, cast
from urllib.parse import urlparse, urlunparse

import paramiko
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
from tracecat.config import TRACECAT__ALLOWED_GIT_DOMAINS
from tracecat.contexts import ctx_role
from tracecat.expressions.expectations import create_expectation_model
from tracecat.expressions.validation import TemplateValidator
from tracecat.logger import logger
from tracecat.registry.actions.models import BoundRegistryAction, TemplateAction
from tracecat.registry.constants import (
    CUSTOM_REPOSITORY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
    GIT_SSH_KEY_SECRET_NAME,
)
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryError

ArgsClsT = type[BaseModel]
type F = Callable[..., Any]


class RegisterKwargs(BaseModel):
    namespace: str
    description: str
    default_title: str | None
    display_group: str | None
    doc_url: str | None
    author: str | None
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

    def safe_remote_url(self, remote_registry_url: str) -> str:
        """Clean a remote registry url."""
        return safe_url(remote_registry_url)

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
        doc_url: str,
        author: str,
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
            description=description,
            type=type,
            doc_url=doc_url,
            author=author,
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
            doc_url=defn.doc_url,
            author=defn.author,
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

    async def load_from_origin(self, commit_sha: str | None = None) -> str | None:
        """Load the registry from the origin and return the commit sha."""
        if self._origin == DEFAULT_REGISTRY_ORIGIN:
            # This is a builtin registry, nothing to load
            logger.info("Loading builtin registry")
            self._load_base_udfs()
            self._load_base_template_actions()
            return None

        elif self._origin == CUSTOM_REPOSITORY_ORIGIN:
            raise RegistryError("You cannot sync this repository.")

        # Load from remote
        logger.info("Loading UDFs from origin", origin=self._origin)

        try:
            git_url = parse_git_url(self._origin)
            host = git_url.host
            org = git_url.org
            repo_name = git_url.repo
            branch = git_url.branch
        except ValueError as e:
            raise RegistryError(
                "Invalid Git repository URL. Please provide a valid Git SSH URL (git+ssh)."
            ) from e
        logger.debug(
            "Parsed Git repository URL",
            host=host,
            org=org,
            package_name=repo_name,
            branch=branch,
        )
        package_name = config.TRACECAT__REMOTE_REPOSITORY_PACKAGE_NAME or repo_name

        cleaned_url = self.safe_remote_url(self._origin)
        logger.debug("Cleaned URL", url=cleaned_url)
        commit_sha = await self._install_remote_repository(
            host=host, repo_url=cleaned_url, commit_sha=commit_sha
        )
        module = await self._load_remote_repository(cleaned_url, package_name)
        logger.info(
            "Imported and reloaded remote repository",
            module_name=module.__name__,
            package_name=package_name,
        )
        return commit_sha

    async def _install_remote_repository(
        self, host: str, repo_url: str, commit_sha: str | None = None
    ) -> str:
        """Install the remote repository into the filesystem and return the commit sha."""

        logger.info("Getting SSH key", role=self.role)
        async with SecretsService.with_session(role=self.role) as service:
            secret = await service.get_ssh_key(GIT_SSH_KEY_SECRET_NAME)

        async with temporary_ssh_agent() as env:
            logger.info("Entered temporary SSH agent context")
            await add_ssh_key_to_agent(secret.reveal().value, env=env)
            await add_host_to_known_hosts(host, env=env)
            if commit_sha is None:
                commit_sha = await get_git_repository_sha(repo_url, env=env)
            await install_remote_repository(repo_url, commit_sha=commit_sha, env=env)
        return commit_sha

    async def _load_remote_repository(
        self, repo_url: str, module_name: str
    ) -> ModuleType:
        """Load remote repository module into memory.

        Args:
            repo_url (str): The URL of the remote git repository
            module_name (str): The name of the Python module to import

        Returns:
            ModuleType: The imported Python module containing the actions

        Raises:
            ImportError: If there is an error importing the module
        """
        try:
            logger.info("Importing remote repository module", module_name=module_name)
            # We only need to call this at the root level because
            # this deletes all the submodules as well
            package_or_module = import_and_reload(module_name)

            # Reload the module to ensure fresh execution
            logger.info("Registering UDFs from package", module_name=package_or_module)
            self._register_udfs_from_package(package_or_module, origin=repo_url)
        except ImportError as e:
            logger.error("Error importing remote repository udfs", error=e)
            raise

        try:
            self.load_template_actions_from_package(
                package_name=module_name, origin=repo_url
            )
        except Exception as e:
            logger.error("Error importing remote repository template actions", error=e)
            raise
        return package_or_module

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

        return self.load_template_actions_from_package(
            package_name=DEFAULT_REGISTRY_ORIGIN, origin=DEFAULT_REGISTRY_ORIGIN
        )

    def load_template_actions_from_package(
        self, *, package_name: str, origin: str
    ) -> None:
        """Load template actions from a package."""
        start_time = default_timer()
        pkg_root = importlib.resources.files(package_name)
        pkg_path = Path(pkg_root)  # type: ignore
        n_loaded = self.load_template_actions_from_path(path=pkg_path, origin=origin)
        time_elapsed = default_timer() - start_time
        if n_loaded > 0:
            logger.info(
                f"✅ Registered {n_loaded} template actions in {time_elapsed:.2f}s",
                num_templates=n_loaded,
                time_elapsed=time_elapsed,
                package_name=package_name,
            )
        else:
            logger.info(
                "No template actions found in package", package_name=package_name
            )

    def load_template_actions_from_path(self, *, path: Path, origin: str) -> int:
        """Load template actions from a package."""
        # Load all .yml files using rglob
        n_loaded = 0
        all_paths = chain(path.rglob("*.yml"), path.rglob("*.yaml"))
        for file_path in all_paths:
            logger.debug("Loading template action from path", path=file_path)
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
                    "Unexpected error loading template action",
                    error=e,
                    path=file_path,
                )
                continue

            key = template_action.definition.action
            if key in self._store:
                logger.debug("Template action already registered, skipping", key=key)
                continue

            self.register_template_action(template_action, origin=origin)
            n_loaded += 1
        return n_loaded

    @staticmethod
    def _not_implemented():
        raise NotImplementedError("Template actions has no direct implementation")


def import_and_reload(module_name: str) -> ModuleType:
    """Import and reload a module.

    Steps
    -----
    1. Remove the module from sys.modules
    2. Import the module
    3. Reload the module
    4. Add the module to sys.modules
    5. Return the reloaded module
    """
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


@dataclass
class GitUrl:
    host: str
    org: str
    repo: str
    branch: str


def parse_git_url(url: str) -> GitUrl:
    """
    Parse a Git repository URL to extract organization, package name, and branch.
    Handles Git SSH URLs with 'git+ssh' prefix and optional '@' for branch specification.

    Args:
        url (str): The repository URL to parse.

    Returns:
        tuple[str, str, str, str]: A tuple containing (host, organization, package_name, branch).

    Raises:
        ValueError: If the URL is not a valid repository URL.
    """
    pattern = r"^git\+ssh://git@(?P<host>[^/]+)/(?P<org>[^/]+)/(?P<repo>[^/@]+?)(?:\.git)?(?:@(?P<branch>[^/]+))?$"

    if match := re.match(pattern, url):
        host = match.group("host")
        if host not in TRACECAT__ALLOWED_GIT_DOMAINS:
            raise ValueError(
                f"Domain {host} not in allowed domains. Must be configured in TRACECAT__ALLOWED_GIT_DOMAINS."
            )

        return GitUrl(
            host=host,
            org=match.group("org"),
            repo=match.group("repo"),
            branch=match.group("branch") or "main",
        )

    raise ValueError(f"Unsupported URL format: {url}. Must be a valid Git SSH URL.")


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


class SshEnv(TypedDict):
    SSH_AUTH_SOCK: str
    SSH_AGENT_PID: str


@asynccontextmanager
async def temporary_ssh_agent() -> AsyncIterator[SshEnv]:
    """Set up a temporary SSH agent and yield the SSH_AUTH_SOCK."""
    original_ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK")
    try:
        # Start ssh-agent
        logger.debug("Starting ssh-agent")
        try:
            # We're using asyncio.to_thread to run the ssh-agent in a separate thread
            # because for some reason, asyncio.create_subprocess_exec stalls and times out
            result = await asyncio.to_thread(
                subprocess.run,
                ["ssh-agent", "-s"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10.0,
            )
            stdout = result.stdout
            stderr = result.stderr
            logger.debug("Started ssh-agent process", stdout=stdout, stderr=stderr)
        except subprocess.TimeoutExpired as e:
            logger.error("SSH-agent execution timed out")
            raise RuntimeError("SSH-agent execution timed out") from e
        except subprocess.CalledProcessError as e:
            logger.error("Failed to start ssh-agent", stderr=e.stderr)
            raise RuntimeError("Failed to start ssh-agent") from e

        ssh_auth_sock = stdout.split("SSH_AUTH_SOCK=")[1].split(";")[0]
        ssh_agent_pid = stdout.split("SSH_AGENT_PID=")[1].split(";")[0]

        logger.debug(
            "Started ssh-agent",
            SSH_AUTH_SOCK=ssh_auth_sock,
            SSH_AGENT_PID=ssh_agent_pid,
        )
        yield SshEnv(
            SSH_AUTH_SOCK=ssh_auth_sock,
            SSH_AGENT_PID=ssh_agent_pid,
        )
    finally:
        if "SSH_AGENT_PID" in os.environ:
            logger.debug("Killing ssh-agent")
            await asyncio.create_subprocess_exec("ssh-agent", "-k")

        # Restore original SSH_AUTH_SOCK if it existed
        if original_ssh_auth_sock is not None:
            logger.debug(
                "Restoring original SSH_AUTH_SOCK", SSH_AUTH_SOCK=original_ssh_auth_sock
            )
            os.environ["SSH_AUTH_SOCK"] = original_ssh_auth_sock
        else:
            os.environ.pop("SSH_AUTH_SOCK", None)
        logger.debug("Killed ssh-agent")


async def add_ssh_key_to_agent(key_data: str, env: SshEnv) -> None:
    """Add the SSH key to the agent then remove it."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_key_file:
        temp_key_file.write(key_data)
        temp_key_file.write("\n")
        temp_key_file.flush()
        logger.debug("Added SSH key to temp file", key_file=temp_key_file.name)
        os.chmod(temp_key_file.name, 0o600)

        try:
            # Validate the key using paramiko
            paramiko.Ed25519Key.from_private_key_file(temp_key_file.name)
        except paramiko.SSHException as e:
            logger.error(f"Invalid SSH key: {str(e)}")
            raise

        try:
            process = await asyncio.create_subprocess_exec(
                "ssh-add",
                temp_key_file.name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=cast(Mapping[str, str], env),
            )
            _, stderr = await process.communicate()

            if process.returncode != 0:
                raise Exception(f"Failed to add SSH key: {stderr.decode().strip()}")

            logger.info("Added SSH key to agent")
        except Exception as e:
            logger.error("Error adding SSH key", error=e)
            raise


async def add_host_to_known_hosts(url: str, *, env: SshEnv) -> None:
    """Add the host to the known hosts file."""
    try:
        # Ensure the ~/.ssh directory exists
        ssh_dir = Path.home() / ".ssh"
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        known_hosts_file = ssh_dir / "known_hosts"

        # Use ssh-keyscan to get the host key
        process = await asyncio.create_subprocess_exec(
            "ssh-keyscan",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=cast(Mapping[str, str], env),
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise Exception(f"Failed to get host key: {stderr.decode().strip()}")

        # Append the host key to the known_hosts file
        with known_hosts_file.open("a") as f:
            f.write(stdout.decode())

        logger.info("Added host to known hosts", url=url)
    except Exception as e:
        logger.error(f"Error adding host to known hosts: {str(e)}")
        raise


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
            env=cast(Mapping[str, str], os.environ.copy() | env),
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


async def get_git_repository_sha(repo_url: str, env: SshEnv) -> str:
    """Get the SHA of the HEAD commit of a Git repository."""
    try:
        # Use git ls-remote to get the HEAD SHA without cloning
        process = await asyncio.create_subprocess_exec(
            "git",
            "ls-remote",
            repo_url,
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,  # type: ignore
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_message = stderr.decode().strip()
            raise RuntimeError(f"Failed to get repository SHA: {error_message}")

        # The output format is: "<SHA>\tHEAD"
        sha = stdout.decode().split()[0]
        return sha

    except Exception as e:
        logger.error("Error getting repository SHA", error=str(e))
        raise RuntimeError(f"Error getting repository SHA: {str(e)}") from e
