"""Generic interface to Ansible Python API."""

import asyncio
from typing import Annotated, Any

import orjson
from ansible_runner import Runner, run_async
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

ansible_secret = RegistrySecret(
    name="ansible",
    keys=[
        "ANSIBLE_SSH_KEY",
    ],
    optional_keys=[
        "ANSIBLE_PASSWORDS",
    ],
)
"""Ansible Runner secret.

- name: `ansible`
- keys:
    - `ANSIBLE_SSH_KEY`
- optional_keys:
    - `ANSIBLE_PASSWORDS`

`ANSIBLE_SSH_KEY` should be the private key string, not the path to the file.
`ANSIBLE_PASSWORDS` should be a JSON object mapping password prompts to their responses:
{
    "SSH password:": "sshpass",
    "BECOME password": "sudopass",
}
"""


@registry.register(
    default_title="Run playbook",
    description="Run Ansible playbook on a single host given a list of plays in JSON format. Supports SSH host-connection mode only.",
    display_group="Ansible",
    doc_url="https://docs.ansible.com/ansible/latest/index.html",
    namespace="tools.ansible",
    secrets=[ansible_secret],
)
async def run_playbook(
    playbook: Annotated[
        list[dict[str, Any]], Field(..., description="List of plays to run")
    ],
    host: Annotated[
        str,
        Field(description="Host to SSH into and run the playbook on"),
    ],
    host_name: Annotated[
        str,
        Field(description="Host name to use in the inventory"),
    ],
    user: Annotated[
        str,
        Field(description="SSH user to connect as"),
    ],
    envvars: Annotated[
        dict[str, Any] | None,
        Field(description="Environment variables to pass to the playbook"),
    ] = None,
    extravars: Annotated[
        dict[str, Any] | None,
        Field(description="Extra variables to pass to the playbook"),
    ] = None,
    runner_kwargs: Annotated[
        dict[str, Any] | None,
        Field(description="Additional keyword arguments to pass to the Ansible runner"),
    ] = None,
    timeout: Annotated[
        int,
        Field(description="Timeout for the playbook execution in seconds"),
    ] = 60,
) -> list[dict[str, Any]]:
    ssh_key = secrets.get("ANSIBLE_SSH_KEY")
    passwords = secrets.get("ANSIBLE_PASSWORDS")

    extravars = extravars or {}
    runner_kwargs = runner_kwargs or {}

    if "inventory" in runner_kwargs:
        raise ValueError(
            "`inventory` is not supported in this integration. Please use the `host` and `user` parameters instead."
        )

    local_patterns = ["localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"]
    if host in local_patterns:
        # Block local connections in initial host connection
        raise ValueError(f"Local connections are not supported. Got host: {host}")

    # Create inventory config
    inventory = {
        "all": {
            "hosts": {
                host_name: {
                    "ansible_host": host,
                    "ansible_user": user,
                    "ansible_connection": "ssh",
                }
            }
        }
    }

    if ssh_key:
        runner_kwargs["ssh_key"] = ssh_key
    if passwords:
        runner_kwargs["passwords"] = orjson.loads(passwords)

    loop = asyncio.get_running_loop()

    def run():
        runner, result = run_async(
            playbook=playbook,
            envvars=envvars,
            extravars=extravars,
            timeout=timeout,
            inventory=inventory,
            **runner_kwargs,
        )
        return runner, result

    _, result = await loop.run_in_executor(None, run)
    if isinstance(result, Runner):
        # Events are a generator, so we need to convert to a list
        return list(result.events)
    else:
        raise ValueError("Ansible runner returned no result.")
