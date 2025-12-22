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
    default_title="Call command",
    description="Instantiate a FalconPy Uber Class client and call a FalconPy API method.",
    display_group="FalconPy",
    doc_url="https://falconpy.io/Usage/Basic-Uber-Class-usage.html",
    namespace="tools.falconpy",
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
        dict[str, Any] | None,
        Field(..., description="Parameters to pass into the command"),
    ] = None,
    member_cid: Annotated[
        str | None,
        Field(..., description="Multi-tenant customer ID"),
    ] = None,
    base_url: Annotated[
        str | None,
        Field(..., description="Base URL for the Falcon API"),
    ] = None,
    extra_kwargs: Annotated[
        dict[str, Any] | None,
        Field(..., description="Extra keyword arguments to pass to the Falcon API"),
    ] = None,
) -> Any:
    params = params or {}
    extra_kwargs = extra_kwargs or {}
    falcon = APIHarnessV2(
        base_url=base_url,
        client_id=secrets.get("CROWDSTRIKE_CLIENT_ID"),
        client_secret=secrets.get("CROWDSTRIKE_CLIENT_SECRET"),
        member_cid=member_cid,
        **extra_kwargs,
    )
    return falcon.command(operation_id, **params)
