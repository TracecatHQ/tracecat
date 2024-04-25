"""Integrations with Emailrep API.

Required credentials: `emailrep` secret with `EMAILREP_API_KEY` key.

API reference: https://docs.sublimesecurity.com/reference/get_-email
"""

import os

import httpx

from tracecat.integrations._registry import registry

EMAILREP_BASE_URL = "https://emailrep.io"


def create_emailrep_client(app_name: str):
    emailrep_api_key = os.environ["EMAILREP_API_KEY"]
    headers = {"User-Agent": f"tracecat/{app_name}", "Key": emailrep_api_key}
    return httpx.Client(base_url=EMAILREP_BASE_URL, headers=headers)


@registry.register(
    description="Check email reputation",
    secrets=["emailrep"],
)
def check_email_reputation(email: str, app_name: str) -> dict[str, str] | str:
    client = create_emailrep_client(app_name)
    response = client.get(f"/{email}")
    response.raise_for_status()
    return response.json()
