"""Generic interface for Tenable Nessus via pyTenable.

https://github.com/tenable/pyTenable/blob/main/tests/nessus/conftest.py
"""

from typing import Annotated, Any

from pydantic import Field
from tenable.nessus import Nessus

from tracecat_registry import RegistrySecret, registry, secrets

tenable_secret = RegistrySecret(
    name="tenable_nessus",
    keys=["TENABLE_ACCESS_KEY", "TENABLE_SECRET_KEY"],
)
"""Tenable Nessus secret.

- name: `tenable_nessus`
- keys:
    - `TENABLE_ACCESS_KEY`
    - `TENABLE_SECRET_KEY`
"""


@registry.register(
    default_title="Call API",
    description="Instantiate a pyTenable Nessus client and call a Nessus API method.",
    display_group="pyTenable",
    doc_url="https://pytenable.readthedocs.io/en/stable/api/nessus/index.html",
    namespace="tools.pytenable",
    secrets=[tenable_secret],
)
async def call_api(
    object_name: Annotated[str, Field(..., description="Nessus API object name")],
    method_name: Annotated[str, Field(..., description="Nessus API method name")],
    params: Annotated[
        dict[str, Any], Field(..., description="Nessus API method parameters")
    ],
    base_url: Annotated[str, Field(..., description="Nessus API URL")],
) -> dict:
    nessus = Nessus(
        url=base_url,
        access_key=secrets.get("TENABLE_ACCESS_KEY"),
        secret_key=secrets.get("TENABLE_SECRET_KEY"),
    )
    return await getattr(getattr(nessus, object_name), method_name)(**params)
