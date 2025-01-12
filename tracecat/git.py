import asyncio
import re
from dataclasses import dataclass

from tracecat.logger import logger
from tracecat.ssh import SshEnv


@dataclass
class GitUrl:
    host: str
    org: str
    repo: str
    branch: str


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
        sha = stdout.decode().split()[0]
        return sha

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
    pattern = r"^git\+ssh://git@(?P<host>[^/]+)/(?P<org>[^/]+)/(?P<repo>[^/@]+?)(?:\.git)?(?:@(?P<branch>[^/]+))?$"

    if match := re.match(pattern, url):
        host = match.group("host")
        if allowed_domains and host not in allowed_domains:
            raise ValueError(
                f"Domain {host} not in allowed domains. Must be configured in `git_allowed_domains` organization setting."
            )

        return GitUrl(
            host=host,
            org=match.group("org"),
            repo=match.group("repo"),
            branch=match.group("branch") or "main",
        )

    raise ValueError(f"Unsupported URL format: {url}. Must be a valid Git SSH URL.")
