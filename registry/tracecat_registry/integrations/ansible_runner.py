"""Ansible Runner integration with S3 support.

Automation framework integration: Ansible Runner with playbooks hosted on S3.

Requires:
- A secret named `ansible_runner` with the following keys:
  - `ANSIBLE_HOST`
  - `ANSIBLE_PORT`
  - `ANSIBLE_USER`
  - Either `ANSIBLE_PRIVATE_KEY` or `ANSIBLE_PASSWORD`
- A secret named `aws` with the following keys:
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_REGION`

"""

import os
import tempfile
from typing import Annotated, Any, Dict, Optional, Tuple

from pydantic import Field
import aioboto3
import asyncio
from ansible_runner import run_async

from tracecat_registry import RegistrySecret, registry, secrets

ansible_runner_secret = RegistrySecret(
    name="ansible_runner",
    keys=[
        "ANSIBLE_HOST",
        "ANSIBLE_PORT",
        "ANSIBLE_USER",
        "ANSIBLE_PRIVATE_KEY",
        "ANSIBLE_PASSWORD",
    ],
)
"""Ansible Runner secret.

- name: `ansible_runner`
- keys:
    - `ANSIBLE_HOST`
    - `ANSIBLE_PORT`
    - `ANSIBLE_USER`
    - `ANSIBLE_PRIVATE_KEY` (optional if `ANSIBLE_PASSWORD` is provided)
    - `ANSIBLE_PASSWORD` (optional if `ANSIBLE_PRIVATE_KEY` is provided)
"""

aws_secret = RegistrySecret(
    name="aws",
    keys=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
)
"""AWS secret.

- name: `aws`
- keys:
    - `AWS_ACCESS_KEY_ID`
    - `AWS_SECRET_ACCESS_KEY`
    - `AWS_REGION`
"""

async def download_playbook_from_s3(s3_url: str) -> str:
    """Download an Ansible playbook from an S3 URL."""
    bucket_name, object_key = parse_s3_url(s3_url)

    session = aioboto3.Session(
        aws_access_key_id=secrets.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=secrets.get("AWS_SECRET_ACCESS_KEY"),
        region_name=secrets.get("AWS_REGION"),
    )

    async with session.client("s3") as s3:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".yml") as temp_file:
            await s3.download_fileobj(bucket_name, object_key, temp_file)
            return temp_file.name


def parse_s3_url(s3_url: str) -> Tuple[str, str]:
    """Parse an S3 URL into bucket name and object key."""
    if not s3_url.startswith("s3://"):
        raise ValueError("Invalid S3 URL")
    path = s3_url[5:]
    bucket_name, _, object_key = path.partition("/")
    if not bucket_name or not object_key:
        raise ValueError("S3 URL must be in the format s3://bucket_name/object_key")
    return bucket_name, object_key


class AnsibleRunnerClient:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        private_key_path: Optional[str] = None,
        password: Optional[str] = None,
        playbook_path: str = "",
    ):
        self.host = host
        self.port = port
        self.user = user
        self.private_key_path = private_key_path
        self.password = password
        self.playbook_path = playbook_path

    async def execute_playbook(self, extra_vars: Dict[str, Any]) -> Dict[str, Any]:
        os.environ["ANSIBLE_HOST_KEY_CHECKING"] = "False"

        env_vars = {"ANSIBLE_REMOTE_USER": self.user}
        if self.password:
            env_vars["ANSIBLE_ASK_PASS"] = "True"

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_async(
                private_data_dir="/tmp",
                playbook=self.playbook_path,
                extravars=extra_vars,
                inventory=f"{self.host},",
                ssh_key=self.private_key_path if self.private_key_path else None,
                envvars=env_vars,
                passwords={"conn_pass": self.password} if self.password else None,
            ),
        )
        return result.stats if result else {"status": "failed", "details": "No result"}


async def create_ansible_runner_client(s3_url: str) -> AnsibleRunnerClient:
    playbook_path = await download_playbook_from_s3(s3_url)
    private_key_path = secrets.get("ANSIBLE_PRIVATE_KEY")
    password = secrets.get("ANSIBLE_PASSWORD")

    if not private_key_path and not password:
        raise ValueError("Either `ANSIBLE_PRIVATE_KEY` or `ANSIBLE_PASSWORD` must be set")

    return AnsibleRunnerClient(
        host=secrets.get("ANSIBLE_HOST"),
        port=secrets.get("ANSIBLE_PORT"),
        user=secrets.get("ANSIBLE_USER"),
        private_key_path=private_key_path,
        password=password,
        playbook_path=playbook_path,
    )

# TODO: accept ansible galaxy identifier, url, git, etc.
@registry.register(
    default_title="Run Ansible Playbook from S3",
    description="Execute a given Ansible playbook hosted on S3 with specified variables",
    display_group="Ansible",
    namespace="integrations.ansible_runner_s3",
    secrets=[ansible_runner_secret, aws_secret],
)
async def run_ansible_playbook_from_s3(
    s3_url: Annotated[
        str,
        Field(..., description="S3 URL to the Ansible playbook to execute"),
    ],
    extra_vars: Annotated[
        Dict[str, Any],
        Field(..., description="Extra variables for the Ansible playbook"),
    ],
) -> Dict[str, Any]:
    client = await create_ansible_runner_client(s3_url=s3_url)
    return await client.execute_playbook(extra_vars)


@registry.register(
    default_title="Check Ansible Connection from S3 Playbook",
    description="Verify connection to a target host using an Ansible playbook hosted on S3",
    display_group="Ansible",
    namespace="integrations.ansible_runner_s3",
    secrets=[ansible_runner_secret, aws_secret],
)
async def check_ansible_connection_from_s3(
    target_host: Annotated[
        str,
        Field(..., description="Target host to check connection"),
    ],
    s3_url: Annotated[
        str,
        Field(..., description="S3 URL to a basic connection test playbook"),
    ],
) -> Dict[str, Any]:
    extra_vars = {"target_host": target_host}
    client = await create_ansible_runner_client(s3_url=s3_url)
    return await client.execute_playbook(extra_vars)
