import orjson
import shlex
import subprocess
import tempfile
from typing import Any, Annotated
from typing_extensions import Doc

from tracecat.logger import logger

from tracecat_registry import registry, RegistrySecret, secrets
from tracecat_registry._internal.kubernetes import decode_kubeconfig, validate_namespace


kubernetes_secret = RegistrySecret(name="kubernetes", keys=["KUBECONFIG_BASE64"])
"""Kubernetes credentials.

- name: `kubernetes`
- keys:
    - `KUBECONFIG_BASE64`: Base64 encoded kubeconfig YAML file.
"""


@registry.register(
    default_title="Run kubectl command",
    description="Run a kubectl command on a Kubernetes cluster.",
    display_group="Kubernetes",
    doc_url="https://kubernetes.io/docs/reference/generated/kubectl/kubectl-commands",
    namespace="tools.kubernetes",
    secrets=[kubernetes_secret],
)
def run_command(
    command: Annotated[str | list[str], Doc("Command to run.")],
    namespace: Annotated[str, Doc("Namespace to run the command in.")],
    dry_run: Annotated[
        bool, Doc("Whether to dry run the command client-side.")
    ] = False,
    stdin: Annotated[
        str | None, Doc("Optional string to pass to the command's standard input.")
    ] = None,
    args: Annotated[
        list[str] | None, Doc("Additional arguments to pass to the command.")
    ] = None,
    timeout: Annotated[int, Doc("Timeout for the command in seconds.")] = 60,
) -> Any:
    """Run a ``kubectl`` command on a Kubernetes cluster.

    The helper automatically appends ``-o json`` for commands that support JSON
    output (``get``, ``create``, ``apply``, ``delete``, ``run`` and ``describe``).

    If the command exits with a non-zero status code a ``RuntimeError`` is
    raised with the stderr contents. When JSON output was requested the stdout
    is parsed into Python objects and returned, otherwise the raw stdout is
    returned.
    """
    kubeconfig_base64 = secrets.get("KUBECONFIG_BASE64")
    validate_namespace(namespace)

    # Convert string command to list for easier manipulation
    if isinstance(command, str):
        command = shlex.split(command)

    # Make sure the user provided at least one sub-command, e.g. `get pods`
    if not command:
        raise ValueError("kubectl command cannot be empty")

    kubeconfig_yaml = decode_kubeconfig(kubeconfig_base64)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=True) as tmp:
        tmp.write(kubeconfig_yaml)
        tmp.flush()

        _args: list[str] = ["kubectl", "--kubeconfig", tmp.name]

        if dry_run:
            _args.append("--dry-run=client")

        _args.extend(["--namespace", namespace])

        # Determine whether we can safely ask for JSON output.
        json_supported_cmds = {"get", "create", "apply", "delete", "run", "describe"}
        first_sub_cmd = command[0]
        request_json = (
            first_sub_cmd in json_supported_cmds
            and "-o" not in command
            and (args is None or "-o" not in args and "--output" not in args)
        )

        # Append the user-supplied kubectl sub-command(s)
        _args.extend(command)

        # Extra arguments from the caller
        if args:
            _args.extend(args)

        if request_json:
            _args.extend(["-o", "json"])

        logger.info("Running kubectl command", command=_args, stdin=stdin)

        proc = subprocess.run(
            _args,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            input=stdin,
            timeout=timeout,
        )

        if proc.returncode != 0:
            logger.error(
                "kubectl command failed",
                command=_args,
                returncode=proc.returncode,
                stderr=proc.stderr,
            )
            raise RuntimeError(
                f"kubectl failed ({proc.returncode}): {proc.stderr.strip()}"
            )

        stdout = proc.stdout.strip()

        # Attempt to parse JSON output when we explicitly requested it
        if request_json:
            try:
                # orjson.loads expects bytes
                return orjson.loads(stdout.encode()) if stdout else {}
            except orjson.JSONDecodeError:
                logger.warning("Failed to parse kubectl JSON output", output=stdout)
                # Fallback to returning raw stdout if parsing fails
                return stdout

        # For commands where JSON is not requested/supported just return stdout
        return stdout
