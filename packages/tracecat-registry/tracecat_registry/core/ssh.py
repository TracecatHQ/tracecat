"""Core SSH actions via Paramiko using `core.ssh.execute_command`."""

import io
import os
import tempfile
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
        paramiko.DSSKey,  # pyright: ignore[reportAttributeAccessIssue] - DSSKey exists but is missing from type stubs
    )

    last_error: Exception | None = None
    for key_type in key_types:
        key_stream.seek(0)
        try:
            return key_type.from_private_key(key_stream)
        except paramiko.SSHException as exc:
            last_error = exc

    raise ValueError("Unsupported or invalid private key format.") from last_error


def _host_key_name(host: str, port: int) -> str:
    if port == 22:
        return host
    return f"[{host}]:{port}"


def _build_known_hosts(host: str, port: int, public_host_key: str) -> str:
    stripped_key = public_host_key.strip()
    if not stripped_key or len(stripped_key.split()) < 2:
        raise ValueError("public_host_key must be in '<key_type> <base64>' format.")
    return f"{_host_key_name(host, port)} {stripped_key}\n"


@registry.register(
    default_title="Execute SSH command",
    description="Execute a shell command over SSH.",
    display_group="SSH",
    namespace="core.ssh",
    secrets=[ssh_secret],
)
def execute_command(
    command: Annotated[
        str,
        Doc("Command to execute on the remote host."),
    ],
    host: Annotated[
        str,
        Doc("SSH host to connect to."),
    ],
    host_public_key: Annotated[
        str,
        Doc(
            "Expected public host key for the target host in '<key_type> <base64>' format. "
            "For non-22 ports, the entry is stored as [host]:port."
        ),
    ],
    username: Annotated[
        str,
        Doc("SSH username."),
    ],
    password: Annotated[
        str | None,
        Doc("Password for the SSH user."),
    ] = None,
    port: Annotated[
        int,
        Doc("SSH port. Defaults to 22."),
    ] = 22,
    timeout_seconds: Annotated[
        float,
        Doc("Timeout in seconds for connection and command execution."),
    ] = 30.0,
) -> SSHCommandResult:
    """Execute a command over SSH and return stdout, stderr, and exit status.

    Unknown host keys are rejected; provide `host_public_key` to trust a host.
    """
    private_key = secrets.get("PRIVATE_KEY")
    pkey = _load_private_key(private_key)

    with paramiko.SSHClient() as client:
        known_hosts_path: str | None = None
        try:
            known_hosts_data = _build_known_hosts(host, port, host_public_key)
            with tempfile.NamedTemporaryFile(
                mode="w",
                delete=False,
                encoding="utf-8",
            ) as known_hosts_file:
                known_hosts_file.write(known_hosts_data)
                known_hosts_path = known_hosts_file.name
            client.load_host_keys(known_hosts_path)

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
        finally:
            if known_hosts_path:
                try:
                    os.unlink(known_hosts_path)
                except FileNotFoundError:
                    pass
