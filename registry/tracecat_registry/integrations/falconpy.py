"""Generic interface to FalconPy Uber class."""

import os
from typing import Annotated, Any

from falconpy import APIHarnessV2
from tracecat.registry import Field, RegistrySecret, registry

crowdstrike_secret = RegistrySecret(
    name="crowdstrike",
    keys=["CROWDSTRIKE_CLIENT_ID", "CROWDSTRIKE_CLIENT_SECRET"],
)
"""Crowdstrike secret.

- name: `crowdstrike`
- keys:
    - `CROWDSTRIKE_CLIENT_ID`
    - `CROWDSTRIKE_CLIENT_SECRET`
"""


@registry.register(
    default_title="Call FalconPy command",
    description="Call any Crowdstrike API via FalconPy.",
    display_group="Crowdstrike",
    namespace="integrations.crowdstrike",
    secrets=[crowdstrike_secret],
)
async def call_falconpy_command(
    operation_id: Annotated[
        str,
        Field(
            ...,
            description="The operation ID (https://www.falconpy.io/Operations/All-Operations.html) to call.",
        ),
    ],
    params: Annotated[
        dict[str, Any],
        Field(..., description="The parameters to pass to the operation."),
    ] = None,
    member_cid: Annotated[
        str | None,
        Field(..., description="(Sensitive) The member CID to call the operation on."),
    ] = None,
) -> dict[str, Any]:
    params = params or {}
    falcon = APIHarnessV2(
        client_id=os.getenv("CROWDSTRIKE_CLIENT_ID"),
        client_secret=os.getenv("CROWDSTRIKE_CLIENT_SECRET"),
        member_cid=member_cid,
    )
    return falcon.command(operation_id, **params)
