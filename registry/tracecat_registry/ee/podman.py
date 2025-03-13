from tracecat.sandbox.podman import (
    run_podman_container,
    list_podman_volumes,
    remove_podman_volumes,
    PodmanNetwork,
    PullPolicy,
)

from typing import Any, Annotated, Literal
from typing_extensions import Doc

from tracecat_registry import registry


@registry.register(
    default_title="Run a container",
    description="Run a container using rootless Podman.",
    display_group="Podman",
    namespace="ee.podman",
    doc_url="https://podman-py.readthedocs.io/en/latest/podman.domain.containers_create.html",
)
def run_container(
    image: Annotated[str, Doc("Image to run.")],
    command: Annotated[str | list[str] | None, Doc("Command to run.")] = None,
    environment: Annotated[
        dict[str, str] | None, Doc("Environment variables to set.")
    ] = None,
    volume_name: Annotated[str | None, Doc("Create a named volume.")] = None,
    volume_path: Annotated[str | None, Doc("Path to mount in the container.")] = None,
    network: Annotated[
        Literal["none", "bridge", "host"], Doc("Network to use.")
    ] = "none",
    pull_policy: Annotated[
        Literal["missing", "never", "always"], Doc("Pull policy.")
    ] = "missing",
) -> dict[str, Any]:
    result = run_podman_container(
        image=image,
        command=command,
        environment=environment,
        pull_policy=PullPolicy(pull_policy),
        volume_name=volume_name,
        volume_path=volume_path,
        network=PodmanNetwork(network),
    )
    return result.model_dump()


@registry.register(
    default_title="List volumes",
    description="List all volumes from the Podman service.",
    display_group="Podman",
    namespace="ee.podman",
    doc_url="https://podman-py.readthedocs.io/en/latest/podman.domain.volumes.html",
)
def list_volumes() -> list[str]:
    return list_podman_volumes()


@registry.register(
    default_title="Remove volumes",
    description="Remove all volumes from the Podman service.",
    display_group="Podman",
    namespace="ee.podman",
    doc_url="https://podman-py.readthedocs.io/en/latest/podman.domain.volumes.html",
)
def remove_volumes(
    volume_name: Annotated[str | list[str] | None, Doc("Volume name to remove.")],
) -> None:
    remove_podman_volumes(volume_name=volume_name)
