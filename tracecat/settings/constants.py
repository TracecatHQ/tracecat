from tracecat.auth.enums import AuthType

PUBLIC_SETTINGS_KEYS = {"auth_allowed_types"}
"""Settings that are allowed to be read by unauthenticated users.
Currently this is used in /info to serve config to the frontend."""

SENSITIVE_SETTINGS_KEYS = {
    "saml_idp_metadata_url",
    "audit_webhook_url",
    "audit_webhook_custom_headers",
}
"""Settings that are encrypted at rest."""

AUTH_TYPE_TO_SETTING_KEY = {
    AuthType.SAML: "saml_enabled",
}
