"""Generic interface to PyVelociraptor class.
Author: Zane Gittins
"""

import base64
import json
from typing import Annotated, Any

import grpc
import yaml
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry.integrations.velociraptor import api_pb2, api_pb2_grpc

velociraptor_secret = RegistrySecret(
    name="velociraptor_ssl",
    keys=["CONFIGURATION"],
    optional_keys=["ORGANIZATION_ID"],
)
"""Velociraptor secret.

- name: `velociraptor_ssl`
- keys:
    - `CONFIGURATION`
- optional_keys:
    - `ORGANIZATION_ID`

Note: The configuration needs to be base64 encoded before adding it as a secret in Tracecat to preserve formatting.
You can use the following command to do so: `cat api.config.yaml | base64 -w`

Please see Velociraptor docs for how to create a configuration and enable the server API:
https://docs.velociraptor.app/docs/server_automation/server_api/.

Example Usage
-------------
Start a velociraptor collection with query:
>>> select collect_client(client_id='${{ TRIGGER.client_id }}',artifacts='Generic.Client.Info',
        env=dict()) as collection from scope()

Read results with query:
>>> select * from source(client_id='${{ var.item.collection.request.client_id
  }}',flow_id='${{ var.item.collection.flow_id
  }}',artifact='Generic.Client.Info/BasicInformation')
"""


@registry.register(
    default_title="Execute Velociraptor query",
    description="Run VQL queries against a Velociraptor server via the gRPC api.",
    display_group="Velociraptor",
    namespace="integrations.velociraptor",
    secrets=[velociraptor_secret],
)
async def run_velociraptor_query(
    query: Annotated[
        str,
        Field(
            ...,
            description="VQL query to run against the Velociraptor server.",
        ),
    ],
    max_rows: Annotated[
        int,
        Field(
            ...,
            description="Maximum rows to return.",
        ),
    ],
    timeout: Annotated[
        int,
        Field(
            ...,
            description="Query timeout.",
        ),
    ],
) -> list[dict[str, Any]]:
    config_data = secrets.get("CONFIGURATION")
    config = base64.b64decode(
        config_data
    )  # configuration is base64 encoded: "cat api.config.yaml | base64 -w"
    config = yaml.safe_load(config_data)

    creds = grpc.ssl_channel_credentials(
        root_certificates=config["ca_certificate"].encode("utf8"),
        private_key=config["client_private_key"].encode("utf8"),
        certificate_chain=config["client_cert"].encode("utf8"),
    )

    options = (
        (
            "grpc.ssl_target_name_override",
            "VelociraptorServer",
        ),
    )
    async with grpc.aio.secure_channel(
        config["api_connection_string"], creds, options
    ) as channel:
        stub = api_pb2_grpc.APIStub(channel)
        request = api_pb2.VQLCollectorArgs(
            org_id=secrets.get("ORGANIZATION_ID", ""),
            max_wait=1,
            max_row=max_rows,
            Query=[
                api_pb2.VQLRequest(
                    Name="Tracecat",
                    VQL=query,
                )
            ],
        )
        result = []
        responses = await stub.Query(request, timeout=timeout)
        async for response in responses:
            if not response.Response or len(response.Response) == 0:
                continue
            result.extend(json.loads(response.Response))
        return result
