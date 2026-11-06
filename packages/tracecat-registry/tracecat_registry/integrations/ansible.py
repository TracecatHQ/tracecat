"""Generic interface to Ansible Python API."""

import re
from typing import Annotated, Any
from ansible_runner import Runner, run
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry._internal.exceptions import TracecatException
from tracecat_registry._internal.remote_exec import (
    find_error_pattern,
    output_tail,
    resolve_remote_target,
    validate_error_patterns,
)

ansible_secret = RegistrySecret(
    name="ansible",
    keys=[
        "PRIVATE_KEY",
    ],
    secret_type="ssh_key",
)
"""Ansible SSH key.
- name: `ansible`
- keys:
    - `PRIVATE_KEY`
"""

ALLOWED_RUNNER_KWARGS = frozenset(
    {
        "forks",
        "json_mode",
        "omit_event_data",
        "only_failed_event_data",
        "quiet",
        "rotate_artifacts",
        "skip_tags",
        "tags",
        "verbosity",
    }
)
"""ansible-runner options that cannot change the target host, inventory, or binary."""

# Connection variables that would override the validated inventory host or
# inject local commands into the SSH invocation (e.g. ProxyCommand).
_CONNECTION_OVERRIDE_VARS = frozenset(
    {
        "ansible_connection",
        "ansible_host",
        "ansible_paramiko_host",
        "ansible_paramiko_proxy_command",
        "ansible_ssh_args",
        "ansible_ssh_common_args",
        "ansible_ssh_executable",
        "ansible_ssh_extra_args",
        "ansible_ssh_host",
        "ansible_scp_extra_args",
        "ansible_sftp_extra_args",
    }
)

# Inventory host names are also used as the `--limit` pattern, so they must not
# contain pattern syntax (`,`, `:`, `!`, `&`, `*`, `~`) that could widen it.
_HOST_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _find_connection_overrides(value: Any) -> set[str]:
    """Return connection-override variable names used as keys anywhere in value."""
    found: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str) and key.lower() in _CONNECTION_OVERRIDE_VARS:
                    found.add(key)
                stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _validate_playbook(playbook: list[dict[str, Any]]) -> None:
    if overrides := _find_connection_overrides(playbook):
        raise ValueError(
            "playbook cannot override connection settings: "
            f"{', '.join(sorted(overrides))}."
        )


_FAILURE_EVENTS = frozenset({"runner_on_failed", "runner_on_unreachable"})


def _first_failure_message(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("event") not in _FAILURE_EVENTS:
            continue
        event_data = event.get("event_data") or {}
        res = event_data.get("res") or {}
        message = res.get("msg") or res.get("stderr") or event.get("stdout")
        if not message:
            continue
        task = event_data.get("task")
        return f"{task}: {message}" if task else str(message)
    return None


def _raise_for_playbook_failure(
    status: str,
    rc: int | None,
    stats: dict[str, Any] | None,
    events: list[dict[str, Any]],
    stdout: str,
    error_patterns: list[str],
) -> None:
    if status != "successful":
        reason = f"finished with status {status!r} (rc={rc})"
    elif not (stats or {}).get("processed"):
        # e.g. `hosts: localhost`, which the inventory limit excludes.
        reason = (
            "did not run on any host. Use `hosts: all`; `localhost` refers to "
            "the Tracecat worker and is excluded"
        )
    elif pattern := find_error_pattern(error_patterns, [stdout]):
        reason = f"output matched error pattern {pattern!r}"
    else:
        return
    message = f"Ansible playbook {reason}."
    if stats:
        failed = sum((stats.get("failures") or {}).values())
        unreachable = sum((stats.get("dark") or {}).values())
        message += f" Failed hosts: {failed}. Unreachable hosts: {unreachable}."
    if detail := _first_failure_message(events):
        message += f"\n\n```\n{output_tail(detail)}\n```"
    raise TracecatException(message, detail={"status": status, "rc": rc})


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
        list[dict[str, Any]],
        Field(
            ...,
            description=(
                "List of plays to run. Plays run only on the inventory host, so use "
                "`hosts: all`. Connection variables that redirect the target "
                "(e.g. `ansible_host`, `ansible_ssh_common_args`) are rejected."
            ),
        ),
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
        Field(
            description=(
                "Additional ansible-runner options. Allowed keys: "
                + ", ".join(sorted(ALLOWED_RUNNER_KWARGS))
                + "."
            )
        ),
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
    raise_on_failure: Annotated[
        bool,
        Field(
            description=(
                "Raise an error when the playbook fails, times out, is canceled, or its "
                "output contains any error_patterns. Defaults to returning the events."
            )
        ),
    ] = False,
    error_patterns: Annotated[
        list[str] | None,
        Field(
            description=(
                "Text that marks the run as failed when raise_on_failure is enabled. "
                "The action raises if the playbook output contains any item exactly "
                "as written: case-sensitive, and characters like `*`, `.` or `^` "
                "match themselves (no wildcards or regex). Example: "
                '`["FAILED!", "UNREACHABLE!"]`. '
                "Up to 20 items, each 1-256 characters."
            )
        ),
    ] = None,
) -> list[dict[str, Any]] | list[str]:
    ignore_events = ignore_events or []
    patterns = validate_error_patterns(error_patterns)

    if not _HOST_NAME_PATTERN.fullmatch(host_name):
        raise ValueError(
            "host_name may only contain letters, digits, '.', '_' and '-'."
        )
    extravars = extravars or {}
    runner_kwargs = dict(runner_kwargs or {})
    if "inventory" in runner_kwargs:
        raise ValueError(
            "`inventory` is not supported in this integration. Please use the `host` and `user` parameters instead."
        )
    if disallowed := sorted(set(runner_kwargs) - ALLOWED_RUNNER_KWARGS):
        raise ValueError(
            f"Unsupported runner_kwargs: {', '.join(disallowed)}. "
            f"Allowed keys: {', '.join(sorted(ALLOWED_RUNNER_KWARGS))}."
        )
    _validate_playbook(playbook)
    if overrides := sorted(
        key for key in extravars if key.lower() in _CONNECTION_OVERRIDE_VARS
    ):
        raise ValueError(
            f"extravars cannot override connection settings: {', '.join(overrides)}."
        )
    # Prevent ansible-runner from writing console output to stdout.
    # Registry actions are executed in a subprocess where stdout must contain
    # only the JSON protocol payload returned by minimal_runner.
    runner_kwargs.setdefault("quiet", True)

    # Resolve once and pin the inventory to the validated address, so loopback,
    # link-local, and metadata targets are rejected even behind DNS names.
    target_address = resolve_remote_target(host, 22)

    # Create inventory config
    inventory = {
        "all": {
            "hosts": {
                host_name: {
                    "ansible_host": target_address,
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
        # Only run against the inventory host; excludes Ansible's implicit localhost.
        limit=host_name,
        **runner_kwargs,
    )
    if isinstance(result, Runner):
        try:
            with result.stdout as stdout_file:
                stdout = stdout_file.read()
        except Exception:
            stdout = ""
        if "Error loading key" in stdout:
            raise ValueError(
                "Failed to load SSH key. Please check the key begins and ends with `-----BEGIN OPENSSH PRIVATE KEY-----` and `-----END OPENSSH PRIVATE KEY-----`."
            )

        # Events are a generator, so we need to convert to a list
        events = list(result.events)
        if raise_on_failure:
            _raise_for_playbook_failure(
                status=result.status,
                rc=result.rc,
                stats=result.stats,
                events=events,
                stdout=stdout,
                error_patterns=patterns,
            )

        # Filter out internal implementation details (temp paths, ssh key loading, etc.)
        filtered_events = []
        for event in events:
            if event.get("event") in ignore_events:
                continue
            # Filter out verbose events that expose internal temp paths
            event_stdout = event.get("stdout", "")
            if "Identity added:" in event_stdout:
                continue
            filtered_events.append(event)
        return filtered_events
    else:
        raise ValueError("Ansible runner returned no result.")
