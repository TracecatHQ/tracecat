from tracecat.auth.enums import AuthType

PUBLIC_SETTINGS_KEYS = {"auth_allowed_types"}
"""Settings that are allowed to be read by unauthenticated users.
Currently this is used in /info to serve config to the frontend."""

AWS_CREDENTIAL_SYNC_SETTING_KEY = "credential_sync_aws_config"
"""Encrypted organization setting key for AWS credential sync configuration."""

SENSITIVE_SETTINGS_KEYS = {
    "saml_idp_metadata_url",
    "audit_webhook_url",
    "audit_webhook_custom_headers",
    "audit_webhook_custom_payload",
    AWS_CREDENTIAL_SYNC_SETTING_KEY,
}
"""Settings that are encrypted at rest."""

AUTH_TYPE_TO_SETTING_KEY = {
    AuthType.SAML: "saml_enabled",
}
