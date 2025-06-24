import asyncio
import re
from dataclasses import dataclass
from typing import cast

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.settings.service import get_setting_cached
from tracecat.ssh import SshEnv
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatSettingsError

GIT_SSH_URL_REGEX = re.compile(
    r"^git\+ssh://git@(?P<host>[^/]+)/(?P<org>[^/]+)/(?P<repo>[^/@]+?)(?:\.git)?(?:@(?P<ref>[^/]+))?$"
)
"""Git SSH URL with git user and optional ref."""


@dataclass
class GitUrl:
    host: str
    org: str
    repo: str
    ref: str | None = None

    def to_url(self) -> str:
        base = f"git+ssh://git@{self.host}/{self.org}/{self.repo}.git"
        return f"{base}@{self.ref}" if self.ref else base


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
            env=env.to_dict(),
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_message = stderr.decode().strip()
            raise RuntimeError(f"Failed to get repository SHA: {error_message}")

        # The output format is: "<SHA>\tHEAD"
        ref = stdout.decode().split()[0]
        return ref

    except Exception as e:
        logger.error("Error getting repository SHA", error=str(e))
        raise RuntimeError(f"Error getting repository SHA: {str(e)}") from e


def parse_git_url(url: str, *, allowed_domains: set[str] | None = None) -> GitUrl:
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

    if match := GIT_SSH_URL_REGEX.match(url):
        host = match.group("host")
        org = match.group("org")
        repo = match.group("repo")
        ref = match.group("ref")

        if (
            not isinstance(host, str)
            or not isinstance(org, str)
            or not isinstance(repo, str)
        ):
            raise ValueError(f"Invalid Git URL: {url}")

        if allowed_domains and host not in allowed_domains:
            raise ValueError(
                f"Domain {host} not in allowed domains. Must be configured in `git_allowed_domains` organization setting."
            )

        return GitUrl(host=host, org=org, repo=repo, ref=ref)

    raise ValueError(f"Unsupported URL format: {url}. Must be a valid Git SSH URL.")


async def prepare_git_url(role: Role | None = None) -> GitUrl | None:
    """Construct the runtime environment
    Deps:
    In the new pull-model registry, the execution environment is ALL the registries
    1. Tracecat registry
    2. User's custom template registry
    3. User's custom UDF registry (github)

    Why?
    Since we no longer depend on the user to push to executor, the db repos are now
    the source of truth.
    """
    role = role or ctx_role.get()

    # Handle the git repo
    url = await get_setting_cached(
        "git_repo_url",
    )
    if not url or not isinstance(url, str):
        logger.debug("No git URL found")
        return None

    logger.debug("Runtime environment", url=url)

    allowed_domains_setting = await get_setting_cached(
        "git_allowed_domains",
        # TODO: Deprecate in future version
        # Must be hashable
        default=frozenset(config.TRACECAT__ALLOWED_GIT_DOMAINS),
    )
    allowed_domains = cast(set[str], allowed_domains_setting or {"github.com"})

    # Grab the sha
    # Find the repository that has the same origin
    sha = None
    async with RegistryReposService.with_session(role=role) as service:
        repo = await service.get_repository(origin=url)
        sha = repo.commit_sha if repo else None

    try:
        # Validate
        git_url = parse_git_url(url, allowed_domains=allowed_domains)
    except ValueError as e:
        raise TracecatSettingsError(str(e)) from e
    git_url.ref = sha
    return git_url
