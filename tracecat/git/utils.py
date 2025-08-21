import asyncio
from typing import cast

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.git.constants import GIT_SSH_URL_REGEX
from tracecat.git.models import GitUrl
from tracecat.logger import logger
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.settings.service import get_setting_cached
from tracecat.ssh import SshEnv
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatSettingsError


def parse_git_url(url: str, *, allowed_domains: set[str] | None = None) -> GitUrl:
    """Parse a Git repository URL to extract components.

    Handles Git SSH URLs with 'git+ssh' prefix and optional '@' for branch specification.
    Supports nested groups (GitLab), ports, and various URL structures.

    Args:
        url: The repository URL to parse.
        allowed_domains: Set of allowed domains. If provided, the host must be in this set.

    Returns:
        GitUrl object with parsed components.

    Raises:
        ValueError: If the URL is not a valid repository URL or host not in allowed domains.
    """
    if match := GIT_SSH_URL_REGEX.match(url):
        host = match.group("host")
        path = match.group("path")
        ref = match.group("ref")

        if not isinstance(host, str) or not isinstance(path, str):
            raise ValueError(f"Invalid Git URL: {url}")

        # Split the path to separate org/groups from repo name
        # The last segment is the repo, everything else is the org/group path
        path_parts = path.split("/")
        if len(path_parts) < 2:
            raise ValueError(f"Invalid Git URL path format: {url}")

        repo = path_parts[-1]
        org = "/".join(path_parts[:-1])

        if allowed_domains and host not in allowed_domains:
            raise ValueError(
                f"Domain {host} not in allowed domains. Must be configured in `git_allowed_domains` organization setting."
            )

        return GitUrl(host=host, org=org, repo=repo, ref=ref)

    raise ValueError(f"Unsupported URL format: {url}. Must be a valid Git SSH URL.")


async def resolve_git_ref(
    repo_url: str, *, ref: str | None, env: dict[str, str], timeout: float = 20.0
) -> str:
    """Resolve a Git reference to its SHA.

    Args:
        repo_url: Git repository URL.
        ref: Git reference to resolve. If None, resolves HEAD.
        env: Environment variables for the git command.
        timeout: Command timeout in seconds.

    Returns:
        SHA string of the resolved reference.

    Raises:
        RuntimeError: If git command fails or reference cannot be resolved.
    """
    try:
        if ref is None:
            # Resolve HEAD
            args = ["git", "ls-remote", repo_url, "HEAD"]
        else:
            # Try refs/heads/<ref> first, then <ref> directly
            args = ["git", "ls-remote", repo_url, f"refs/heads/{ref}"]

        code, stdout, stderr = await run_git(args, env=env, timeout=timeout)

        if code != 0:
            if ref is not None:
                # Try with ref directly if refs/heads/<ref> failed
                args = ["git", "ls-remote", repo_url, ref]
                code, stdout, stderr = await run_git(args, env=env, timeout=timeout)

                if code != 0:
                    raise RuntimeError(
                        f"Failed to resolve git ref '{ref}': {stderr.strip()}"
                    )
            else:
                raise RuntimeError(f"Failed to resolve git HEAD: {stderr.strip()}")

        # Parse the output to get SHA
        lines = stdout.strip().split("\n")
        if not lines or not lines[0]:
            raise RuntimeError(f"No output from git ls-remote for ref: {ref or 'HEAD'}")

        # The output format is: "<SHA>\t<ref_name>"
        sha = lines[0].split()[0]
        if not sha:
            raise RuntimeError(
                f"Could not parse SHA from git ls-remote output: {stdout}"
            )

        logger.debug("Resolved git ref", ref=ref or "HEAD", sha=sha)
        return sha

    except Exception as e:
        logger.error("Error resolving git ref", ref=ref, error=str(e))
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(
            f"Error resolving git ref '{ref or 'HEAD'}': {str(e)}"
        ) from e


async def run_git(
    args: list[str],
    *,
    env: dict[str, str],
    cwd: str | None = None,
    timeout: float = 120.0,
) -> tuple[int, str, str]:
    """Run a git command asynchronously.

    Args:
        args: Git command arguments (including 'git' as first argument).
        env: Environment variables for the command.
        cwd: Working directory for the command.
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (return_code, stdout, stderr).

    Raises:
        RuntimeError: If command times out.
    """
    try:
        logger.debug("Running git command", args=args, cwd=cwd)

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(
                f"Git command timed out after {timeout} seconds: {' '.join(args)}"
            ) from None

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        logger.debug(
            "Git command completed",
            args=args,
            return_code=process.returncode,
            stdout_len=len(stdout),
            stderr_len=len(stderr),
        )

        return process.returncode or 0, stdout, stderr

    except Exception as e:
        logger.error("Error running git command", args=args, error=str(e))
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(
            f"Error running git command {' '.join(args)}: {str(e)}"
        ) from e


# Existing functions for backward compatibility


async def get_git_repository_sha(repo_url: str, env: SshEnv) -> str:
    """Get the SHA of the HEAD commit of a Git repository.

    This function maintains backward compatibility with the existing API.
    """
    return await resolve_git_ref(repo_url, ref=None, env=env.to_dict())


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
        # Validate and parse URL
        parsed_url = parse_git_url(url, allowed_domains=allowed_domains)
        # Create new GitUrl with the resolved SHA since GitUrl is now frozen
        git_url = GitUrl(
            host=parsed_url.host,
            org=parsed_url.org,
            repo=parsed_url.repo,
            ref=sha,
        )
    except ValueError as e:
        raise TracecatSettingsError(str(e)) from e
    return git_url


async def safe_prepare_git_url(role: Role | None = None) -> GitUrl | None:
    """Prepare the git URL, but return None if there's an error or the url doesn't exist."""
    try:
        return await prepare_git_url(role)
    except Exception as e:
        logger.error("Error preparing git URL", error=e)
        return None
