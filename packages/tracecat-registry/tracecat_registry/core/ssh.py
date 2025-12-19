"""Core SSH actions via Paramiko using `core.ssh.execute_command`."""

import io
from typing import Annotated, TypedDict

import paramiko
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

ssh_secret = RegistrySecret(
    name="ssh",
    keys=["PRIVATE_KEY"],
)
"""SSH key secret.

- name: `ssh`
- keys:
    - `PRIVATE_KEY`
"""


class SSHCommandResult(TypedDict):
    stdout: str
    stderr: str
    exit_status: int


def _load_private_key(private_key: str) -> paramiko.PKey:
    key_stream = io.StringIO(private_key)
    key_types = (
        paramiko.Ed25519Key,
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.DSSKey,
    )

    last_error: Exception | None = None
    for key_type in key_types:
        key_stream.seek(0)
        try:
            return key_type.from_private_key(key_stream)
        except paramiko.SSHException as exc:
            last_error = exc

    raise ValueError("Unsupported or invalid private key format.") from last_error


@registry.register(
    default_title="Execute SSH command",
    description="Execute a shell command over SSH.",
    display_group="SSH",
    namespace="core.ssh",
    secrets=[ssh_secret],
)
def execute_command(
    host: Annotated[
        str,
        Doc("SSH host to connect to."),
    ],
    username: Annotated[
        str,
        Doc("SSH username."),
    ],
    command: Annotated[
        str,
        Doc("Command to execute on the remote host."),
    ],
    port: Annotated[
        int,
        Doc("SSH port. Defaults to 22."),
    ] = 22,
    password: Annotated[
        str | None,
        Doc("Password for the SSH user."),
    ] = None,
    timeout_seconds: Annotated[
        float,
        Doc("Timeout in seconds for connection and command execution."),
    ] = 30.0,
) -> SSHCommandResult:
    """Execute a command over SSH and return stdout, stderr, and exit status."""
    private_key = secrets.get("PRIVATE_KEY")
    pkey = _load_private_key(private_key)

    with paramiko.SSHClient() as client:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            pkey=pkey,
            timeout=timeout_seconds,
            banner_timeout=timeout_seconds,
            auth_timeout=timeout_seconds,
            # NOTE: we disable key lookup and agent forwarding
            # Every ssh connection should be scoped to this action only
            # and should not have access to the host SSH keys or agent.
            look_for_keys=False,
            allow_agent=False,
        )
        # Execute the command
        stdin, stdout, stderr = client.exec_command(
            command,
            timeout=timeout_seconds,
        )
        try:
            stdout_data = stdout.read()
            stderr_data = stderr.read()
            exit_status = stdout.channel.recv_exit_status()
        finally:
            stdin.close()
            stdout.close()
            stderr.close()

        return {
            "stdout": stdout_data.decode("utf-8", errors="replace"),
            "stderr": stderr_data.decode("utf-8", errors="replace"),
            "exit_status": exit_status,
        }
