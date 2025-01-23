"""Generic interface to Ansible Python API."""

import asyncio
import tempfile
from pathlib import Path
from typing import Annotated, Any

import orjson
from ansible_runner import Runner, run_async
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

ansible_secret = RegistrySecret(
    name="ansible",
    optional_keys=[
        "ANSIBLE_SSH_KEY",
        "ANSIBLE_PASSWORDS",
    ],
)
"""Ansible Runner secret.

- name: `ansible`
- optional_keys:
    - `ANSIBLE_SSH_KEY`
    - `ANSIBLE_PASSWORDS`

`ANSIBLE_SSH_KEY` should be the private key string, not the path to the file.
`ANSIBLE_PASSWORDS` should be a JSON object mapping password prompts to their responses (e.g. `{"Vault password": "password"}`).
"""


@registry.register(
    default_title="Run playbook",
    description="Run Ansible playbook given as a list of plays in JSON format.",
    display_group="Ansible",
    doc_url="https://docs.ansible.com/ansible/latest/index.html",
    namespace="integrations.ansible",
    secrets=[ansible_secret],
)
async def run_playbook(
    playbook: Annotated[
        list[dict[str, Any]], Field(..., description="List of plays to run")
    ],
    extra_vars: Annotated[
        dict[str, Any],
        Field(description="Extra variables to pass to the playbook"),
    ] = None,
    runner_kwargs: Annotated[
        dict[str, Any],
        Field(description="Additional keyword arguments to pass to the Ansible runner"),
    ] = None,
) -> list[dict[str, Any]]:
    ssh_key = secrets.get("ANSIBLE_SSH_KEY")
    passwords = secrets.get("ANSIBLE_PASSWORDS")

    if not ssh_key and not passwords:
        raise ValueError(
            "Either `ANSIBLE_SSH_KEY` or `ANSIBLE_PASSWORDS` must be provided"
        )

    runner_kwargs = runner_kwargs or {}

    with tempfile.TemporaryDirectory() as temp_dir:
        if ssh_key:
            ssh_key_path = Path(temp_dir) / "id_rsa"
            with ssh_key_path.open("w") as f:
                f.write(ssh_key)
            runner_kwargs["ssh_key"] = str(ssh_key_path.resolve())

        if passwords:
            runner_kwargs["passwords"] = orjson.loads(passwords)

        loop = asyncio.get_running_loop()

        def run():
            return run_async(
                private_data_dir=temp_dir,
                playbook=playbook,
                extravars=extra_vars,
                **runner_kwargs,
            )

        _, result = await loop.run_in_executor(None, run)
        if isinstance(result, Runner):
            return list(result.events)
        else:
            raise ValueError("Ansible runner returned no result.")
