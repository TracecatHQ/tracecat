"""Scanner.dev API integrations for queries, detections, event sinks, and indexes."""

from typing import Annotated, Any, Literal

import httpx
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

scanner_secret = RegistrySecret(
    name="scanner", keys=["SCANNER_API_KEY", "SCANNER_BASE_URL"]
)
"""Scanner API key.

- name: `scanner`
- keys:
    - `SCANNER_API_KEY`
    - `SCANNER_BASE_URL`
"""

DetectionState = Literal["Active", "Staging", "Paused"]
DetectionSeverity = Literal[
    "Unknown",
    "Information",
    "Informational",
    "Low",
    "Medium",
    "High",
    "Critical",
    "Fatal",
    "Other",
]


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop omitted optional fields while preserving falsey values."""
    return {key: value for key, value in payload.items() if value is not None}


def _resolve_base_url() -> str:
    resolved = secrets.get("SCANNER_BASE_URL").strip()

    if "://" not in resolved:
        resolved = f"https://{resolved}"

    return resolved.rstrip("/")


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    content: str | bytes | None = None,
    content_type: str = "application/json",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Make an authenticated request to the Scanner API."""
    api_key = secrets.get("SCANNER_API_KEY")
    base = _resolve_base_url()
    url = f"{base}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": content_type,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=payload,
            content=content,
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {"status": "success", "status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            return {"status_code": response.status_code, "text": response.text}


# =============================================================================
# Ad hoc queries
# =============================================================================


@registry.register(
    default_title="Run Scanner query",
    description="Execute a blocking ad hoc query against Scanner and return the results.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/ad-hoc-queries",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def run_query(
    query: Annotated[str, Doc("Scanner query text to execute.")],
    start_time: Annotated[
        str,
        Doc("Inclusive query start timestamp in RFC 3339 format."),
    ],
    end_time: Annotated[
        str,
        Doc("Exclusive query end timestamp in RFC 3339 format."),
    ],
    max_rows: Annotated[
        int | None,
        Doc(
            "Maximum number of rows to return. Scanner defaults to 1,000 and allows up to 100,000."
        ),
    ] = None,
    max_bytes: Annotated[
        int | None,
        Doc(
            "Maximum bytes to allocate for the result table. Scanner defaults to 134,217,728 bytes."
        ),
    ] = None,
    scan_back_to_front: Annotated[
        bool | None,
        Doc(
            "Scan from latest events toward earliest events. Scanner defaults to true."
        ),
    ] = None,
    timeout_seconds: Annotated[
        int,
        Doc(
            "HTTP timeout in seconds. Scanner may hold blocking queries open for up to 300 seconds."
        ),
    ] = 310,
) -> dict[str, Any]:
    payload = _clean_payload(
        {
            "query": query,
            "start_time": start_time,
            "end_time": end_time,
            "max_rows": max_rows,
            "max_bytes": max_bytes,
            "scan_back_to_front": scan_back_to_front,
        }
    )
    return await _request(
        "POST",
        "/v1/blocking_query",
        payload=payload,
        timeout=float(timeout_seconds),
    )


@registry.register(
    default_title="Start Scanner query",
    description="Start an asynchronous Scanner ad hoc query and return its query run ID.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/ad-hoc-queries",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def start_query(
    query: Annotated[str, Doc("Scanner query text to execute.")],
    start_time: Annotated[
        str, Doc("Inclusive query start timestamp in RFC 3339 format.")
    ],
    end_time: Annotated[str, Doc("Exclusive query end timestamp in RFC 3339 format.")],
    max_rows: Annotated[int | None, Doc("Maximum number of rows to return.")] = None,
    max_bytes: Annotated[
        int | None, Doc("Maximum bytes to allocate for the result table.")
    ] = None,
    scan_back_to_front: Annotated[
        bool | None,
        Doc(
            "Scan from latest events toward earliest events. Scanner defaults to true."
        ),
    ] = None,
) -> dict[str, Any]:
    payload = _clean_payload(
        {
            "query": query,
            "start_time": start_time,
            "end_time": end_time,
            "max_rows": max_rows,
            "max_bytes": max_bytes,
            "scan_back_to_front": scan_back_to_front,
        }
    )
    return await _request("POST", "/v1/start_query", payload=payload)


@registry.register(
    default_title="Get Scanner query progress",
    description="Poll a Scanner asynchronous ad hoc query for progress and results.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/ad-hoc-queries",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def get_query_progress(
    query_run_id: Annotated[
        str, Doc("Query run ID returned by start_query (`qr_id`).")
    ],
    show_intermediate_results: Annotated[
        bool,
        Doc("Whether to return intermediate results while the query is still running."),
    ] = True,
) -> dict[str, Any]:
    return await _request(
        "GET",
        f"/v1/query_progress/{query_run_id}",
        params={"show_intermediate_results": show_intermediate_results},
    )


@registry.register(
    default_title="Cancel Scanner query",
    description="Cancel a Scanner asynchronous ad hoc query.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/ad-hoc-queries",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def cancel_query(
    query_run_id: Annotated[
        str, Doc("Query run ID returned by start_query (`qr_id`).")
    ],
) -> dict[str, Any]:
    return await _request("POST", f"/v1/cancel_query/{query_run_id}")


# =============================================================================
# Indexes
# =============================================================================


@registry.register(
    default_title="List Scanner indexes",
    description="List searchable Scanner indexes for a tenant.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/indexes",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def list_indexes(
    tenant_id: Annotated[str, Doc("Unique identifier for the Scanner tenant.")],
    page_size: Annotated[
        int | None, Doc("Maximum number of indexes to return in a page.")
    ] = None,
    page_token: Annotated[
        str | None, Doc("Pagination cursor from a previous response.")
    ] = None,
) -> dict[str, Any]:
    params = _clean_payload(
        {
            "tenant_id": tenant_id,
            "pagination[page_size]": page_size,
            "pagination[page_token]": page_token,
        }
    )
    return await _request("GET", "/v1/index", params=params)


@registry.register(
    default_title="Get Scanner index",
    description="Get a Scanner index by ID.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/indexes",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def get_index(
    index_id: Annotated[str, Doc("Unique Scanner index ID.")],
) -> dict[str, Any]:
    return await _request("GET", f"/v1/index/{index_id}")


# =============================================================================
# Detection rules
# =============================================================================


@registry.register(
    default_title="List Scanner detection rules",
    description="List Scanner detection rules for a tenant.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/detection-rules",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def list_detection_rules(
    tenant_id: Annotated[str, Doc("Unique identifier for the Scanner tenant.")],
    page_size: Annotated[
        int | None, Doc("Maximum number of rules to return in a page.")
    ] = None,
    page_token: Annotated[
        str | None, Doc("Pagination cursor from a previous response.")
    ] = None,
) -> dict[str, Any]:
    params = _clean_payload(
        {
            "tenant_id": tenant_id,
            "pagination[page_size]": page_size,
            "pagination[page_token]": page_token,
        }
    )
    return await _request("GET", "/v1/detection_rule", params=params)


@registry.register(
    default_title="Get Scanner detection rule",
    description="Get a Scanner detection rule by ID.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/detection-rules",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def get_detection_rule(
    detection_rule_id: Annotated[str, Doc("Unique Scanner detection rule ID.")],
) -> dict[str, Any]:
    return await _request("GET", f"/v1/detection_rule/{detection_rule_id}")


@registry.register(
    default_title="Create Scanner detection rule",
    description="Create a Scanner detection rule.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/detection-rules",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def create_detection_rule(
    tenant_id: Annotated[str, Doc("Unique identifier for the Scanner tenant.")],
    name: Annotated[str, Doc("Name of the detection rule.")],
    description: Annotated[str, Doc("Description of the detection rule.")],
    time_range_s: Annotated[
        int,
        Doc("Lookback period in seconds. Must use minute granularity."),
    ],
    run_frequency_s: Annotated[
        int,
        Doc(
            "How often the rule runs in seconds. Must use minute granularity and be <= time_range_s."
        ),
    ],
    enabled_state_override: Annotated[
        DetectionState,
        Doc("Whether the rule runs and sends events to event sinks."),
    ],
    severity: Annotated[DetectionSeverity, Doc("Detection severity.")],
    query_text: Annotated[str, Doc("Scanner query text for the detection rule.")],
    event_sink_ids: Annotated[
        list[str], Doc("Event sink IDs to send detection alerts to.")
    ],
    alert_per_row: Annotated[
        bool | None,
        Doc(
            "Emit one alert per result row instead of one alert containing the result table."
        ),
    ] = None,
    tags: Annotated[
        list[str] | None, Doc("Tags to associate with the detection rule.")
    ] = None,
    sync_key: Annotated[
        str | None, Doc("Sync key used by automatic detection rule syncers.")
    ] = None,
    alert_template: Annotated[
        dict[str, Any] | None,
        Doc("Custom alert formatting template with `info` and `actions` arrays."),
    ] = None,
) -> dict[str, Any]:
    payload = _clean_payload(
        {
            "tenant_id": tenant_id,
            "name": name,
            "description": description,
            "time_range_s": time_range_s,
            "run_frequency_s": run_frequency_s,
            "enabled_state_override": enabled_state_override,
            "severity": severity,
            "query_text": query_text,
            "event_sink_ids": event_sink_ids,
            "alert_per_row": alert_per_row,
            "tags": tags,
            "sync_key": sync_key,
            "alert_template": alert_template,
        }
    )
    return await _request("POST", "/v1/detection_rule", payload=payload)


@registry.register(
    default_title="Update Scanner detection rule",
    description="Update a Scanner detection rule with the supplied fields.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/detection-rules",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def update_detection_rule(
    detection_rule_id: Annotated[str, Doc("Unique Scanner detection rule ID.")],
    updates: Annotated[
        dict[str, Any],
        Doc("Detection rule fields to update. The rule ID is added automatically."),
    ],
) -> dict[str, Any]:
    payload = {"id": detection_rule_id, **updates}
    return await _request(
        "PUT", f"/v1/detection_rule/{detection_rule_id}", payload=payload
    )


@registry.register(
    default_title="Delete Scanner detection rule",
    description="Delete a Scanner detection rule by ID.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/detection-rules",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def delete_detection_rule(
    detection_rule_id: Annotated[str, Doc("Unique Scanner detection rule ID.")],
) -> dict[str, Any]:
    return await _request("DELETE", f"/v1/detection_rule/{detection_rule_id}")


# =============================================================================
# Event sinks
# =============================================================================


@registry.register(
    default_title="List Scanner event sinks",
    description="List Scanner event sinks for a tenant.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/event-sinks",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def list_event_sinks(
    tenant_id: Annotated[str, Doc("Unique identifier for the Scanner tenant.")],
    page_size: Annotated[
        int | None, Doc("Maximum number of event sinks to return in a page.")
    ] = None,
    page_token: Annotated[
        str | None, Doc("Pagination cursor from a previous response.")
    ] = None,
) -> dict[str, Any]:
    params = _clean_payload(
        {
            "tenant_id": tenant_id,
            "pagination[page_size]": page_size,
            "pagination[page_token]": page_token,
        }
    )
    return await _request("GET", "/v1/event_sink", params=params)


@registry.register(
    default_title="Get Scanner event sink",
    description="Get a Scanner event sink by ID.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/event-sinks",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def get_event_sink(
    event_sink_id: Annotated[str, Doc("Unique Scanner event sink ID.")],
) -> dict[str, Any]:
    return await _request("GET", f"/v1/event_sink/{event_sink_id}")


@registry.register(
    default_title="Create Scanner event sink",
    description="Create a Scanner event sink for Slack, webhook, or PagerDuty destinations.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/event-sinks",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def create_event_sink(
    tenant_id: Annotated[str, Doc("Unique identifier for the Scanner tenant.")],
    name: Annotated[str, Doc("Name of the event sink.")],
    description: Annotated[str, Doc("Description of the event sink.")],
    event_sink_args: Annotated[
        dict[str, Any],
        Doc("Event sink details, e.g. {'Webhook': {'url': 'https://...'}}."),
    ],
) -> dict[str, Any]:
    payload = {
        "tenant_id": tenant_id,
        "name": name,
        "description": description,
        "event_sink_args": event_sink_args,
    }
    return await _request("POST", "/v1/event_sink", payload=payload)


@registry.register(
    default_title="Update Scanner event sink",
    description="Update a Scanner event sink with the supplied fields.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/event-sinks",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def update_event_sink(
    event_sink_id: Annotated[str, Doc("Unique Scanner event sink ID.")],
    updates: Annotated[
        dict[str, Any],
        Doc("Event sink fields to update. The event sink ID is added automatically."),
    ],
) -> dict[str, Any]:
    payload = {"id": event_sink_id, **updates}
    return await _request("PUT", f"/v1/event_sink/{event_sink_id}", payload=payload)


@registry.register(
    default_title="Delete Scanner event sink",
    description="Delete a Scanner event sink by ID.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/event-sinks",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def delete_event_sink(
    event_sink_id: Annotated[str, Doc("Unique Scanner event sink ID.")],
) -> dict[str, Any]:
    return await _request("DELETE", f"/v1/event_sink/{event_sink_id}")


# =============================================================================
# Detection rule YAML validation
# =============================================================================


@registry.register(
    default_title="Validate Scanner detection YAML",
    description="Validate a Scanner detection rule YAML file.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/validating-yaml-files",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def validate_detection_rule_yaml(
    yaml_text: Annotated[str, Doc("Detection rule YAML content to validate.")],
) -> dict[str, Any]:
    return await _request(
        "POST",
        "/v1/detection_rule_yaml/validate",
        content=yaml_text,
        content_type="application/x-yaml",
    )


@registry.register(
    default_title="Run Scanner detection YAML tests",
    description="Run tests declared in a Scanner detection rule YAML file.",
    display_group="Scanner",
    doc_url="https://docs.scanner.dev/scanner/using-scanner-complete-feature-reference/developer-tools/api/validating-yaml-files",
    namespace="tools.scanner",
    secrets=[scanner_secret],
)
async def run_detection_rule_yaml_tests(
    yaml_text: Annotated[
        str, Doc("Detection rule YAML content whose tests should be run.")
    ],
) -> dict[str, Any]:
    return await _request(
        "POST",
        "/v1/detection_rule_yaml/run_tests",
        content=yaml_text,
        content_type="application/x-yaml",
    )
