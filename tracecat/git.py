import asyncio

from tracecat.logger import logger
from tracecat.ssh import SshEnv


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
