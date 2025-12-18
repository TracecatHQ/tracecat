"""Google SecOps Detection Engine API integration UDFs for Tracecat.

This module provides Google Chronicle Detection Engine API integration for security automation,
including YARA-L rule management, detection monitoring, and retrohunt capabilities.

Uses Tracecat's native Google Service Account integration for authentication.
Configure the 'google' OAuth integration with service account credentials in Tracecat UI.
"""

from typing import Annotated, Any

import httpx
from typing_extensions import Doc

from tracecat_registry import RegistryOAuthSecret, RegistrySecret, registry, secrets

# Use Tracecat's native Google Service Account integration
google_service_account = RegistryOAuthSecret(
    provider_id="google",
    grant_type="client_credentials",
)
"""Google Service Account OAuth credentials.

- provider_id: `google`
- grant_type: `client_credentials`
- token_name: `GOOGLE_SERVICE_TOKEN`
- Required scope: `https://www.googleapis.com/auth/chronicle-backstory`
"""

# Additional secret for Chronicle instance configuration
chronicle_config = RegistrySecret(
    name="chronicle_config",
    keys=["CHRONICLE_REGION"],
    optional_keys=["CHRONICLE_INSTANCE", "CHRONICLE_PROJECT_ID"],
)
"""Chronicle configuration secret.

- name: `chronicle_config`
- keys:
    - `CHRONICLE_REGION`: Region - `us`, `eu`, or `asia`
- optional_keys:
    - `CHRONICLE_INSTANCE`: Chronicle instance ID
    - `CHRONICLE_PROJECT_ID`: GCP Project ID
"""


def _get_access_token() -> str:
    """Get access token from Tracecat's Google Service Account integration."""
    return secrets.get("GOOGLE_SERVICE_TOKEN")


def _get_chronicle_base_url() -> str:
    """Get Chronicle API base URL based on region.

    Chronicle Detection Engine API uses regional endpoints.
    Default: https://backstory.googleapis.com (US)
    EU: https://europe-backstory.googleapis.com
    Asia: https://asia-southeast1-backstory.googleapis.com
    """
    region = secrets.get("CHRONICLE_REGION")
    if region and region.lower() == "eu":
        return "https://europe-backstory.googleapis.com"
    elif region and region.lower() == "asia":
        return "https://asia-southeast1-backstory.googleapis.com"
    return "https://backstory.googleapis.com"


def _get_headers() -> dict[str, str]:
    """Get headers for Chronicle API requests."""
    token = _get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ============================================================================
# RULE MANAGEMENT
# ============================================================================


@registry.register(
    default_title="List detection rules",
    display_group="Google SecOps Detection Engine",
    description="List all detection rules in Chronicle",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def list_rules(
    page_size: Annotated[
        int,
        Doc("Maximum number of rules to return"),
    ] = 100,
    page_token: Annotated[
        str | None,
        Doc("Token for pagination"),
    ] = None,
) -> dict[str, Any]:
    """List all detection rules in Chronicle.

    Returns rules with their metadata including rule ID, name, and status.
    Use page_token for pagination through large rule sets.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    params: dict[str, Any] = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/v2/detect/rules",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get detection rule",
    display_group="Google SecOps Detection Engine",
    description="Get details of a specific detection rule",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def get_rule(
    rule_id: Annotated[
        str,
        Doc("The rule ID (e.g., 'ru_12345678-1234-1234-1234-123456789012')"),
    ],
) -> dict[str, Any]:
    """Get detailed information about a specific detection rule.

    Returns the rule definition including YARA-L text, metadata, and compilation status.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/v2/detect/rules/{rule_id}",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Create detection rule",
    display_group="Google SecOps Detection Engine",
    description="Create a new YARA-L detection rule in Chronicle",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def create_rule(
    rule_text: Annotated[
        str,
        Doc("YARA-L 2.0 rule text"),
    ],
) -> dict[str, Any]:
    """Create a new detection rule in Chronicle.

    The rule_text must be valid YARA-L 2.0 syntax.
    Use verify_rule first to validate syntax before creating.

    Returns the created rule with its assigned rule ID.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v2/detect/rules",
            headers=headers,
            json={"rule_text": rule_text},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Update detection rule",
    display_group="Google SecOps Detection Engine",
    description="Update an existing detection rule",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def update_rule(
    rule_id: Annotated[
        str,
        Doc("The rule ID to update"),
    ],
    rule_text: Annotated[
        str,
        Doc("Updated YARA-L 2.0 rule text"),
    ],
) -> dict[str, Any]:
    """Update an existing detection rule.

    Creates a new version of the rule. Previous versions are retained.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{base_url}/v2/detect/rules/{rule_id}",
            headers=headers,
            json={"rule_text": rule_text},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Delete detection rule",
    display_group="Google SecOps Detection Engine",
    description="Delete a detection rule from Chronicle",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def delete_rule(
    rule_id: Annotated[
        str,
        Doc("The rule ID to delete"),
    ],
) -> dict[str, str]:
    """Delete a detection rule.

    This permanently removes the rule and all its versions.
    Any active detections from this rule will no longer be generated.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{base_url}/v2/detect/rules/{rule_id}",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return {"status": "success", "message": f"Rule {rule_id} deleted"}


@registry.register(
    default_title="Verify detection rule",
    display_group="Google SecOps Detection Engine",
    description="Validate YARA-L rule syntax without creating it",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def verify_rule(
    rule_text: Annotated[
        str,
        Doc("YARA-L 2.0 rule text to validate"),
    ],
) -> dict[str, Any]:
    """Verify YARA-L rule syntax without creating the rule.

    Use this to validate rules before deployment.
    Returns compilation status and any syntax errors.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v2/detect/rules:verifyRule",
            headers=headers,
            json={"rule_text": rule_text},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


# ============================================================================
# RULE DEPLOYMENT
# ============================================================================


@registry.register(
    default_title="Enable detection rule",
    display_group="Google SecOps Detection Engine",
    description="Enable a detection rule for live alerting",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def enable_rule(
    rule_id: Annotated[
        str,
        Doc("The rule ID to enable"),
    ],
) -> dict[str, Any]:
    """Enable a detection rule for live alerting.

    Once enabled, the rule will generate detections for matching events.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v2/detect/rules/{rule_id}:enableLiveRule",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Disable detection rule",
    display_group="Google SecOps Detection Engine",
    description="Disable a detection rule to stop alerting",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def disable_rule(
    rule_id: Annotated[
        str,
        Doc("The rule ID to disable"),
    ],
) -> dict[str, Any]:
    """Disable a detection rule to stop live alerting.

    The rule remains in Chronicle but won't generate new detections.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v2/detect/rules/{rule_id}:disableLiveRule",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get rule deployment status",
    display_group="Google SecOps Detection Engine",
    description="Get the deployment/alerting status of a rule",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def get_rule_deployment(
    rule_id: Annotated[
        str,
        Doc("The rule ID"),
    ],
) -> dict[str, Any]:
    """Get the deployment status of a detection rule.

    Returns whether the rule is enabled for live alerting and its current state.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/v2/detect/rules/{rule_id}/deployment",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


# ============================================================================
# DETECTIONS
# ============================================================================


@registry.register(
    default_title="List detections for rule",
    display_group="Google SecOps Detection Engine",
    description="Get detections generated by a specific rule",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def list_detections(
    rule_id: Annotated[
        str,
        Doc("The rule ID to get detections for"),
    ],
    start_time: Annotated[
        str | None,
        Doc("Start time (RFC 3339 format, e.g., '2024-01-01T00:00:00Z')"),
    ] = None,
    end_time: Annotated[
        str | None,
        Doc("End time (RFC 3339 format)"),
    ] = None,
    page_size: Annotated[
        int,
        Doc("Maximum detections to return"),
    ] = 100,
    page_token: Annotated[
        str | None,
        Doc("Token for pagination"),
    ] = None,
) -> dict[str, Any]:
    """List detections generated by a specific rule.

    Returns detection events with matched entities and timestamps.
    Use time filters to scope the results.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    params: dict[str, Any] = {"page_size": page_size}
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    if page_token:
        params["page_token"] = page_token

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/v2/detect/rules/{rule_id}/detections",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


# ============================================================================
# RETROHUNTS
# ============================================================================


@registry.register(
    default_title="Create retrohunt",
    display_group="Google SecOps Detection Engine",
    description="Run a rule against historical data",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def create_retrohunt(
    rule_id: Annotated[
        str,
        Doc("The rule ID to run retrohunt for"),
    ],
    start_time: Annotated[
        str,
        Doc("Start time (RFC 3339 format, e.g., '2024-01-01T00:00:00Z')"),
    ],
    end_time: Annotated[
        str,
        Doc("End time (RFC 3339 format)"),
    ],
) -> dict[str, Any]:
    """Create a retrohunt to run a rule against historical data.

    Retrohunts scan past events to find matches for a detection rule.
    Useful for threat hunting and validating new rules.

    Returns the retrohunt job details including operation ID.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v2/detect/rules/{rule_id}/retrohunts",
            headers=headers,
            json={
                "start_time": start_time,
                "end_time": end_time,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get retrohunt status",
    display_group="Google SecOps Detection Engine",
    description="Get the status of a retrohunt operation",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def get_retrohunt(
    rule_id: Annotated[
        str,
        Doc("The rule ID"),
    ],
    retrohunt_id: Annotated[
        str,
        Doc("The retrohunt ID"),
    ],
) -> dict[str, Any]:
    """Get the status and results of a retrohunt operation.

    Returns progress, state, and detection count.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/v2/detect/rules/{rule_id}/retrohunts/{retrohunt_id}",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="List retrohunts",
    display_group="Google SecOps Detection Engine",
    description="List all retrohunts for a rule",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def list_retrohunts(
    rule_id: Annotated[
        str,
        Doc("The rule ID"),
    ],
    page_size: Annotated[
        int,
        Doc("Maximum retrohunts to return"),
    ] = 100,
    page_token: Annotated[
        str | None,
        Doc("Token for pagination"),
    ] = None,
) -> dict[str, Any]:
    """List all retrohunt operations for a specific rule.

    Returns retrohunt history with status and results.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    params: dict[str, Any] = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/v2/detect/rules/{rule_id}/retrohunts",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Cancel retrohunt",
    display_group="Google SecOps Detection Engine",
    description="Cancel a running retrohunt operation",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def cancel_retrohunt(
    rule_id: Annotated[
        str,
        Doc("The rule ID"),
    ],
    retrohunt_id: Annotated[
        str,
        Doc("The retrohunt ID to cancel"),
    ],
) -> dict[str, Any]:
    """Cancel a running retrohunt operation.

    Use this to stop long-running retrohunts that are no longer needed.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v2/detect/rules/{rule_id}/retrohunts/{retrohunt_id}:cancel",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


# ============================================================================
# ERRORS
# ============================================================================


@registry.register(
    default_title="List rule errors",
    display_group="Google SecOps Detection Engine",
    description="Get compilation or execution errors for a rule",
    namespace="tools.google_secops_detection",
    secrets=[google_service_account, chronicle_config],
)
async def list_rule_errors(
    rule_id: Annotated[
        str,
        Doc("The rule ID"),
    ],
    page_size: Annotated[
        int,
        Doc("Maximum errors to return"),
    ] = 100,
    page_token: Annotated[
        str | None,
        Doc("Token for pagination"),
    ] = None,
) -> dict[str, Any]:
    """List compilation or execution errors for a detection rule.

    Useful for debugging rules that aren't working as expected.
    """
    base_url = _get_chronicle_base_url()
    headers = _get_headers()

    params: dict[str, Any] = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/v2/detect/rules/{rule_id}/errors",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

