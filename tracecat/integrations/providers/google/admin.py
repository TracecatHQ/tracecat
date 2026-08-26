"""Google Workspace Admin service account provider.

Credential for the Admin SDK namespaces (`tools.google_directory`,
`tools.google_reports`, `tools.google_alert_center`). The Workspace app
namespaces use their own per-service providers.
"""

from typing import ClassVar

from tracecat.integrations.providers.google.common import get_google_cc_metadata
from tracecat.integrations.providers.google.service_account import (
    GoogleServiceAccountOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

GOOGLE_ADMIN_SCOPES = [
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
        "admin.reports.audit.readonly",
        "admin.reports.usage.readonly",
        "apps.alerts",
    )
]


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
        api_docs_url="https://developers.google.com/workspace/admin",
        setup_guide_url="https://developers.google.com/workspace/guides/create-credentials#optional_set_up_domain-wide_delegation_for_a_service_account",
        troubleshooting_url="https://developers.google.com/workspace/admin/directory/v1/guides/troubleshoot-error-codes",
    )
