"""OpenAI Codex integration.

Sandbox options:
- [x] Modal sandbox
- [ ] AWS Lambda (coming soon)
"""

import modal
from typing import Annotated

from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets


openai_codex_secret = RegistrySecret(
    name="openai_codex",
    keys=["OPENAI_API_KEY"],
)
"""OpenAI Codex API key.

- name: `openai_codex`
- keys:
    - `OPENAI_API_KEY`
"""


modal_secret = RegistrySecret(
    name="modal",
    keys=["MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"],
)
"""Modal API key.

- name: `modal`
- keys:
    - `MODAL_TOKEN_ID`
    - `MODAL_TOKEN_SECRET`
"""


SANDBOX_APP_NAME = "tracecat-openai-codex"


def _deploy_to_modal():
    """Create (or look up) the Modal app and sandbox image for OpenAI Codex."""

    # Base image with Node.js runtime and the OpenAI npm package pre-installed.
    image = (
        modal.Image.debian_slim()
        .apt_install("nodejs", "npm")
        .run_commands("npm install -g openai@latest")
    )

    app = modal.App.lookup(SANDBOX_APP_NAME, create_if_missing=True)
    return app, image


@registry.register(
    default_title="Run Codex sandbox command",
    description=(
        "Execute a shell command inside a Modal sandbox with the OpenAI npm package installed. "
        "The sandbox runs in full access mode with outbound network access blocked."
    ),
    display_group="OpenAI Codex",
    doc_url="https://modal.com/docs/guide/sandbox",
    namespace="ai.openai_codex",
    secrets=[openai_codex_secret, modal_secret],
)
def run_sandbox_command(
    prompt: Annotated[
        str,
        Field(
            ...,
            description="Prompt to execute inside the sandbox.",
        ),
    ],
    timeout: Annotated[
        int,
        Field(
            ...,
            description="Lifetime of the sandbox in seconds.",
        ),
    ] = 600,
) -> str:
    """Run a user-provided command inside a pre-configured Modal sandbox."""

    app, image = _deploy_to_modal()

    openai_api_key = secrets.get("OPENAI_API_KEY")
    openai_secret = modal.Secret.from_dict({"OPENAI_API_KEY": openai_api_key})

    sandbox = modal.Sandbox.create(
        app=app,
        image=image,
        secrets=[openai_secret],
        timeout=timeout,
        block_network=True,
    )
    output = sandbox.exec(
        "codex",
        "exec",
        prompt,
    )
    returncode = output.returncode
    if returncode != 0:
        stdout = output.stdout.read()
        stderr = output.stderr.read()
        raise RuntimeError(
            f"Sandbox command exited with status {returncode}.\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        )
    return output.stdout.read()
