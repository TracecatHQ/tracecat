"""ServiceNow Table API integrations using OAuth client credentials."""

from typing import Annotated, Any

import httpx
from typing_extensions import Doc

from tracecat_registry import registry, secrets
from tracecat_registry._internal.models import RegistryOAuthSecret

servicenow_oauth_secret = RegistryOAuthSecret(
    provider_id="servicenow",
    grant_type="client_credentials",
)
"""ServiceNow OAuth2.0 credentials (client credentials grant).

- provider_id: `servicenow`
- grant_type: `client_credentials`
- token_name: `SERVICENOW_SERVICE_TOKEN`
"""


SERVICENOW_TABLE_API_DOCS_URL = "https://www.servicenow.com/docs/bundle/zurich-api-reference/page/build/applications/concept/api-rest.html"


async def _request_table_api(
    method: str,
    base_url: str,
    table_name: str,
    record_id: str | None = None,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = secrets.get(servicenow_oauth_secret.token_name)
    url = f"{base_url.rstrip('/')}/table/{table_name}"
    if record_id:
        url = f"{url}/{record_id}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=payload,
        )
        response.raise_for_status()
        if not response.content:
            return {"status": "success", "status_code": response.status_code}
        return response.json()


@registry.register(
    default_title="Create ServiceNow record",
    description="Create a record in a ServiceNow table.",
    display_group="ServiceNow",
    doc_url=SERVICENOW_TABLE_API_DOCS_URL,
    namespace="tools.servicenow",
    secrets=[servicenow_oauth_secret],
)
async def create_record(
    base_url: Annotated[
        str,
        Doc("Base API URL, e.g. https://example.service-now.com/api/now."),
    ],
    table_name: Annotated[
        str,
        Doc("ServiceNow table name, e.g. 'incident', 'sys_user'."),
    ],
    record: Annotated[
        dict[str, Any],
        Doc("JSON payload for the new record."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Doc("Optional query parameters such as sysparm_display_value."),
    ] = None,
) -> dict[str, Any]:
    return await _request_table_api(
        method="POST",
        base_url=base_url,
        table_name=table_name,
        params=params,
        payload=record,
    )


@registry.register(
    default_title="Query ServiceNow records",
    description="Query a ServiceNow table with optional filters.",
    display_group="ServiceNow",
    doc_url=SERVICENOW_TABLE_API_DOCS_URL,
    namespace="tools.servicenow",
    secrets=[servicenow_oauth_secret],
)
async def query_records(
    base_url: Annotated[
        str,
        Doc("Base API URL, e.g. https://example.service-now.com/api/now."),
    ],
    table_name: Annotated[
        str,
        Doc("ServiceNow table name, e.g. 'incident', 'sys_user'."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Doc(
            "Optional query parameters such as sysparm_query, sysparm_fields, or sysparm_limit."
        ),
    ] = None,
) -> dict[str, Any]:
    return await _request_table_api(
        method="GET",
        base_url=base_url,
        table_name=table_name,
        params=params,
    )


@registry.register(
    default_title="Get ServiceNow record",
    description="Retrieve a record from a ServiceNow table by sys_id.",
    display_group="ServiceNow",
    doc_url=SERVICENOW_TABLE_API_DOCS_URL,
    namespace="tools.servicenow",
    secrets=[servicenow_oauth_secret],
)
async def get_record(
    base_url: Annotated[
        str,
        Doc("Base API URL, e.g. https://example.service-now.com/api/now."),
    ],
    table_name: Annotated[
        str,
        Doc("ServiceNow table name, e.g. 'incident', 'sys_user'."),
    ],
    record_id: Annotated[
        str,
        Doc("Unique record identifier (sys_id)."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Doc(
            "Optional query parameters such as sysparm_fields or sysparm_display_value."
        ),
    ] = None,
) -> dict[str, Any]:
    return await _request_table_api(
        method="GET",
        base_url=base_url,
        table_name=table_name,
        record_id=record_id,
        params=params,
    )


@registry.register(
    default_title="Update ServiceNow record",
    description="Update fields on a ServiceNow table record.",
    display_group="ServiceNow",
    doc_url=SERVICENOW_TABLE_API_DOCS_URL,
    namespace="tools.servicenow",
    secrets=[servicenow_oauth_secret],
)
async def update_record(
    base_url: Annotated[
        str,
        Doc("Base API URL, e.g. https://example.service-now.com/api/now."),
    ],
    table_name: Annotated[
        str,
        Doc("ServiceNow table name, e.g. 'incident', 'sys_user'."),
    ],
    record_id: Annotated[
        str,
        Doc("Unique record identifier (sys_id)."),
    ],
    fields: Annotated[
        dict[str, Any],
        Doc("Fields to update on the record."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Doc("Optional query parameters such as sysparm_display_value."),
    ] = None,
) -> dict[str, Any]:
    return await _request_table_api(
        method="PATCH",
        base_url=base_url,
        table_name=table_name,
        record_id=record_id,
        params=params,
        payload=fields,
    )


@registry.register(
    default_title="Delete ServiceNow record",
    description="Delete a ServiceNow table record by sys_id.",
    display_group="ServiceNow",
    doc_url=SERVICENOW_TABLE_API_DOCS_URL,
    namespace="tools.servicenow",
    secrets=[servicenow_oauth_secret],
)
async def delete_record(
    base_url: Annotated[
        str,
        Doc("Base API URL, e.g. https://example.service-now.com/api/now."),
    ],
    table_name: Annotated[
        str,
        Doc("ServiceNow table name, e.g. 'incident', 'sys_user'."),
    ],
    record_id: Annotated[
        str,
        Doc("Unique record identifier (sys_id)."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Doc("Optional query parameters such as sysparm_display_value."),
    ] = None,
) -> dict[str, Any]:
    return await _request_table_api(
        method="DELETE",
        base_url=base_url,
        table_name=table_name,
        record_id=record_id,
        params=params,
    )
