"""Generic interface to FalconPy Uber class."""

from typing import Annotated, Any

from falconpy import APIHarnessV2
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

crowdstrike_secret = RegistrySecret(
    name="crowdstrike",
    keys=["CROWDSTRIKE_CLIENT_ID", "CROWDSTRIKE_CLIENT_SECRET"],
)
"""Crowdstrike OAuth2.0 credentials.

- name: `crowdstrike`
- keys:
    - `CROWDSTRIKE_CLIENT_ID`
    - `CROWDSTRIKE_CLIENT_SECRET`
"""


@registry.register(
    default_title="Call Crowdstrike API",
    description="Instantiate a FalconPy Uber Class client and call a Crowdstrike API method.",
    display_group="Crowdstrike FalconPy",
    doc_url="https://falconpy.io/Usage/Basic-Uber-Class-usage.html",
    namespace="integrations.crowdstrike_falconpy",
    secrets=[crowdstrike_secret],
)
async def call_command(
    operation_id: Annotated[
        str,
        Field(
            ...,
            description="Operation ID from https://www.falconpy.io/Operations/All-Operations.html",
        ),
    ],
    params: Annotated[
        dict[str, Any],
        Field(..., description="Parameters to pass into the command"),
    ] = None,
    member_cid: Annotated[
        str | None,
        Field(..., description="Multi-tenant customer ID"),
    ] = None,
) -> dict[str, Any]:
    params = params or {}
    falcon = APIHarnessV2(
        client_id=secrets.get("CROWDSTRIKE_CLIENT_ID"),
        client_secret=secrets.get("CROWDSTRIKE_CLIENT_SECRET"),
        member_cid=member_cid,
    )
    return falcon.command(operation_id, **params)
