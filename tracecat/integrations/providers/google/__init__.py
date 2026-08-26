"""Google OAuth providers."""

from .admin import GoogleAdminACProvider, GoogleAdminCCProvider
from .common import (
    GoogleAuthorizationCodeOAuthProvider,
    get_google_ac_metadata,
    get_google_cc_metadata,
)
from .docs import GoogleDocsACProvider, GoogleDocsCCProvider
from .drive import GoogleDriveACProvider, GoogleDriveCCProvider
from .forms import GoogleFormsACProvider, GoogleFormsCCProvider
from .gmail import GoogleGmailACProvider, GoogleGmailCCProvider
from .service_account import GoogleServiceAccountOAuthProvider
from .sheets import GoogleSheetsACProvider, GoogleSheetsCCProvider
from .slides import GoogleSlidesACProvider, GoogleSlidesCCProvider

__all__ = [
    "GoogleAdminACProvider",
    "GoogleAdminCCProvider",
    "GoogleAuthorizationCodeOAuthProvider",
    "GoogleDocsACProvider",
    "GoogleDocsCCProvider",
    "GoogleDriveACProvider",
    "GoogleDriveCCProvider",
    "GoogleFormsACProvider",
    "GoogleFormsCCProvider",
    "GoogleGmailACProvider",
    "GoogleGmailCCProvider",
    "GoogleServiceAccountOAuthProvider",
    "GoogleSheetsACProvider",
    "GoogleSheetsCCProvider",
    "GoogleSlidesACProvider",
    "GoogleSlidesCCProvider",
    "get_google_ac_metadata",
    "get_google_cc_metadata",
]
