"""Google Reporting Integration for Safe Browsing and Web Risk APIs.

Integration for searching and reporting URLs to Google Safe Browsing and Web Risk.
"""

import time
from typing import Annotated, Any, Literal

import httpx
import jwt
import orjson
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

google_reporting_secret = RegistrySecret(
    name="google_reporting",
    keys=["GOOGLE_SERVICE_ACCOUNT_CREDENTIALS", "GOOGLE_API_KEY"],
)
"""Google Reporting service account credentials and API key.

- name: `google_reporting`
- keys:
    - `GOOGLE_SERVICE_ACCOUNT_CREDENTIALS` (JSON string of service account credentials)
    - `GOOGLE_API_KEY` (Google API key for Web Risk API)
"""


async def _get_oauth_token(private_key: str, client_email: str, token_uri: str) -> str:
    """Get OAuth token using JWT authentication flow."""
    private_key = private_key.replace("\\n", "\n")

    # Define JWT claims
    now = int(time.time())
    expiry = now + 3600  # 1 hour in the future
    payload = {
        "iss": client_email,  # Issuer (the service account email)
        "scope": "https://www.googleapis.com/auth/cloud-platform",  # API Scopes
        "aud": token_uri,  # Audience (the token endpoint)
        "iat": now,  # Issued at time
        "exp": expiry,  # Expiration time (1 hour from issued time)
    }

    # Create the JWT signed with RS256 using the service account's private key
    jwt_token = jwt.encode(payload, private_key, algorithm="RS256")

    # Exchange JWT for OAuth token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_uri,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt_token,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["access_token"]


@registry.register(
    default_title="Submit URI to Google Safe Browsing",
    description="Submit a suspicious URI to Google Safe Browsing for analysis and reporting.",
    display_group="Google Reporting",
    doc_url="https://cloud.google.com/web-risk/docs/reference/rest/v1/projects.uris/submit",
    namespace="tools.google_reporting",
    secrets=[google_reporting_secret],
)
async def submit_uri(
    google_project: Annotated[str, Field(..., description="Google Cloud Project ID")],
    phishing_uri: Annotated[str, Field(..., description="URI to report as suspicious")],
    threat_confidence: Annotated[
        Literal["HIGH", "MEDIUM", "LOW"],
        Field("HIGH", description="Confidence level of the threat"),
    ] = "HIGH",
    region_codes: Annotated[
        str,
        Field("US", description="Region codes where the threat was discovered"),
    ] = "US",
    comments: Annotated[
        str,
        Field(
            "Suspicious URI reported via Tracecat automation",
            description="Comments about the threat",
        ),
    ] = "Suspicious URI reported via Tracecat automation",
    abuse_type: Annotated[
        Literal["SOCIAL_ENGINEERING", "MALWARE", "UNWANTED_SOFTWARE"],
        Field("SOCIAL_ENGINEERING", description="Type of abuse"),
    ] = "SOCIAL_ENGINEERING",
    threat_justification_labels: Annotated[
        list[str],
        Field(
            ["USER_REPORT", "MANUAL_VERIFICATION", "AUTOMATED_REPORT"],
            description="Labels for the threat justification",
        ),
    ] = ["AUTOMATED_REPORT"],
    platform: Annotated[
        Literal[
            "PLATFORM_UNSPECIFIED",
            "ANDROID",
            "IOS",
            "WINDOWS",
            "MACOS",
        ],
        Field("PLATFORM_UNSPECIFIED", description="Platform of the threat"),
    ] = "PLATFORM_UNSPECIFIED",
) -> dict[str, Any]:
    """Submit a URI to Google Safe Browsing for threat analysis."""

    # Get service account credentials
    creds_json = secrets.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS")
    try:
        creds = orjson.loads(creds_json)
    except orjson.JSONDecodeError as e:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS is not a valid JSON string."
        ) from e

    # Get OAuth token
    auth_token = await _get_oauth_token(
        creds["private_key"], creds["client_email"], creds["token_uri"]
    )

    # Ensure URI has protocol
    if not phishing_uri.startswith(("http://", "https://")):
        phishing_uri = "http://" + phishing_uri

    # Format the submission URL
    submission_uri = (
        f"https://webrisk.googleapis.com/v1/projects/{google_project}/uris:submit"
    )

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {auth_token}",
        "x-goog-user-project": google_project,
    }

    body = {
        "submission": {"uri": phishing_uri},
        "threatDiscovery": {"platform": platform, "regionCodes": [region_codes]},
        "threatInfo": {
            "abuseType": abuse_type,
            "threatJustification": {
                "labels": threat_justification_labels,
                "comments": comments,
            },
            "threatConfidence": {"level": threat_confidence},
        },
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(submission_uri, headers=headers, json=body)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Search URI in Google Web Risk",
    description="Search for threats associated with a URI using Google Web Risk API.",
    display_group="Google Reporting",
    doc_url="https://cloud.google.com/web-risk/docs/reference/rest/v1/uris/search",
    namespace="tools.google_reporting",
    secrets=[google_reporting_secret],
)
async def search_uri(
    uri: Annotated[str, Field(..., description="URI to search for threats")],
    threat_types: Annotated[
        list[str],
        Field(
            ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"],
            description="Types of threats to search for",
        ),
    ] = None,
) -> dict[str, Any]:
    """Search for threats associated with a URI using Google Web Risk API."""

    if threat_types is None:
        threat_types = ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"]

    api_key = secrets.get("GOOGLE_API_KEY")
    search_url = "https://webrisk.googleapis.com/v1/uris:search"

    params = {
        "uri": uri,
        "threatTypes": threat_types,
        "key": api_key,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(search_url, params=params)

        if response.status_code == 403:
            raise ValueError("API Key is not valid or insufficient permissions")

        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get operation status",
    description="Gets the latest state of a long-running operation from Google Web Risk API.",
    display_group="Google Reporting",
    doc_url="https://cloud.google.com/web-risk/docs/reference/rest/v1/projects.operations/get",
    namespace="tools.google_reporting",
    secrets=[google_reporting_secret],
)
async def get_operation(
    operation_name: Annotated[
        str,
        Field(
            ...,
            description="The name of the operation resource. Format: projects/{project}/operations/{operation}",
        ),
    ],
) -> dict[str, Any]:
    """Gets the latest state of a long-running operation.

    This function is useful for checking the status of operations like URI submissions
    that may take time to process.
    """

    # Get service account credentials
    creds_json = secrets.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS")
    try:
        creds = orjson.loads(creds_json)
    except orjson.JSONDecodeError as e:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS is not a valid JSON string."
        ) from e

    # Get OAuth token
    auth_token = await _get_oauth_token(
        creds["private_key"], creds["client_email"], creds["token_uri"]
    )

    # Construct the operation URL
    operation_url = f"https://webrisk.googleapis.com/v1/{operation_name}"

    headers = {
        "Authorization": f"Bearer {auth_token}",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(operation_url, headers=headers)
        response.raise_for_status()
        return response.json()
