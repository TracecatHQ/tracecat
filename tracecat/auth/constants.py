from enum import StrEnum


class AuthType(StrEnum):
    BASIC = "basic"
    GOOGLE_OAUTH = "google_oauth"
    OIDC = "oidc"
    SAML = "saml"
