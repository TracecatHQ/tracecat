"""Have I Been Pwned integration for checking email breaches and data exposure."""

from typing import Annotated, Any, Optional
from urllib.parse import quote

import httpx
from pydantic import Field
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

hibp_secret = RegistrySecret(
    name="hibp",
    keys=["HIBP_API_KEY"],
)
"""Have I Been Pwned API key.

- name: `hibp`
- keys:
    - `HIBP_API_KEY`
"""


@registry.register(
    default_title="Check email for breaches",
    description="Check if an email address has been compromised in known data breaches.",
    display_group="Have I Been Pwned",
    doc_url="https://haveibeenpwned.com/API/v3#BreachesForAccount",
    namespace="tools.hibp",
    secrets=[hibp_secret],
)
async def check_email_breaches(
    email: Annotated[str, Doc("Email address to check for breaches")],
    truncate_response: Annotated[
        bool,
        Doc("Return only breach names (True) or full breach details (False)"),
        Field(default=True),
    ],
    include_unverified: Annotated[
        bool,
        Doc("Include unverified breaches in results"),
        Field(default=True),
    ],
    domain_filter: Annotated[
        Optional[str],
        Doc("Filter results to only breaches from this domain"),
        Field(default=None),
    ],
) -> dict[str, Any]:
    """Check if an email address has been found in any data breaches."""

    # URL encode the email address
    encoded_email = quote(email.strip())

    # Build query parameters
    params = {
        "truncateResponse": str(truncate_response).lower(),
        "includeUnverified": str(include_unverified).lower(),
    }

    if domain_filter:
        params["domain"] = domain_filter

    headers = {
        "hibp-api-key": secrets.get("HIBP_API_KEY"),
        "User-Agent": "Tracecat-HIBP-Integration",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{encoded_email}",
            headers=headers,
            params=params,
        )

        if response.status_code == 404:
            return {
                "email": email,
                "breaches_found": False,
                "breach_count": 0,
                "message": "No breaches found for this email address",
                "breaches": [],
            }

        response.raise_for_status()
        breaches = response.json()

        return {
            "email": email,
            "breaches_found": True,
            "breach_count": len(breaches),
            "breaches": breaches,
        }


@registry.register(
    default_title="Get breach details",
    description="Get detailed information about a specific data breach.",
    display_group="Have I Been Pwned",
    doc_url="https://haveibeenpwned.com/API/v3#SingleBreach",
    namespace="tools.hibp",
)
async def get_breach_details(
    breach_name: Annotated[str, Doc("Name of the breach to get details for")],
) -> dict[str, Any]:
    """Get detailed information about a specific data breach."""

    headers = {
        "User-Agent": "Tracecat-HIBP-Integration",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://haveibeenpwned.com/api/v3/breach/{breach_name}",
            headers=headers,
        )

        if response.status_code == 404:
            return {
                "breach_name": breach_name,
                "found": False,
                "message": "Breach not found",
            }

        response.raise_for_status()
        breach_data = response.json()

        return {
            "breach_name": breach_name,
            "found": True,
            "breach_data": breach_data,
        }


@registry.register(
    default_title="Get all breaches",
    description="Get a list of all data breaches in the system.",
    display_group="Have I Been Pwned",
    doc_url="https://haveibeenpwned.com/API/v3#AllBreaches",
    namespace="tools.hibp",
)
async def get_all_breaches(
    domain_filter: Annotated[
        Optional[str],
        Doc("Filter breaches to only those affecting this domain"),
        Field(default=None),
    ],
) -> dict[str, Any]:
    """Get a list of all data breaches in the system."""

    headers = {
        "User-Agent": "Tracecat-HIBP-Integration",
    }

    params = {}
    if domain_filter:
        params["domain"] = domain_filter

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://haveibeenpwned.com/api/v3/breaches",
            headers=headers,
            params=params,
        )

        response.raise_for_status()
        breaches = response.json()

        return {
            "total_breaches": len(breaches),
            "domain_filter": domain_filter,
            "breaches": breaches,
        }


@registry.register(
    default_title="Check email for pastes",
    description="Check if an email address has been found in any pastes.",
    display_group="Have I Been Pwned",
    doc_url="https://haveibeenpwned.com/API/v3#PastesForAccount",
    namespace="tools.hibp",
    secrets=[hibp_secret],
)
async def check_email_pastes(
    email: Annotated[str, Doc("Email address to check for pastes")],
) -> dict[str, Any]:
    """Check if an email address has been found in any pastes."""

    # URL encode the email address
    encoded_email = quote(email.strip())

    headers = {
        "hibp-api-key": secrets.get("HIBP_API_KEY"),
        "User-Agent": "Tracecat-HIBP-Integration",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://haveibeenpwned.com/api/v3/pasteaccount/{encoded_email}",
            headers=headers,
        )

        if response.status_code == 404:
            return {
                "email": email,
                "pastes_found": False,
                "paste_count": 0,
                "message": "No pastes found for this email address",
                "pastes": [],
            }

        response.raise_for_status()
        pastes = response.json()

        return {
            "email": email,
            "pastes_found": True,
            "paste_count": len(pastes),
            "pastes": pastes,
        }


@registry.register(
    default_title="Get latest breach",
    description="Get the most recently added breach in the system.",
    display_group="Have I Been Pwned",
    doc_url="https://haveibeenpwned.com/API/v3#LatestBreach",
    namespace="tools.hibp",
)
async def get_latest_breach() -> dict[str, Any]:
    """Get the most recently added breach in the system."""

    headers = {
        "User-Agent": "Tracecat-HIBP-Integration",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://haveibeenpwned.com/api/v3/latestbreach",
            headers=headers,
        )

        response.raise_for_status()
        breach_data = response.json()

        return {
            "latest_breach": breach_data,
        }


@registry.register(
    default_title="Get data classes",
    description="Get all data classes in the system.",
    display_group="Have I Been Pwned",
    doc_url="https://haveibeenpwned.com/API/v3#DataClasses",
    namespace="tools.hibp",
)
async def get_data_classes() -> dict[str, Any]:
    """Get all data classes in the system."""

    headers = {
        "User-Agent": "Tracecat-HIBP-Integration",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://haveibeenpwned.com/api/v3/dataclasses",
            headers=headers,
        )

        response.raise_for_status()
        data_classes = response.json()

        return {
            "total_data_classes": len(data_classes),
            "data_classes": data_classes,
        }
