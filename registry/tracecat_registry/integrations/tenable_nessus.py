"""Generic interface for Tenable Nessus via pyTenable.

https://github.com/tenable/pyTenable/blob/main/tests/nessus/conftest.py
"""

from tenable.nessus import Nessus

from tracecat_registry import RegistrySecret, registry, secrets

tenable_secret = RegistrySecret(
    name="tenable_nessus",
    keys=["TENNABLE_ACCESS_KEY", "TENNABLE_SECRET_KEY"],
)
"""Tenable Nessus secret.

- name: `tenable_nessus`
- keys:
    - `TENNABLE_ACCESS_KEY`
    - `TENNABLE_SECRET_KEY`
"""


@registry.register(
    default_title="Call Nessus API",
    description="Call any Nessus API using the pyTenable library",
    display_group="Tenabl Nessus",
    doc_url="https://pytenable.readthedocs.io/en/stable/api/nessus/index.html",
    namespace="integrations.tenable_nessus",
    secrets=[tenable_secret],
)
async def call_api(object_name: str, method_name: str, params: dict) -> dict:
    nessus = Nessus(
        access_key=secrets.get("TENNABLE_ACCESS_KEY"),
        secret_key=secrets.get("TENNABLE_SECRET_KEY"),
    )
    return await getattr(getattr(nessus, object_name), method_name)(**params)
