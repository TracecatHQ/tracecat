from tracecat.auth.enums import AuthType

PUBLIC_SETTINGS_KEYS = {"auth_allowed_types"}
"""Settings that are allowed to be read by unauthenticated users.
Currently this is used in /info to serve config to the frontend."""

SENSITIVE_SETTINGS_KEYS = {"saml_idp_metadata_url"}
"""Settings that are encrypted at rest."""

AUTH_TYPE_TO_SETTING_KEY = {
    AuthType.BASIC: "auth_basic_enabled",
    AuthType.GOOGLE_OAUTH: "oauth_google_enabled",
    AuthType.SAML: "saml_enabled",
}
