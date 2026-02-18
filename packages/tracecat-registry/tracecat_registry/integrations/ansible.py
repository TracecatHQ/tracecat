"""Generic interface to Ansible Python API."""

from typing import Annotated, Any
from ansible_runner import Runner, run
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

ansible_secret = RegistrySecret(
    name="ansible",
    keys=[
        "PRIVATE_KEY",
    ],
)
"""Ansible SSH key.
- name: `ansible`
- keys:
    - `PRIVATE_KEY`
"""


@registry.register(
    default_title="Run Ansible playbook",
    description="Run Ansible playbook on a single host given a list of plays in JSON format. Supports SSH host-connection mode only.",
    display_group="Ansible",
    doc_url="https://docs.ansible.com/ansible/latest/index.html",
    namespace="tools.ansible",
    secrets=[ansible_secret],
)
def run_playbook(
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
        Field(description="Environment variables to be used when running Ansible"),
    ] = None,
    extravars: Annotated[
        dict[str, Any] | None,
        Field(description="Extra variables to pass to Ansible using `-e`"),
    ] = None,
    runner_kwargs: Annotated[
        dict[str, Any] | None,
        Field(description="Additional keyword arguments to pass to the runner"),
    ] = None,
    passwords: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "Optional mapping from password prompt to value. "
                'Example: {"SSH password:": "${{ SECRETS.your_secret_name.some_password }}"}.'
            )
        ),
    ] = None,
    timeout: Annotated[
        int,
        Field(description="Timeout for the playbook execution in seconds"),
    ] = 60,
    ignore_events: Annotated[
        list[str] | None,
        Field(
            description="List of events to ignore from the playbook. Returns all events by default."
        ),
    ] = None,
) -> list[dict[str, Any]] | list[str]:
    ignore_events = ignore_events or []

    extravars = extravars or {}
    runner_kwargs = runner_kwargs or {}
    # Prevent ansible-runner from writing console output to stdout.
    # Registry actions are executed in a subprocess where stdout must contain
    # only the JSON protocol payload returned by minimal_runner.
    runner_kwargs.setdefault("quiet", True)

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

    ssh_key = secrets.get("PRIVATE_KEY")
    if ssh_key:
        runner_kwargs["ssh_key"] = ssh_key
    if passwords:
        runner_kwargs["passwords"] = passwords

    result = run(
        playbook=playbook,
        envvars=envvars,
        extravars=extravars,
        timeout=timeout,
        inventory=inventory,
        **runner_kwargs,
    )
    if isinstance(result, Runner):
        # Check stdout for errors
        if result.stdout:
            if "Error loading key" in result.stdout:
                raise ValueError(
                    "Failed to load SSH key. Please check the key begins and ends with `-----BEGIN OPENSSH PRIVATE KEY-----` and `-----END OPENSSH PRIVATE KEY-----`."
                )

        # Events are a generator, so we need to convert to a list
        # Filter out internal implementation details (temp paths, ssh key loading, etc.)
        events = result.events
        filtered_events = []
        for event in events:
            if event.get("event") in ignore_events:
                continue
            # Filter out verbose events that expose internal temp paths
            stdout = event.get("stdout", "")
            if "Identity added:" in stdout:
                continue
            filtered_events.append(event)
        return filtered_events
    else:
        raise ValueError("Ansible runner returned no result.")
