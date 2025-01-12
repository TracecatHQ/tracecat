import asyncio
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import paramiko

from tracecat.logger import logger


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
            env=env.to_dict(),
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


async def add_ssh_key_to_agent(key_data: str, env: SshEnv) -> None:
    """Add the SSH key to the agent then remove it."""
    # TODO(perf): Improve concurrency
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
                env=env.to_dict(),
            )
            _, stderr = await process.communicate()

            if process.returncode != 0:
                raise Exception(f"Failed to add SSH key: {stderr.decode().strip()}")

            logger.info("Added SSH key to agent")
        except Exception as e:
            logger.error("Error adding SSH key", error=e)
            raise
