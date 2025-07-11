"""HackerOne API Integration."""

import base64
from typing import Annotated, Any

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

hacker_one_secret = RegistrySecret(
    name="hacker_one",
    keys=["HACKER_ONE_API_USERNAME", "HACKER_ONE_API_TOKEN"],
)
"""HackerOne API credentials.

- name: `hacker_one`
- keys:
    - `HACKER_ONE_API_USERNAME`
    - `HACKER_ONE_API_TOKEN`
"""


@registry.register(
    default_title="Get programs",
    description="Get a paginated list of programs from HackerOne.",
    display_group="HackerOne (H1)",
    doc_url="https://api.hackerone.com/customer-resources/?python#programs-get-programs",
    namespace="tools.hacker_one",
    secrets=[hacker_one_secret],
)
async def get_programs(
    page_number: Annotated[
        int,
        Field(1, description="The page number to retrieve (starts at 1)"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(25, description="Number of programs per page (1-100)"),
    ] = 25,
) -> dict[str, Any]:
    """Get a paginated list of programs from HackerOne."""
    username = secrets.get("HACKER_ONE_API_USERNAME")
    api_key = secrets.get("HACKER_ONE_API_TOKEN")

    auth_string = f"{username}:{api_key}"
    auth = base64.b64encode(auth_string.encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.hackerone.com/v1/me/programs/",
            params={
                "page[number]": page_number,
                "page[size]": page_size,
            },
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get reports",
    description="Get a paginated list of reports from HackerOne.",
    display_group="HackerOne (H1)",
    doc_url="https://api.hackerone.com/customer-resources/?python#reports-update-weakness",
    namespace="tools.hacker_one",
    secrets=[hacker_one_secret],
)
async def get_reports(
    filters: Annotated[
        dict[str, Any],
        Field(
            ...,
            description="Filters to apply to the reports. This is a required parameter. For example, `{'filter[program][]': 'tracecat', 'filter[state][]': ['new', 'triaged'], 'filter[assignee][]': 'Tracecat Team'}`",
        ),
    ],
    page_number: Annotated[
        int,
        Field(1, description="The page number to retrieve (starts at 1)"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(25, description="Number of reports per page (1-100)"),
    ] = 25,
) -> dict[str, Any]:
    """Get a paginated list of reports from HackerOne."""
    if filters is None:
        params = {
            "page[number]": page_number,
            "page[size]": page_size,
        }
    else:
        params = {
            "page[number]": page_number,
            "page[size]": page_size,
            **filters,
        }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.hackerone.com/v1/reports/",
            params=params,
            auth=(
                secrets.get("HACKER_ONE_API_USERNAME"),
                secrets.get("HACKER_ONE_API_TOKEN"),
            ),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get program",
    description="Get details of a specific program from HackerOne.",
    display_group="HackerOne (H1)",
    doc_url="https://api.hackerone.com/customer-resources/?python#programs-get-program",
    namespace="tools.hacker_one",
    secrets=[hacker_one_secret],
)
async def get_program(
    program_id: Annotated[
        int,
        Field(..., description="The ID of the program to retrieve"),
    ],
) -> dict[str, Any]:
    """Get details of a specific program from HackerOne."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.hackerone.com/v1/programs/{program_id}/",
            auth=(
                secrets.get("HACKER_ONE_API_USERNAME"),
                secrets.get("HACKER_ONE_API_TOKEN"),
            ),
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get report",
    description="Get details of a specific report from HackerOne.",
    display_group="HackerOne (H1)",
    doc_url="https://api.hackerone.com/customer-resources/?python#reports-get-report",
    namespace="tools.hacker_one",
    secrets=[hacker_one_secret],
)
async def get_report(
    report_id: Annotated[
        int,
        Field(..., description="The ID of the report to retrieve"),
    ],
) -> dict[str, Any]:
    """Get details of a specific report from HackerOne."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.hackerone.com/v1/reports/{report_id}",
            auth=(
                secrets.get("HACKER_ONE_API_USERNAME"),
                secrets.get("HACKER_ONE_API_TOKEN"),
            ),
        )
        response.raise_for_status()
        return response.json()
