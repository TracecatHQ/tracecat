import os
from typing import Annotated, Any, TypedDict

import httpx
from requests.auth import HTTPBasicAuth

from tracecat.registry import Field, RegistrySecret, registry


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | dict[str, Any] | list[Any] | None


jira_secret = RegistrySecret(
    name="jira",
    keys=["JIRA_USERNAME", "JIRA_API_TOKEN"],
)

"""Jira Secret

- name: `jira`
- keys:
    - `JIRA_USERNAME`
    - `JIRA_API_TOKEN`

"""


@registry.register(
    default_title="Create Jira Issue",
    description="Creates a Jira ticket with the information provided",
    display_group="Jira",
    namespace="integrations.jira",
    secrets=[jira_secret],
)
async def create_issue(
    atlassian_domain: Annotated[
        str,
        Field(
            ...,
            description="Your Atlassian domain. (i.e. https://your-jira-instance.atlassian.net)",
        ),
    ],
    project_id: Annotated[
        str,
        Field(
            ...,
            description="The Jira Project ID of the project you wish to create an issue in.",
        ),
    ],
    issue_summary: Annotated[str, Field(..., description="The issue summary (title).")],
    issue_priority: Annotated[
        str, Field(..., description="The severity of the issue.")
    ],
    issue_type: Annotated[
        str, Field(..., description="The ID of the Issue Type for the issue.")
    ],
    optional_fields: Annotated[
        dict,
        Field(
            ...,
            description="Optional dictionary of fields you wish to add into the issue.",
        ),
    ] = None,
) -> list[dict[str, Any]]:
    url = f"{atlassian_domain}/rest/api/3/issue"
    AUTH_TOKEN = HTTPBasicAuth(os.getenv("JIRA_USERNAME"), os.getenv("JIRA_API_TOKEN"))
    if not AUTH_TOKEN:
        raise ValueError("Missing JIRA_USERNAME or JIRA_API_TOKEN")

    headers = {
        "Accept": "application/json",
        "User-Agent": "Tracecat",
        "Content-Type": "application/json",
    }

    issue_data = {
        "fields": {
            "project": {"id": project_id},
            "summary": issue_summary,
            "priority": {"id": issue_priority},
            "issuetype": {"id": issue_type},
        }
    }

    # If optional fields are provided, merge them into the issue data
    if optional_fields:
        issue_data["fields"].update(optional_fields)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=headers, auth=AUTH_TOKEN, json=issue_data
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Update Jira Issue",
    description="Updates a Jira ticket with the information provided",
    display_group="Jira",
    namespace="integrations.jira",
    secrets=[jira_secret],
)
async def update_issue(
    atlassian_domain: Annotated[
        str,
        Field(
            ...,
            description="Your Atlassian domain. (i.e. https://your-jira-instance.atlassian.net)",
        ),
    ],
    issue_key: Annotated[
        str, Field(..., description="The issue key for the issue you wish to update.")
    ],
    update_data: Annotated[
        dict,
        Field(
            ...,
            description="The data you wish to update the issue with. This should be formatted exactly as the custom field is configured.",
        ),
    ] = None,
) -> list[dict[str, Any]]:
    url = f"{atlassian_domain}/rest/api/3/issue/{issue_key}"
    AUTH_TOKEN = HTTPBasicAuth(os.getenv("JIRA_USERNAME"), os.getenv("JIRA_API_TOKEN"))
    if not AUTH_TOKEN:
        raise ValueError("Missing JIRA_USERNAME or JIRA_API_TOKEN")

    headers = {
        "Accept": "application/json",
        "User-Agent": "Tracecat",
        "Content-Type": "application/json",
    }

    update_payload = {"fields": update_data} | {}

    async with httpx.AsyncClient() as client:
        response = await client.put(
            url, headers=headers, auth=AUTH_TOKEN, json=update_payload
        )
        response.raise_for_status()
        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            data=update_payload,  # No content
        )
