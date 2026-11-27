from enum import StrEnum


class AuthType(StrEnum):
    BASIC = "basic"
    OIDC = "oidc"
    SAML = "saml"


class SpecialUserID(StrEnum):
    """A sentinel user ID that represents the current user."""

    CURRENT = "current"


class CredentialChangeErrorCode(StrEnum):
    """Why a self-service email or password change was refused."""

    PASSWORD_LOGIN_NOT_ALLOWED = "password_login_not_allowed"
    INVALID_CURRENT_PASSWORD = "invalid_current_password"
    INVALID_EMAIL = "invalid_email"


class AuthErrorCode(StrEnum):
    """Machine-readable codes for login failures surfaced to the sign-in UI."""

    SAML_ENFORCED = "saml_enforced"
