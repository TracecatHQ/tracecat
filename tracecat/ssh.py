from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
import paramiko
from pydantic import SecretStr
from slugify import slugify
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.secrets.schemas import SSHKeyTarget
from tracecat.secrets.service import SecretsService

if TYPE_CHECKING:
    from tracecat.git.types import GitUrl

# Export list for backward compatibility
__all__ = [
    "SshEnv",
    "temporary_ssh_agent",
    "add_host_to_known_hosts",
    "add_ssh_key_to_agent",
    "get_ssh_command",
    "prepare_ssh_key_file",
    "ssh_context",
    "get_git_ssh_command",
]


@dataclass
class SshEnv:
    ssh_auth_sock: str
    ssh_agent_pid: str

    def to_dict(self) -> dict[str, str]:
        return {
            "SSH_AUTH_SOCK": self.ssh_auth_sock,
            "SSH_AGENT_PID": self.ssh_agent_pid,
        }


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
            ssh_auth_sock=ssh_auth_sock,
            ssh_agent_pid=ssh_agent_pid,
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


def _split_host_port(url: str) -> tuple[str, str | None]:
    """Split a host string into host and port components."""

    if url.startswith("[") and "]" in url:
        closing_idx = url.index("]")
        host_part = url[1:closing_idx]
        remainder = url[closing_idx + 1 :]
        if remainder.startswith(":") and remainder[1:].isdigit():
            return host_part, remainder[1:]
        if not remainder:
            return host_part, None
        return url, None

    # Only treat single-colon inputs as host:port; IPv6 literals contain multiple colons
    if url.count(":") == 1:
        host_part, port_part = url.rsplit(":", 1)
        if port_part.isdigit():
            return host_part, port_part
    return url, None


def add_host_to_known_hosts_sync(url: str, env: SshEnv) -> None:
    """Synchronously add the host to the known hosts file if not already present.

    Args:
        url: The host URL to add
        env: SSH environment variables

    Raises:
        Exception: If ssh-keyscan fails to get the host key
    """
    try:
        # Ensure the ~/.ssh directory exists
        ssh_dir = Path.home() / ".ssh"
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        known_hosts_file = ssh_dir / "known_hosts"

        host, port = _split_host_port(url)
        if port:
            formatted_host = f"[{host}]:{port}"
            known_host_tokens = {url, formatted_host, host}
        else:
            formatted_host = host
            known_host_tokens = {url, formatted_host}

        # Check if host already exists in known_hosts
        if known_hosts_file.exists():
            with known_hosts_file.open("r") as f:
                # Look for the hostname in existing entries
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    entry_host = stripped.split()[0]
                    # known_hosts entries can list multiple hosts separated by commas
                    matching_host = next(
                        (
                            host
                            for host in entry_host.split(",")
                            if host in known_host_tokens
                        ),
                        None,
                    )
                    if matching_host:
                        logger.debug(
                            "Host already in known_hosts file",
                            url=url,
                            entry_host=matching_host,
                        )
                        return
        # Use ssh-keyscan to get the host key
        cmd = ["ssh-keyscan"]
        if port:
            cmd.extend(["-p", port])
        cmd.append(host)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env.to_dict(),
            check=False,
            timeout=30.0,  # Prevent indefinite hang if network is unavailable
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to get host key: {result.stderr.strip()}")

        output_lines = result.stdout.splitlines(keepends=True)
        if port:
            rewritten_lines = []
            for line in output_lines:
                if line.startswith(host):
                    rewritten_lines.append(formatted_host + line[len(host) :])
                else:
                    rewritten_lines.append(line)
            output = "".join(rewritten_lines)
        else:
            output = "".join(output_lines)

        # Append the host key to the known_hosts file
        with known_hosts_file.open("a") as f:
            f.write(output)

        logger.info("Added host to known hosts", url=url)
    except Exception as e:
        logger.error("Error adding host to known hosts", error=e)
        raise


async def add_host_to_known_hosts(url: str, *, env: SshEnv) -> None:
    """Asynchronously add the host to the known hosts file."""
    return await asyncio.to_thread(add_host_to_known_hosts_sync, url, env)


def add_ssh_key_to_agent_sync(key_data: str, env: SshEnv) -> None:
    """Synchronously add the SSH key to the agent without writing to disk.

    Uses stdin to pass the key directly to ssh-add, avoiding any filesystem writes.
    This is important for multi-tenant security.
    """
    # Ensure key ends with newline (required by ssh-add)
    key_with_newline = key_data if key_data.endswith("\n") else key_data + "\n"

    # Validate the key using paramiko (reads from string, no disk)
    try:
        paramiko.Ed25519Key.from_private_key(StringIO(key_with_newline))
    except paramiko.SSHException as e:
        logger.error(f"Invalid SSH key: {str(e)}")
        raise

    try:
        # Pass key via stdin using '-' flag - never touches disk
        result = subprocess.run(
            ["ssh-add", "-"],
            input=key_with_newline,
            capture_output=True,
            text=True,
            env=env.to_dict(),
            check=False,
            timeout=30.0,  # Prevent indefinite hang
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to add SSH key: {result.stderr.strip()}")

        logger.info("Added SSH key to agent (via stdin, no disk write)")
    except Exception as e:
        logger.error("Error adding SSH key", error=e)
        raise


async def add_ssh_key_to_agent(key_data: str, env: SshEnv) -> None:
    """Asynchronously add the SSH key to the agent then remove it."""
    return await asyncio.to_thread(add_ssh_key_to_agent_sync, key_data, env)


async def get_ssh_command(git_url: GitUrl, role: Role, session: AsyncSession) -> str:
    """Get an SSH command for the given Git URL and SSH key."""
    role = role or ctx_role.get()
    service = SecretsService(session=session, role=role)
    ssh_key = await service.get_ssh_key()
    ssh_cmd = await prepare_ssh_key_file(git_url=git_url, ssh_key=ssh_key)
    return ssh_cmd


async def prepare_ssh_key_file(git_url: GitUrl, ssh_key: SecretStr) -> str:
    """Prepare an SSH key file for use in an SSH command."""
    key_dir = Path.home().joinpath(".tracecat")
    key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = key_dir / slugify(f"{git_url.host}_{git_url.org}_{git_url.repo}")

    # Write the key content to the file with proper permissions
    async with aiofiles.open(key_path, mode="w", encoding="utf-8") as f:
        await f.write(ssh_key.get_secret_value())
        await f.flush()

    # Set strict permissions after writing (important!)
    os.chmod(key_path, 0o600)

    logger.debug("Created SSH key file", key_path=key_path)

    # Use the key file in SSH command with more permissive host key checking
    ssh_cmd = (
        f"ssh -i {key_path!s} -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={Path.home().joinpath('.ssh/known_hosts')!s}"
    )
    return ssh_cmd


@asynccontextmanager
async def ssh_context(
    *,
    git_url: GitUrl | None = None,
    session: AsyncSession,
    role: Role | None = None,
    key_name: str | None = None,
    target: SSHKeyTarget = "registry",
) -> AsyncIterator[SshEnv | None]:
    """Context manager for SSH environment variables."""
    if git_url is None:
        yield None
    else:
        sec_svc = SecretsService(session, role=role)
        secret = await sec_svc.get_ssh_key(key_name=key_name, target=target)
        async with temporary_ssh_agent() as env:
            await add_ssh_key_to_agent(secret.get_secret_value(), env=env)
            await add_host_to_known_hosts(git_url.host, env=env)
            yield env


# New SSH helper functions for Git operations


async def get_git_ssh_command(
    git_url: GitUrl, *, session: AsyncSession, role: Role | None
) -> str:
    """Get a Git SSH command for the given Git URL.

    Args:
        git_url: Git URL object containing repository information.
        session: Database session.
        role: User role for permissions.

    Returns:
        SSH command string for use with GIT_SSH_COMMAND.

    Raises:
        Exception: If SSH key retrieval or file preparation fails.
    """
    role = role or ctx_role.get()
    service = SecretsService(session=session, role=role)
    ssh_key = await service.get_ssh_key(target="registry")
    ssh_cmd = await prepare_ssh_key_file(git_url=git_url, ssh_key=ssh_key)
    return ssh_cmd
