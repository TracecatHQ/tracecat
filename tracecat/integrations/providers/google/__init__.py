"""Google OAuth providers."""

from .admin import GoogleAdminCCProvider
from .common import (
    GoogleAuthorizationCodeOAuthProvider,
    get_google_ac_metadata,
    get_google_cc_metadata,
)
from .docs import GoogleDocsACProvider, GoogleDocsOAuthProvider
from .drive import GoogleDriveACProvider, GoogleDriveCCProvider
from .forms import GoogleFormsACProvider, GoogleFormsCCProvider
from .gmail import GoogleGmailACProvider, GoogleGmailCCProvider
from .service_account import GoogleServiceAccountOAuthProvider
from .sheets import GoogleSheetsACProvider, GoogleSheetsOAuthProvider
from .slides import GoogleSlidesACProvider, GoogleSlidesCCProvider

__all__ = [
    "GoogleAdminCCProvider",
    "GoogleAuthorizationCodeOAuthProvider",
    "GoogleDocsACProvider",
    "GoogleDocsOAuthProvider",
    "GoogleDriveACProvider",
    "GoogleDriveCCProvider",
    "GoogleFormsACProvider",
    "GoogleFormsCCProvider",
    "GoogleGmailACProvider",
    "GoogleGmailCCProvider",
    "GoogleServiceAccountOAuthProvider",
    "GoogleSheetsACProvider",
    "GoogleSheetsOAuthProvider",
    "GoogleSlidesACProvider",
    "GoogleSlidesCCProvider",
    "get_google_ac_metadata",
    "get_google_cc_metadata",
]
