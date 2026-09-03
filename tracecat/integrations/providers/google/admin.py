"""Google Workspace Admin providers.

Credential for the Admin SDK namespaces. The Directory and Reports namespaces
(`tools.google_directory`, `tools.google_reports`) accept either grant: a
Workspace administrator's user OAuth connection or the service account with
domain-wide delegation. Alert Center (`tools.google_alert_center`) requires the
service account. The Workspace app namespaces use their own per-service
providers.
"""

from typing import ClassVar

from tracecat.integrations.providers.google.common import (
    GoogleAuthorizationCodeOAuthProvider,
    get_google_ac_metadata,
    get_google_cc_metadata,
)
from tracecat.integrations.providers.google.service_account import (
    GoogleServiceAccountOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

ADMIN_API_DOCS_URL = "https://developers.google.com/workspace/admin"
ADMIN_TROUBLESHOOT_URL = "https://developers.google.com/workspace/admin/directory/v1/guides/troubleshoot-error-codes"

GOOGLE_DIRECTORY_SCOPES = [
    f"https://www.googleapis.com/auth/{scope}"
    for scope in (
        "admin.directory.user",
        "admin.directory.user.alias",
        "admin.directory.user.security",
        "admin.directory.group",
        "admin.directory.group.member",
        "admin.directory.orgunit",
        "admin.directory.rolemanagement",
        "admin.directory.device.mobile",
        "admin.directory.device.mobile.action",
        "admin.directory.device.chromeos",
        "admin.directory.domain.readonly",
        "admin.directory.customer.readonly",
    )
]

GOOGLE_REPORTS_SCOPES = [
    f"https://www.googleapis.com/auth/{scope}"
    for scope in (
        "admin.reports.audit.readonly",
        "admin.reports.usage.readonly",
    )
]

GOOGLE_ALERT_CENTER_SCOPES = [
    f"https://www.googleapis.com/auth/{scope}" for scope in ("apps.alerts",)
]

GOOGLE_ADMIN_AC_SCOPES = GOOGLE_DIRECTORY_SCOPES + GOOGLE_REPORTS_SCOPES

GOOGLE_ADMIN_SCOPES = (
    GOOGLE_DIRECTORY_SCOPES + GOOGLE_REPORTS_SCOPES + GOOGLE_ALERT_CENTER_SCOPES
)


class GoogleAdminACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Google Workspace Admin provider using the authorization code flow for an administrator."""

    id: ClassVar[str] = "google_admin"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=GOOGLE_ADMIN_AC_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_admin",
        name="Google Workspace Admin",
        description=(
            "Connect a Google Workspace administrator account with OAuth to call the "
            "Admin SDK Directory and Reports APIs as that administrator. The Alert "
            "Center API needs the service account integration instead."
        ),
        api_docs_url=ADMIN_API_DOCS_URL,
        troubleshooting_url=ADMIN_TROUBLESHOOT_URL,
    )


class GoogleAdminCCProvider(GoogleServiceAccountOAuthProvider):
    """Google Workspace Admin provider using service account credentials."""

    id: ClassVar[str] = "google_admin"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=GOOGLE_ADMIN_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_admin",
        name="Google Workspace Admin",
        description=(
            "Service account for the Google Workspace Admin SDK (Directory, "
            "Reports, Alert Center). Authenticate with a service account JSON key "
            "and a domain-wide delegation subject. The scopes configured here must "
            "match exactly the scopes delegated to this client ID in the Admin "
            "console."
        ),
        api_docs_url=ADMIN_API_DOCS_URL,
        setup_guide_url="https://developers.google.com/workspace/guides/create-credentials#optional_set_up_domain-wide_delegation_for_a_service_account",
        troubleshooting_url=ADMIN_TROUBLESHOOT_URL,
    )
