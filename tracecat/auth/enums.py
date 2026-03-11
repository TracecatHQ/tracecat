from enum import StrEnum


class AuthType(StrEnum):
    BASIC = "basic"
    OIDC = "oidc"
    SAML = "saml"


class SpecialUserID(StrEnum):
    """A sentinel user ID that represents the current user."""

    CURRENT = "current"
