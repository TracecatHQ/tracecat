from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
import paramiko
from pydantic import SecretStr
from slugify import slugify
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role

if TYPE_CHECKING:
    from tracecat.git import GitUrl


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


def add_host_to_known_hosts_sync(url: str, port: int, env: SshEnv) -> None:
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

        # Check if host already exists in known_hosts
        if known_hosts_file.exists():
            with known_hosts_file.open("r") as f:
                # Look for the hostname in existing entries
                if any(url in line for line in f.readlines()):
                    logger.debug("Host already in known_hosts file", url=url)
                    return
        # Use ssh-keyscan to get the host key
        result = subprocess.run(
            ["ssh-keyscan", "-p", str(port), url],
            capture_output=True,
            text=True,
            env=env.to_dict(),
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to get host key: {result.stderr.strip()}")

        # Append the host key to the known_hosts file
        with known_hosts_file.open("a") as f:
            f.write(result.stdout)

        logger.info("Added host to known hosts", url=url)
    except Exception as e:
        logger.error("Error adding host to known hosts", error=e)
        raise


async def add_host_to_known_hosts(url: str, port: int, *, env: SshEnv) -> None:
    """Asynchronously add the host to the known hosts file."""
    return await asyncio.to_thread(add_host_to_known_hosts_sync, url, port, env)


def add_ssh_key_to_agent_sync(key_data: str, env: SshEnv) -> None:
    """Synchronously add the SSH key to the agent then remove it."""
    with tempfile.NamedTemporaryFile(mode="w", delete=True) as temp_key_file:
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
            result = subprocess.run(
                ["ssh-add", temp_key_file.name],
                capture_output=True,
                text=True,
                env=env.to_dict(),
                check=False,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Failed to add SSH key: {result.stderr.strip()}")

            logger.info("Added SSH key to agent")
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
) -> AsyncIterator[SshEnv | None]:
    """Context manager for SSH environment variables."""
    if git_url is None:
        yield None
    else:
        sec_svc = SecretsService(session, role=role)
        secret = await sec_svc.get_ssh_key()
        async with temporary_ssh_agent() as env:
            await add_ssh_key_to_agent(secret.get_secret_value(), env=env)
            await add_host_to_known_hosts(git_url.host, git_url.port, env=env)
            yield env
