"""Google SecOps (Chronicle) SOAR API integration UDFs for Tracecat.

This module provides Google Chronicle SOAR API integration for security automation workflows,
including case management, alert handling, and bulk operations.

Requires an API key from Chronicle SOAR.
Configure the API credentials in Tracecat secrets.
"""

from typing import Annotated, Any

import httpx
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

google_secops_soar_secret = RegistrySecret(
    name="google_secops_soar",
    keys=["GOOGLE_SECOPS_API_KEY"],
)
"""Google SecOps SOAR API credentials.

- name: `google_secops_soar`
- keys:
    - `GOOGLE_SECOPS_API_KEY`: Chronicle SOAR API key (found in SOAR Settings → API Keys)
"""


def _get_secops_headers() -> dict[str, str]:
    """Get headers for Chronicle SOAR API requests."""
    api_key = secrets.get("GOOGLE_SECOPS_API_KEY")
    return {
        "AppKey": api_key,
        "Content-Type": "application/json;odata.metadata=minimal;odata.streaming=true",
        "accept": "application/json;odata.metadata=minimal;odata.streaming=true",
    }


@registry.register(
    default_title="Search SOAR cases",
    display_group="Google SecOps SOAR",
    description="Search and filter cases in Chronicle SOAR using comprehensive query parameters",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def search_cases(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    title: Annotated[
        str | None,
        Doc("Search by case title/name (partial match supported)"),
    ] = None,
    time_range_filter: Annotated[
        int | None,
        Doc(
            "Predefined time range in days: 0=CUSTOM, 1=LAST_DAY, 2=LAST_2_DAYS, "
            "3=LAST_3_DAYS, 4=LAST_4_DAYS, 7=LAST_WEEK, 14=LAST_2_WEEKS, "
            "30=LAST_MONTH, 90=LAST_3_MONTHS, 180=LAST_6_MONTHS, 365=LAST_YEAR, "
            "395=LAST_13_MONTHS"
        ),
    ] = None,
    start_time: Annotated[
        str | None,
        Doc(
            "UTC start time (ISO 8601 format, e.g., '2024-01-01T00:00:00.000Z'). "
            "Only used when time_range_filter=0 (CUSTOM)"
        ),
    ] = None,
    end_time: Annotated[
        str | None,
        Doc(
            "UTC end time (ISO 8601 format). Only used when time_range_filter=0 (CUSTOM)"
        ),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Doc("List of case tags to filter by"),
    ] = None,
    priorities: Annotated[
        list[str] | None,
        Doc("List of priorities: Informative, Low, Medium, High, Critical"),
    ] = None,
    stages: Annotated[
        list[str] | None,
        Doc(
            "List of stages: Triage, Assessment, Investigation, Incident, Improvement, Research"
        ),
    ] = None,
    environments: Annotated[
        list[str] | None,
        Doc("List of environments to filter by"),
    ] = None,
    assigned_users: Annotated[
        list[str] | None,
        Doc("List of user IDs or @Role names"),
    ] = None,
    is_case_closed: Annotated[
        bool | None,
        Doc("Filter by case status (true=closed, false=open, null=all)"),
    ] = None,
    importance: Annotated[
        list[str] | None,
        Doc("Filter by importance: ['True'] for important cases only"),
    ] = None,
    incident: Annotated[
        list[str] | None,
        Doc("Filter by incident flag: ['True'] for incidents only"),
    ] = None,
    case_ids: Annotated[
        list[int] | None,
        Doc("List of specific case IDs to retrieve"),
    ] = None,
    page_size: Annotated[
        int,
        Doc("Number of results per page (max 100)"),
    ] = 50,
    requested_page: Annotated[
        int,
        Doc("Page number (0-indexed)"),
    ] = 0,
) -> dict[str, Any]:
    """Search Chronicle SOAR cases with advanced filtering.

    Use this for:
    - Finding cases by time range, tags, or priority
    - Filtering by assignment, environment, or stage
    - Identifying important or incident cases
    - Paginating through large result sets

    Returns paginated case results with totalCount and pageNumber.
    """
    headers = _get_secops_headers()

    # Build request body
    request_body: dict[str, Any] = {
        "paging": {
            "requestedPage": requested_page,
            "pageSize": min(page_size, 100),
        }
    }

    # Add optional filters
    if title:
        request_body["title"] = title
    if time_range_filter is not None:
        request_body["timeRangeFilter"] = time_range_filter
    if start_time:
        request_body["startTime"] = start_time
    if end_time:
        request_body["endTime"] = end_time
    if tags:
        request_body["tags"] = tags
    if priorities:
        request_body["priorities"] = priorities
    if stages:
        request_body["stage"] = stages
    if environments:
        request_body["environments"] = environments
    if assigned_users:
        request_body["assignedUsers"] = assigned_users
    if is_case_closed is not None:
        request_body["isCaseClosed"] = is_case_closed
    if importance:
        request_body["importance"] = importance
    if incident:
        request_body["incident"] = incident
    if case_ids:
        request_body["caseIds"] = case_ids

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/search/CaseSearchEverything",
            headers=headers,
            json=request_body,
            timeout=30.0,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Add case tag",
    display_group="Google SecOps SOAR",
    description="Add a tag to a Chronicle SOAR case for filtering and organization",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def add_case_tag(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    case_id: Annotated[
        int,
        Doc("The case ID"),
    ],
    tag: Annotated[
        str,
        Doc("Tag to add to the case"),
    ],
    alert_identifier: Annotated[
        str | None,
        Doc("Optional alert identifier within the case"),
    ] = None,
) -> dict[str, Any]:
    """Add a tag to a Chronicle SOAR case.

    Tags help with:
    - Case categorization (e.g., 'phishing', 'malware', 'data-leak')
    - Filtering and search
    - Automated workflows and playbooks

    Returns the API response.
    """
    headers = _get_secops_headers()

    request_body: dict[str, Any] = {
        "caseId": case_id,
        "tag": tag,
    }
    if alert_identifier:
        request_body["alertIdentifier"] = alert_identifier

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/cases/AddCaseTag",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Assign user to case",
    display_group="Google SecOps SOAR",
    description="Assign a specific user or SOC role to a Chronicle SOAR case",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def assign_user_to_case(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    case_id: Annotated[
        int,
        Doc("The case ID"),
    ],
    user_id: Annotated[
        str,
        Doc("User ID (GUID) or @RoleName to assign"),
    ],
    alert_identifier: Annotated[
        str | None,
        Doc("Optional alert identifier within the case"),
    ] = None,
) -> dict[str, Any]:
    """Assign a user or SOC role to a Chronicle SOAR case.

    Examples:
    - User ID: "67e2cbc1-eaa4-4b6e-a594-fe0e6278a255"
    - Role: "@Tier1" or "@SOCManager"

    The assigned user/role will be visible in the case top bar.
    """
    headers = _get_secops_headers()

    request_body: dict[str, Any] = {
        "caseId": case_id,
        "userId": user_id,
    }
    if alert_identifier:
        request_body["alertIdentifier"] = alert_identifier

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/dynamic-cases/AssignUserToCase",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Change case stage",
    display_group="Google SecOps SOAR",
    description="Change the handling stage of a Chronicle SOAR case",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def change_case_stage(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    case_id: Annotated[
        int,
        Doc("The case ID"),
    ],
    stage: Annotated[
        str,
        Doc(
            "New stage: Triage, Assessment, Investigation, Incident, Improvement, or Research"
        ),
    ],
) -> dict[str, Any]:
    """Change the stage of a Chronicle SOAR case.

    Available stages:
    - Triage: Initial case review
    - Assessment: Detailed analysis
    - Investigation: Active investigation
    - Incident: Confirmed incident
    - Improvement: Post-incident review
    - Research: Threat research

    Stages are configured in Settings → Case Data.
    """
    headers = _get_secops_headers()

    request_body = {
        "caseId": case_id,
        "stage": stage,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/dynamic-cases/ChangeCaseStage",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Update case priority",
    display_group="Google SecOps SOAR",
    description="Update the priority level of a Chronicle SOAR case",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def update_case_priority(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    case_id: Annotated[
        int,
        Doc("The case ID"),
    ],
    priority: Annotated[
        int,
        Doc("Priority: -1=Informative, 40=Low, 60=Medium, 80=High, 100=Critical"),
    ],
) -> dict[str, Any]:
    """Update the priority of a Chronicle SOAR case.

    Priority values:
    - -1: Informative (no action required)
    - 40: Low
    - 60: Medium
    - 80: High
    - 100: Critical

    Returns the API response.
    """
    headers = _get_secops_headers()

    request_body = {
        "caseId": case_id,
        "priority": priority,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/dynamic-cases/UpdateCasePriority",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Update alert priority",
    display_group="Google SecOps SOAR",
    description="Update the priority of a specific alert within a Chronicle SOAR case",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def update_alert_priority(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    case_id: Annotated[
        int,
        Doc("The case ID"),
    ],
    alert_identifier: Annotated[
        str,
        Doc("The alert identifier"),
    ],
    alert_name: Annotated[
        str,
        Doc("The alert name"),
    ],
    priority: Annotated[
        int,
        Doc("New priority: -1=Informative, 40=Low, 60=Medium, 80=High, 100=Critical"),
    ],
    previous_priority: Annotated[
        int,
        Doc("Previous priority (0=Unchanged if unknown)"),
    ] = 0,
) -> dict[str, Any]:
    """Update the priority of a specific alert within a case.

    Priority values:
    - -1: Informative
    - 40: Low
    - 60: Medium
    - 80: High
    - 100: Critical

    Use this to escalate or de-escalate specific alerts.
    """
    headers = _get_secops_headers()

    request_body = {
        "caseId": case_id,
        "alertIdentifier": alert_identifier,
        "alertName": alert_name,
        "priority": priority,
        "previousPriority": previous_priority,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/dynamic-cases/UpdateAlertPriority",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Create case comment",
    display_group="Google SecOps SOAR",
    description="Add a comment (with optional attachment) to a Chronicle SOAR case",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def create_case_comment(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    case_id: Annotated[
        int,
        Doc("The case ID"),
    ],
    comment: Annotated[
        str,
        Doc("Comment text to add to the case"),
    ],
    alert_identifier: Annotated[
        str | None,
        Doc("Optional alert identifier"),
    ] = None,
    file_name: Annotated[
        str | None,
        Doc("Optional attachment filename"),
    ] = None,
    file_type: Annotated[
        str | None,
        Doc("Optional file type (e.g., '.pdf', '.txt')"),
    ] = None,
    base64_blob: Annotated[
        str | None,
        Doc("Optional base64-encoded file content"),
    ] = None,
) -> dict[str, Any]:
    """Add a comment to a Chronicle SOAR case.

    Optionally attach a file by providing:
    - file_name: Name of the file
    - file_type: Extension (e.g., '.pdf')
    - base64_blob: Base64-encoded file content

    Comments appear in the case wall/history.
    """
    headers = _get_secops_headers()

    request_body: dict[str, Any] = {
        "caseId": case_id,
        "comment": comment,
    }
    if alert_identifier:
        request_body["alertIdentifier"] = alert_identifier
    if file_name:
        request_body["fileName"] = file_name
    if file_type:
        request_body["fileType"] = file_type
    if base64_blob:
        request_body["base64Blob"] = base64_blob

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/cases/comments",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Update case comment",
    display_group="Google SecOps SOAR",
    description="Update an existing comment in a Chronicle SOAR case",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def update_case_comment(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    comment_id: Annotated[
        int,
        Doc("The comment ID to update"),
    ],
    comment: Annotated[
        str,
        Doc("Updated comment text"),
    ],
    attachment_id: Annotated[
        int | None,
        Doc("Optional attachment ID to update"),
    ] = None,
    file_name: Annotated[
        str | None,
        Doc("Optional updated filename"),
    ] = None,
    file_type: Annotated[
        str | None,
        Doc("Optional updated file type"),
    ] = None,
    base64_blob: Annotated[
        str | None,
        Doc("Optional updated base64-encoded file content"),
    ] = None,
) -> dict[str, Any]:
    """Update an existing comment in a Chronicle SOAR case.

    You can update:
    - Comment text
    - Attached file (provide attachment_id, file_name, file_type, base64_blob)

    Returns updated comment metadata.
    """
    headers = _get_secops_headers()

    request_body: dict[str, Any] = {
        "comment": comment,
    }
    if attachment_id:
        request_body["attachmentId"] = attachment_id
    if file_name:
        request_body["fileName"] = file_name
    if file_type:
        request_body["fileType"] = file_type
    if base64_blob:
        request_body["base64Blob"] = base64_blob

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{base_url.rstrip('/')}/cases/comments/{comment_id}",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Close alert",
    display_group="Google SecOps SOAR",
    description="Close a specific alert within a Chronicle SOAR case",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def close_alert(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    source_case_id: Annotated[
        int,
        Doc("The case ID where the alert is being closed"),
    ],
    alert_identifier: Annotated[
        str,
        Doc("The alert identifier to close"),
    ],
    reason: Annotated[
        str,
        Doc("Close reason: Malicious, NotMalicious, Maintenance, or Inconclusive"),
    ],
    root_cause: Annotated[
        str,
        Doc("Root cause description"),
    ],
    comment: Annotated[
        str,
        Doc("Comment explaining why the alert is being closed"),
    ],
    usefulness: Annotated[
        str,
        Doc("Alert usefulness: None, NotUseful, or Useful"),
    ] = "None",
) -> dict[str, Any]:
    """Close a specific alert within a Chronicle SOAR case.

    Close reasons:
    - Malicious: Confirmed threat
    - NotMalicious: False positive
    - Maintenance: Planned activity
    - Inconclusive: Insufficient evidence

    Usefulness values:
    - None: Not evaluated
    - NotUseful: Alert was not helpful
    - Useful: Alert provided value

    The alert moves to a closed state and a new case may be created.
    """
    headers = _get_secops_headers()

    request_body = {
        "sourceCaseId": source_case_id,
        "alertIdentifier": alert_identifier,
        "reason": reason,
        "rootCause": root_cause,
        "comment": comment,
        "usefulness": usefulness,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/dynamic-cases/CloseAlert",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Reopen alert",
    display_group="Google SecOps SOAR",
    description="Reopen a previously closed alert in a Chronicle SOAR case",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def reopen_alert(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    case_id: Annotated[
        int,
        Doc("The case ID"),
    ],
    alert_identifier: Annotated[
        str,
        Doc("The alert identifier to reopen"),
    ],
) -> dict[str, Any]:
    """Reopen a previously closed alert in a Chronicle SOAR case.

    Use this when:
    - New evidence suggests the alert should be reinvestigated
    - The alert was incorrectly closed
    - Additional analysis is required

    Returns the API response.
    """
    headers = _get_secops_headers()

    request_body = {
        "caseId": case_id,
        "alertIdentifier": alert_identifier,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/dynamic-cases/ReopenAlert",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Bulk close cases",
    display_group="Google SecOps SOAR",
    description="Close multiple Chronicle SOAR cases at once",
    namespace="tools.google_secops_soar",
    secrets=[google_secops_soar_secret],
)
async def bulk_close_cases(
    base_url: Annotated[
        str,
        Doc(
            "Chronicle SOAR API base URL "
            "(e.g., 'https://your-instance.siemplify-soar.com/api/external/v1')"
        ),
    ],
    case_ids: Annotated[
        list[int],
        Doc("List of case IDs to close"),
    ],
    close_reason: Annotated[
        int,
        Doc(
            "Close reason enum: 0=Malicious, 1=NotMalicious, 2=Maintenance, 3=Inconclusive, 4=Unknown"
        ),
    ],
    root_cause: Annotated[
        str,
        Doc("Root cause description"),
    ],
    close_comment: Annotated[
        str,
        Doc("Comment for all closed cases"),
    ],
) -> dict[str, Any]:
    """Close multiple Chronicle SOAR cases in a single operation.

    Close reason values:
    - 0: Malicious
    - 1: NotMalicious
    - 2: Maintenance
    - 3: Inconclusive
    - 4: Unknown

    Useful for:
    - Mass closure of false positives
    - Batch maintenance operations
    - Campaign-based case resolution

    Returns the API response.
    """
    headers = _get_secops_headers()

    request_body = {
        "casesIds": case_ids,
        "closeReason": close_reason,
        "rootCause": root_cause,
        "closeComment": close_comment,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/cases-queue/bulk-operations/ExecuteBulkCloseCase",
            headers=headers,
            json=request_body,
            timeout=60.0,  # Longer timeout for bulk operations
        )
        response.raise_for_status()
        # Return empty dict if no content, otherwise return JSON
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()
